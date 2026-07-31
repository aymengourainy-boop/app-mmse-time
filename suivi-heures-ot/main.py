"""
API FastAPI — c'est le pont entre la page HTML et la base de données.

Le HTML n'accède JAMAIS directement à la base de données : il envoie des
requêtes HTTP (via fetch en JavaScript) à cette API, qui elle seule lit et
écrit dans la base via SQLAlchemy (models.py / database.py).

Lancer le serveur :
    uvicorn main:app --reload

Puis ouvrir : http://127.0.0.1:8000
"""

from datetime import datetime, timedelta, date as date_type, time as time_type
from decimal import Decimal
import io
import os
import csv
import json
import logging
import secrets
import smtplib
import base64
import urllib.parse
import urllib.request
from email.message import EmailMessage

from fastapi import Depends, FastAPI, HTTPException, status, Header, UploadFile, File, Form, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

import models
import schemas
from auth import creer_token, decoder_token, verifier_mot_de_passe, hasher_mot_de_passe
from database import SessionLocal, engine, ensure_schema, get_db, APP_DATA_DIR
from pathlib import Path

# Crée les tables si elles n'existent pas encore (ne touche jamais aux données existantes)
ensure_schema()
UPLOADS_DIR = APP_DATA_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Suivi des heures et OT")
logger = logging.getLogger(__name__)

# CORS : autorise le HTML à appeler l'API même s'il n'est pas servi
# exactement depuis la même origine (utile en développement).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Authentification : récupérer l'utilisateur courant à partir du token JWT
# --------------------------------------------------------------------------- #

def get_utilisateur_courant(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> models.Utilisateur:
    erreur = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentification requise ou invalide",
    )
    if not authorization or not authorization.startswith("Bearer "):
        raise erreur
    token = authorization.removeprefix("Bearer ").strip()
    payload = decoder_token(token)
    if not payload:
        raise erreur
    sujet = payload.get("sub")
    if not sujet:
        raise erreur
    try:
        utilisateur_id = int(sujet)
    except (TypeError, ValueError):
        raise erreur
    utilisateur = db.get(models.Utilisateur, utilisateur_id)
    if not utilisateur or not utilisateur.actif:
        raise erreur
    return utilisateur


# --------------------------------------------------------------------------- #
# Calcul automatique des heures (règle simple par défaut : seuil 8h/jour)
# --------------------------------------------------------------------------- #

def calculer_heures(heure_debut, heure_fin, seuil_normal: Decimal = Decimal("9.00"), est_jour_ferie: bool = False, est_jour_de_repos: bool = False):
    debut = datetime.combine(date_type.today(), heure_debut)
    fin = datetime.combine(date_type.today(), heure_fin)
    if fin < debut:
        fin += timedelta(days=1)  # gère les shifts de nuit qui passent minuit
    total_heures = Decimal(str(round((fin - debut).total_seconds() / 3600, 2)))
    if est_jour_ferie or est_jour_de_repos:
        return total_heures, Decimal("0.00"), total_heures
    heures_normales = min(total_heures, seuil_normal)
    heures_supplementaires = max(Decimal("0.00"), total_heures - seuil_normal)
    return total_heures, heures_normales, heures_supplementaires


def est_weekend(date_demande: date_type) -> bool:
    return date_demande.weekday() >= 5


def peut_saisir_weekend(utilisateur: models.Utilisateur) -> bool:
    return utilisateur.role in {
        models.RoleUtilisateur.TECHNICIEN_SHIFT,
        models.RoleUtilisateur.SUPERVISEUR_SHIFT,
        models.RoleUtilisateur.ADMINISTRATEUR,
    } or utilisateur.autorise_weekend


def peut_saisir_jours_passes(utilisateur: models.Utilisateur) -> bool:
    return bool(utilisateur.autorise_jours_passes)


def payload_contient_jours_passes(
    normales_par_jour: dict | None,
    supplementaires_par_jour: dict | None,
    conges_par_jour: dict | None,
    reference: date_type,
) -> bool:
    clefs = set()
    if normales_par_jour:
        clefs.update(normales_par_jour.keys())
    if supplementaires_par_jour:
        clefs.update(supplementaires_par_jour.keys())
    if conges_par_jour:
        clefs.update(conges_par_jour.keys())

    for cle in clefs:
        if not jour_a_du_contenu(normales_par_jour, supplementaires_par_jour, conges_par_jour, cle):
            continue
        date_jour = date_depuis_cle(cle, reference)
        if date_jour and date_jour < date_type.today():
            return True
    return False


def verifier_date_demande_technicien_shift(date_demande: date_type, utilisateur: models.Utilisateur):
    if utilisateur.role == models.RoleUtilisateur.TECHNICIEN_SHIFT:
        if date_demande != date_type.today():
            raise HTTPException(
                status_code=400,
                detail="Un technicien shift ne peut saisir que la date du jour",
            )


def verifier_demande_modifiable(demande: models.Demande, utilisateur: models.Utilisateur):
    if utilisateur.role == models.RoleUtilisateur.TECHNICIEN_SHIFT:
        if demande.date_demande < date_type.today() and not demande.autorise_modification_retro:
            raise HTTPException(
                status_code=403,
                detail="Cette demande ne peut plus être modifiée après la fin de la journée",
            )
    return True


def nettoyer_telephone(numero: str | None) -> str | None:
    if not numero:
        return None
    return "".join(ch for ch in str(numero) if ch.isdigit() or ch == "+")


def envoyer_sms(numero: str | None, message: str) -> bool:
    numero = nettoyer_telephone(numero)
    if not numero:
        return False

    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_FROM_NUMBER")
    if not account_sid or not auth_token or not from_number:
        logger.warning("SMS non envoyé: configuration Twilio absente pour %s", numero)
        return False

    data = urllib.parse.urlencode({
        "To": numero,
        "From": from_number,
        "Body": message,
    }).encode("utf-8")
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    request = urllib.request.Request(url, data=data, method="POST")
    credentials = f"{account_sid}:{auth_token}".encode("utf-8")
    basic_auth = base64.b64encode(credentials).decode("ascii")
    request.add_header("Authorization", f"Basic {basic_auth}")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return 200 <= response.status < 300
    except Exception as exc:
        logger.warning("Échec de l'envoi SMS vers %s: %s", numero, exc)
        return False


def creer_notification(db: Session, utilisateur_id: int, demande_id: int | None, type_notification: models.TypeNotification, message: str):
    db.add(models.Notification(
        utilisateur_id=utilisateur_id,
        demande_id=demande_id,
        type=type_notification,
        message=message,
    ))


def notifier_utilisateur(db: Session, utilisateur: models.Utilisateur | None, demande_id: int | None, type_notification: models.TypeNotification, message: str):
    if not utilisateur:
        return
    creer_notification(db, utilisateur.id, demande_id, type_notification, message)
    numero = nettoyer_telephone(utilisateur.numero_telephone)
    if numero:
        envoyer_sms(numero, message)


def envoyer_email_reset_password(adresse: str, code: str) -> bool:
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    from_email = os.environ.get("SMTP_FROM_EMAIL", smtp_username or "")
    if not smtp_username or not smtp_password or not from_email:
        logger.warning("Email de réinitialisation non envoyé: configuration SMTP absente pour %s", adresse)
        return False

    message = EmailMessage()
    message["Subject"] = "Réinitialisation de votre mot de passe"
    message["From"] = from_email
    message["To"] = adresse
    message.set_content(
        "Votre code de réinitialisation Time MMSE est : {code}\n\n"
        "Ce code expire dans 15 minutes. Si vous n'êtes pas à l'origine de cette demande, ignorez ce message.".format(code=code)
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(message)
        return True
    except Exception as exc:
        logger.warning("Échec de l'envoi email vers %s: %s", adresse, exc)
        return False


def trouver_regle_ot(db: Session, departement_id: int | None, date_demande: date_type):
    if departement_id is not None:
        regle_dept = (
            db.query(models.RegleHeuresSupplementaires)
            .filter(models.RegleHeuresSupplementaires.actif.is_(True))
            .filter(models.RegleHeuresSupplementaires.departement_id == departement_id)
            .filter(models.RegleHeuresSupplementaires.date_debut_validite <= date_demande)
            .filter(
                (models.RegleHeuresSupplementaires.date_fin_validite == None)
                | (models.RegleHeuresSupplementaires.date_fin_validite >= date_demande)
            )
            .order_by(models.RegleHeuresSupplementaires.date_debut_validite.desc())
            .first()
        )
        if regle_dept:
            return regle_dept

    return (
        db.query(models.RegleHeuresSupplementaires)
        .filter(models.RegleHeuresSupplementaires.actif.is_(True))
        .filter(models.RegleHeuresSupplementaires.departement_id == None)
        .filter(models.RegleHeuresSupplementaires.date_debut_validite <= date_demande)
        .filter(
            (models.RegleHeuresSupplementaires.date_fin_validite == None)
            | (models.RegleHeuresSupplementaires.date_fin_validite >= date_demande)
        )
        .order_by(models.RegleHeuresSupplementaires.date_debut_validite.desc())
        .first()
    )


def normaliser_heures_par_jour(valeurs: dict | None) -> dict | None:
    if not valeurs:
        return None
    result = {}
    for jour, valeur in valeurs.items():
        if valeur is None:
            result[jour] = None
            continue
        try:
            result[jour] = float(valeur)
        except (TypeError, ValueError):
            continue
    return result


def normaliser_conges_par_jour(valeurs: dict | None) -> dict | None:
    if not valeurs:
        return None
    result = {}
    for jour, valeur in valeurs.items():
        if valeur is None:
            result[jour] = None
            continue
        if isinstance(valeur, bool):
            result[jour] = valeur
        else:
            result[jour] = str(valeur).lower() in {"1", "true", "oui", "yes", "y"}
    return result


def jour_a_du_contenu(
    normales_par_jour: dict | None,
    supplementaires_par_jour: dict | None,
    conges_par_jour: dict | None,
    cle: str,
) -> bool:
    normal_val = normales_par_jour.get(cle) if normales_par_jour else None
    supp_val = supplementaires_par_jour.get(cle) if supplementaires_par_jour else None
    conge_val = conges_par_jour.get(cle) if conges_par_jour else None

    if normal_val is not None:
        try:
            if Decimal(str(normal_val)) != Decimal("0"):
                return True
        except Exception:
            return True
    if supp_val is not None:
        try:
            if Decimal(str(supp_val)) != Decimal("0"):
                return True
        except Exception:
            return True
    if conge_val:
        return True
    return False


def parse_json_field(valeur: str | None):
    if not valeur:
        return None
    try:
        return json.loads(valeur)
    except ValueError:
        return None


def date_depuis_cle(cle: str, reference: date_type | None = None) -> date_type | None:
    if not cle:
        return None
    try:
        return date_type.fromisoformat(cle)
    except ValueError:
        pass
    if reference is None:
        reference = date_type.today()
    jours_fr = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    cle_norm = cle.lower().strip()
    if cle_norm in jours_fr:
        lundi = reference - timedelta(days=reference.weekday())
        return lundi + timedelta(days=jours_fr.index(cle_norm))
    return None


def trouver_jour_ferie(db: Session, date_demande: date_type) -> models.JourFerie | None:
    if date_demande is None:
        return None
    return (
        db.query(models.JourFerie)
        .filter(models.JourFerie.date == date_demande)
        .filter(models.JourFerie.actif.is_(True))
        .first()
    )


def calculer_heures_depuis_breakdown(
    heures_normales_par_jour: dict | None,
    heures_supplementaires_par_jour: dict | None,
    conges_par_jour: dict | None,
    db: Session,
    utilisateur: models.Utilisateur,
    reference: date_type,
):
    normales = Decimal("0.00")
    supplementaires = Decimal("0.00")

    if not heures_normales_par_jour and not heures_supplementaires_par_jour:
        return None

    regle = trouver_regle_ot(db, utilisateur.departement_id, reference)
    seuil_normal_jour = regle.seuil_heures_normales_jour if regle else Decimal("9.00")
    seuil_normal_semaine = regle.seuil_heures_normales_semaine if regle else Decimal("44.00")

    clefs = set()
    if heures_normales_par_jour:
        clefs.update(heures_normales_par_jour.keys())
    if heures_supplementaires_par_jour:
        clefs.update(heures_supplementaires_par_jour.keys())

    week_totals = []
    for cle in sorted(clefs):
        date_jour = date_depuis_cle(cle, reference)
        if date_jour is None:
            continue
        if conges_par_jour and conges_par_jour.get(cle):
            continue

        normal_val = heures_normales_par_jour.get(cle) if heures_normales_par_jour else None
        supp_val = heures_supplementaires_par_jour.get(cle) if heures_supplementaires_par_jour else None
        total = Decimal("0.00")
        if normal_val is not None:
            try:
                total += Decimal(str(normal_val))
            except Exception:
                pass
        if supp_val is not None:
            try:
                total += Decimal(str(supp_val))
            except Exception:
                pass
        if total == Decimal("0.00"):
            continue

        jour_ferie = trouver_jour_ferie(db, date_jour)
        date_repos = est_weekend(date_jour)
        if jour_ferie:
            if utilisateur.role in {models.RoleUtilisateur.TECHNICIEN, models.RoleUtilisateur.SUPERVISEUR} and not jour_ferie.comptabilise_pour_techniciens_normaux:
                continue
            week_totals.append((date_jour, Decimal("0.00"), total))
        elif date_repos:
            week_totals.append((date_jour, Decimal("0.00"), total))
        else:
            normales_jour = min(total, seuil_normal_jour)
            supplementaires_jour = max(Decimal("0.00"), total - seuil_normal_jour)
            week_totals.append((date_jour, normales_jour, supplementaires_jour))

    for _, normales_jour, supplementaires_jour in week_totals:
        normales += normales_jour
        supplementaires += supplementaires_jour

    if normales == Decimal("0.00") and supplementaires == Decimal("0.00"):
        return None

    if normales > seuil_normal_semaine:
        excedent = normales - seuil_normal_semaine
        normales -= excedent
        supplementaires += excedent

    return (
        Decimal(str(round(normales + supplementaires, 2))),
        Decimal(str(round(normales, 2))),
        Decimal(str(round(supplementaires, 2))),
    )


# --------------------------------------------------------------------------- #
# Authentification
# --------------------------------------------------------------------------- #

@app.post("/api/auth/login", response_model=schemas.TokenReponse)
def login(requete: schemas.LoginRequete, db: Session = Depends(get_db)):
    utilisateur = (
        db.query(models.Utilisateur)
        .filter(models.Utilisateur.matricule == requete.matricule)
        .first()
    )
    if not utilisateur or not verifier_mot_de_passe(requete.mot_de_passe, utilisateur.mot_de_passe_hash):
        raise HTTPException(status_code=401, detail="Matricule ou mot de passe incorrect")
    if not utilisateur.actif:
        raise HTTPException(status_code=403, detail="Compte désactivé")

    token = creer_token({"sub": str(utilisateur.id), "role": utilisateur.role.value})
    return schemas.TokenReponse(
        access_token=token,
        role=utilisateur.role.value,
        nom=utilisateur.nom,
        prenom=utilisateur.prenom,
        utilisateur_id=utilisateur.id,
        autorise_weekend=bool(utilisateur.autorise_weekend),
        autorise_jours_passes=bool(utilisateur.autorise_jours_passes),
    )


@app.post("/api/auth/mot-de-passe-oublie")
def mot_de_passe_oublie(
    requete: schemas.MotDePasseOublieRequete,
    db: Session = Depends(get_db),
):
    email = requete.email.strip().lower()
    utilisateur = db.query(models.Utilisateur).filter(models.Utilisateur.email == email).first()
    if not utilisateur:
        return {"message": "Si ce compte existe, un code de réinitialisation a été envoyé."}

    code = f"{secrets.randbelow(900000) + 100000}"
    expiration = datetime.utcnow() + timedelta(minutes=15)
    db.query(models.MotDePasseOublie).filter(models.MotDePasseOublie.utilisateur_id == utilisateur.id).delete()
    db.add(models.MotDePasseOublie(
        utilisateur_id=utilisateur.id,
        code_hash=hasher_mot_de_passe(code),
        expire_le=expiration,
    ))
    db.commit()
    envoyer_email_reset_password(utilisateur.email, code)
    return {"message": "Si ce compte existe, un code de réinitialisation a été envoyé."}


@app.post("/api/auth/mot-de-passe-reinitialiser")
def mot_de_passe_reinitialiser(
    requete: schemas.MotDePasseReinitialisationRequete,
    db: Session = Depends(get_db),
):
    email = requete.email.strip().lower()
    utilisateur = db.query(models.Utilisateur).filter(models.Utilisateur.email == email).first()
    if not utilisateur:
        raise HTTPException(status_code=404, detail="Compte introuvable")

    reset = (
        db.query(models.MotDePasseOublie)
        .filter(models.MotDePasseOublie.utilisateur_id == utilisateur.id)
        .order_by(models.MotDePasseOublie.cree_le.desc())
        .first()
    )
    if not reset or reset.utilise_le is not None or reset.expire_le < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Code invalide ou expiré")
    if not verifier_mot_de_passe(requete.code.strip(), reset.code_hash):
        raise HTTPException(status_code=400, detail="Code invalide ou expiré")

    utilisateur.mot_de_passe_hash = hasher_mot_de_passe(requete.nouveau_mot_de_passe)
    reset.utilise_le = datetime.utcnow()
    db.commit()
    return {"message": "Mot de passe réinitialisé avec succès."}


@app.get("/api/me")
def me(utilisateur: models.Utilisateur = Depends(get_utilisateur_courant)):
    return {
        "id": utilisateur.id,
        "nom": utilisateur.nom,
        "prenom": utilisateur.prenom,
        "matricule": utilisateur.matricule,
        "role": utilisateur.role.value,
        "autorise_weekend": bool(utilisateur.autorise_weekend),
        "autorise_jours_passes": bool(utilisateur.autorise_jours_passes),
        "numero_telephone": utilisateur.numero_telephone,
    }


@app.get("/api/utilisateurs")
def lister_utilisateurs_pour_admin(
    utilisateur: models.Utilisateur = Depends(get_utilisateur_courant),
    db: Session = Depends(get_db),
):
    if utilisateur.role != models.RoleUtilisateur.ADMINISTRATEUR:
        raise HTTPException(status_code=403, detail="Accès réservé aux administrateurs")
    return [
        {
            "id": u.id,
            "nom": u.nom,
            "prenom": u.prenom,
            "matricule": u.matricule,
            "role": u.role.value,
            "autorise_weekend": bool(u.autorise_weekend),
            "autorise_jours_passes": bool(u.autorise_jours_passes),
            "numero_telephone": u.numero_telephone,
        }
        for u in db.query(models.Utilisateur)
        .filter(models.Utilisateur.actif.is_(True))
        .order_by(models.Utilisateur.nom, models.Utilisateur.prenom)
        .all()
    ]


@app.put("/api/utilisateurs/{utilisateur_id}/autorise_weekend")
def toggle_autorise_weekend(
    utilisateur_id: int,
    payload: schemas.AutoriseWeekendRequete | None = Body(default=None),
    utilisateur: models.Utilisateur = Depends(get_utilisateur_courant),
    db: Session = Depends(get_db),
):
    if utilisateur.role != models.RoleUtilisateur.ADMINISTRATEUR:
        raise HTTPException(status_code=403, detail="Accès réservé aux administrateurs")
    cible = db.get(models.Utilisateur, utilisateur_id)
    if not cible:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    if payload is None:
        cible.autorise_weekend = not cible.autorise_weekend
    else:
        cible.autorise_weekend = payload.autorise_weekend
    db.commit()
    db.refresh(cible)
    return {"id": cible.id, "autorise_weekend": bool(cible.autorise_weekend)}


@app.put("/api/utilisateurs/{utilisateur_id}/autorise_jours_passes")
def toggle_autorise_jours_passes(
    utilisateur_id: int,
    payload: schemas.AutoriseJoursPassesRequete | None = Body(default=None),
    utilisateur: models.Utilisateur = Depends(get_utilisateur_courant),
    db: Session = Depends(get_db),
):
    if utilisateur.role != models.RoleUtilisateur.ADMINISTRATEUR:
        raise HTTPException(status_code=403, detail="Accès réservé aux administrateurs")
    cible = db.get(models.Utilisateur, utilisateur_id)
    if not cible:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    if payload is None:
        cible.autorise_jours_passes = not cible.autorise_jours_passes
    else:
        cible.autorise_jours_passes = payload.autorise_jours_passes
    db.commit()
    db.refresh(cible)
    return {"id": cible.id, "autorise_jours_passes": bool(cible.autorise_jours_passes)}


@app.put("/api/utilisateurs/{utilisateur_id}/telephone")
def modifier_telephone_utilisateur(
    utilisateur_id: int,
    payload: schemas.TelephoneUtilisateurRequete,
    utilisateur: models.Utilisateur = Depends(get_utilisateur_courant),
    db: Session = Depends(get_db),
):
    if utilisateur.role != models.RoleUtilisateur.ADMINISTRATEUR:
        raise HTTPException(status_code=403, detail="Accès réservé aux administrateurs")
    cible = db.get(models.Utilisateur, utilisateur_id)
    if not cible:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    cible.numero_telephone = nettoyer_telephone(payload.numero_telephone)
    db.commit()
    db.refresh(cible)
    return {"id": cible.id, "numero_telephone": cible.numero_telephone}


@app.get("/api/jours_feries", response_model=list[schemas.JourFerieReponse])
def lister_jours_feries(
    utilisateur: models.Utilisateur = Depends(get_utilisateur_courant),
    db: Session = Depends(get_db),
):
    return db.query(models.JourFerie).order_by(models.JourFerie.date).all()


@app.post("/api/jours_feries", response_model=schemas.JourFerieReponse)
def creer_jour_ferie(
    jour: schemas.JourFerieCreation,
    utilisateur: models.Utilisateur = Depends(get_utilisateur_courant),
    db: Session = Depends(get_db),
):
    if utilisateur.role != models.RoleUtilisateur.ADMINISTRATEUR:
        raise HTTPException(status_code=403, detail="Accès réservé aux administrateurs")
    existing = db.query(models.JourFerie).filter(models.JourFerie.date == jour.date).first()
    if existing:
        raise HTTPException(status_code=400, detail="Un jour férié existe déjà à cette date")
    nouveau = models.JourFerie(
        date=jour.date,
        nom=jour.nom,
        est_musulman=jour.est_musulman,
        actif=jour.actif,
        comptabilise_pour_techniciens_normaux=jour.comptabilise_pour_techniciens_normaux,
        description=jour.description,
    )
    db.add(nouveau)
    db.commit()
    db.refresh(nouveau)
    return nouveau


@app.put("/api/jours_feries/{jour_ferie_id}", response_model=schemas.JourFerieReponse)
def modifier_jour_ferie(
    jour_ferie_id: int,
    jour: schemas.JourFerieCreation,
    utilisateur: models.Utilisateur = Depends(get_utilisateur_courant),
    db: Session = Depends(get_db),
):
    if utilisateur.role != models.RoleUtilisateur.ADMINISTRATEUR:
        raise HTTPException(status_code=403, detail="Accès réservé aux administrateurs")
    entree = db.get(models.JourFerie, jour_ferie_id)
    if not entree:
        raise HTTPException(status_code=404, detail="Jour férié introuvable")
    entree.date = jour.date
    entree.nom = jour.nom
    entree.est_musulman = jour.est_musulman
    entree.actif = jour.actif
    entree.comptabilise_pour_techniciens_normaux = jour.comptabilise_pour_techniciens_normaux
    entree.description = jour.description
    db.commit()
    db.refresh(entree)
    return entree


# --------------------------------------------------------------------------- #
# Demandes
# --------------------------------------------------------------------------- #

@app.post("/api/demandes", response_model=schemas.DemandeReponse)
async def creer_demande(
    equipement: str | None = Form(None),
    ordre_travail_sap: str | None = Form(None),
    type_intervention: str | None = Form(None),
    description_travaux: str | None = Form(None),
    justification_ot: str | None = Form(None),
    commentaires: str | None = Form(None),
    soumettre: bool = Form(False),
    date_demande_str: str | None = Form(None),
    heures_normales_par_jour: str | None = Form(None),
    heures_supplementaires_par_jour: str | None = Form(None),
    conges_par_jour: str | None = Form(None),
    fichiers: list[UploadFile] | None = File(None),
    utilisateur: models.Utilisateur = Depends(get_utilisateur_courant),
    db: Session = Depends(get_db),
):
    # Autorise la création pour les techniciens et superviseurs (normaux et shift)
    allowed_roles = {
        models.RoleUtilisateur.TECHNICIEN,
        models.RoleUtilisateur.TECHNICIEN_SHIFT,
        models.RoleUtilisateur.SUPERVISEUR,
        models.RoleUtilisateur.SUPERVISEUR_SHIFT,
    }
    if utilisateur.role not in allowed_roles:
        raise HTTPException(status_code=403, detail="Accès refusé : rôle non autorisé à créer une demande")
    if soumettre:
        raise HTTPException(
            status_code=400,
            detail="Sauvegardez d'abord la demande en brouillon avant de l'envoyer au superviseur",
        )

    # Pour les techniciens (non-supervisors) on exige qu'un superviseur soit associé
    if utilisateur.role in {models.RoleUtilisateur.TECHNICIEN, models.RoleUtilisateur.TECHNICIEN_SHIFT} and not utilisateur.superviseur_id:
        raise HTTPException(status_code=400, detail="Aucun superviseur n'est associé à ce technicien")

    date_demande = date_type.today()
    if date_demande_str:
        try:
            date_demande = date_type.fromisoformat(date_demande_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="Date de demande invalide")

    normales_dict = normaliser_heures_par_jour(parse_json_field(heures_normales_par_jour))
    supps_dict = normaliser_heures_par_jour(parse_json_field(heures_supplementaires_par_jour))
    conges_dict = normaliser_conges_par_jour(parse_json_field(conges_par_jour))

    def trouver_date_dans_demande(cle: str) -> date_type | None:
        return date_depuis_cle(cle, date_demande)

    jours_nommes = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    jour_courant = jours_nommes[date_demande.weekday()]

    demande_existante = (
        db.query(models.Demande)
        .filter(models.Demande.technicien_id == utilisateur.id)
        .filter(models.Demande.date_demande == date_demande)
        .filter(models.Demande.est_archive.is_(False))
        .first()
    )
    if demande_existante:
        raise HTTPException(
            status_code=400,
            detail="Une seule demande est autorisée par jour. Ouvrez la demande existante pour la modifier.",
        )

    if utilisateur.role in {models.RoleUtilisateur.TECHNICIEN, models.RoleUtilisateur.SUPERVISEUR}:
        if not peut_saisir_weekend(utilisateur):
            for cle in set((normales_dict or {}).keys()) | set((supps_dict or {}).keys()) | set((conges_dict or {}).keys()):
                if not jour_a_du_contenu(normales_dict, supps_dict, conges_dict, cle):
                    continue
                date_jour = trouver_date_dans_demande(cle)
                if date_jour and est_weekend(date_jour):
                    raise HTTPException(status_code=403, detail="Saisie le week-end non autorisée pour votre rôle")

    if utilisateur.role == models.RoleUtilisateur.TECHNICIEN_SHIFT:
        if payload_contient_jours_passes(normales_dict, supps_dict, conges_dict, date_demande) and not peut_saisir_jours_passes(utilisateur):
            raise HTTPException(status_code=403, detail="La saisie des jours passés nécessite l'autorisation d'un administrateur")

    if utilisateur.role == models.RoleUtilisateur.TECHNICIEN_SHIFT:
        if jour_a_du_contenu(normales_dict, supps_dict, conges_dict, jour_courant):
            if date_demande != date_type.today():
                raise HTTPException(status_code=403, detail="Les techniciens Shift ne peuvent saisir que la date du jour")

    if utilisateur.role == models.RoleUtilisateur.TECHNICIEN:
        if jour_a_du_contenu(normales_dict, supps_dict, conges_dict, jour_courant):
            if date_demande != date_type.today():
                raise HTTPException(status_code=403, detail="Les techniciens ne peuvent saisir que la date du jour")
            if est_weekend(date_demande) and not peut_saisir_weekend(utilisateur):
                raise HTTPException(status_code=403, detail="Saisie le week-end non autorisée pour votre rôle")

    regle = trouver_regle_ot(db, utilisateur.departement_id, date_demande)

    # Calcule le total / normales / supp à partir du breakdown envoyé
    breakdown = calculer_heures_depuis_breakdown(normales_dict, supps_dict, conges_dict, db, utilisateur, date_demande)
    if breakdown is None:
        total, normales, supp = Decimal("0.00"), Decimal("0.00"), Decimal("0.00")
    else:
        total, normales, supp = breakdown

    # Stocke la justification globale si fournie.
    justification_stockee = justification_ot if justification_ot else None

    demande = models.Demande(
        reference=f"DEM-{datetime.now():%Y%m%d%H%M%S}-{utilisateur.id}",
        technicien_id=utilisateur.id,
        superviseur_id=utilisateur.superviseur_id,
        departement_id=utilisateur.departement_id,
        equipe_id=utilisateur.equipe_id,
        date_demande=date_demande,
        heure_debut=time_type(0,0),
        heure_fin=time_type(0,0),
        heures_travaillees=total,
        heures_normales=normales,
        heures_supplementaires=supp,
        heures_normales_par_jour=normales_dict,
        heures_supplementaires_par_jour=supps_dict,
        conges_par_jour=conges_dict,
        regle_ot_appliquee_id=regle.id if regle else None,
        equipement=equipement,
        ordre_travail_sap=ordre_travail_sap,
        type_intervention=type_intervention,
        description_travaux=description_travaux,
        justification_ot=justification_stockee,
        commentaires=commentaires,
        statut=models.StatutDemande.EN_ATTENTE if soumettre else models.StatutDemande.BROUILLON,
        envoyee_le=datetime.utcnow() if soumettre else None,
    )
    db.add(demande)
    db.commit()
    db.refresh(demande)

    if fichiers:
        uploads_dir = UPLOADS_DIR
        for fichier in fichiers:
            original_name = os.path.basename(fichier.filename)
            safe_name = f"{datetime.utcnow():%Y%m%d%H%M%S}_{original_name}"
            chemin_stockage = f"uploads/{safe_name}"
            destination = uploads_dir / safe_name
            contenu = await fichier.read()
            destination.write_bytes(contenu)
            db.add(models.PieceJointe(
                demande_id=demande.id,
                type=models.TypePieceJointe.DOCUMENT,
                nom_fichier=original_name,
                chemin_stockage=chemin_stockage,
                taille_octets=len(contenu),
                type_mime=fichier.content_type,
                televerse_par_id=utilisateur.id,
            ))
        db.commit()

    db.add(models.HistoriqueDemande(
        demande_id=demande.id,
        version=1,
        type_action=models.TypeActionHistorique.SOUMISSION if soumettre else models.TypeActionHistorique.CREATION,
        instantane_donnees={"statut": demande.statut.value, "heures_travaillees": str(total)},
        modifie_par_id=utilisateur.id,
    ))
    if soumettre:
        notifier_utilisateur(
            db,
            db.get(models.Utilisateur, utilisateur.superviseur_id),
            demande.id,
            models.TypeNotification.NOUVELLE_DEMANDE,
            f"Nouvelle demande de {utilisateur.prenom} {utilisateur.nom} pour le {demande.date_demande}",
        )
    db.commit()
    return demande


@app.post("/api/demandes/{demande_id}/pieces_jointes")
async def ajouter_pieces_jointes(
    demande_id: int,
    fichiers: list[UploadFile] | None = File(None),
    utilisateur: models.Utilisateur = Depends(get_utilisateur_courant),
    db: Session = Depends(get_db),
):
    demande = db.get(models.Demande, demande_id)
    if not demande or demande.technicien_id != utilisateur.id:
        raise HTTPException(status_code=404, detail="Demande introuvable")

    if not fichiers:
        return {"detail": "Aucun fichier envoyé"}

    uploads_dir = UPLOADS_DIR
    for fichier in fichiers:
        original_name = os.path.basename(fichier.filename)
        safe_name = f"{datetime.utcnow():%Y%m%d%H%M%S}_{original_name}"
        chemin_stockage = f"uploads/{safe_name}"
        destination = uploads_dir / safe_name
        contenu = await fichier.read()
        destination.write_bytes(contenu)
        db.add(models.PieceJointe(
            demande_id=demande.id,
            type=models.TypePieceJointe.DOCUMENT,
            nom_fichier=original_name,
            chemin_stockage=chemin_stockage,
            taille_octets=len(contenu),
            type_mime=fichier.content_type,
            televerse_par_id=utilisateur.id,
        ))
    db.commit()
    return {"detail": "Fichiers enregistrés"}


@app.get("/api/demandes", response_model=list[schemas.DemandeReponse])
def lister_demandes(
    statut: str | None = None,
    archived: bool = False,
    utilisateur: models.Utilisateur = Depends(get_utilisateur_courant),
    db: Session = Depends(get_db),
):
    requete = db.query(models.Demande)
    if utilisateur.role in {models.RoleUtilisateur.TECHNICIEN, models.RoleUtilisateur.TECHNICIEN_SHIFT}:
        requete = requete.filter(models.Demande.technicien_id == utilisateur.id)
    elif utilisateur.role in {models.RoleUtilisateur.SUPERVISEUR, models.RoleUtilisateur.SUPERVISEUR_SHIFT}:
        requete = requete.filter(models.Demande.superviseur_id == utilisateur.id)
    # l'administrateur voit tout, pas de filtre

    # Filtre sur archivé / non archivé (par défaut on montre seulement non-archivé)
    if archived:
        requete = requete.filter(models.Demande.est_archive.is_(True))
    else:
        requete = requete.filter(models.Demande.est_archive.is_(False))

    if statut:
        try:
            requete = requete.filter(models.Demande.statut == models.StatutDemande(statut))
        except ValueError:
            raise HTTPException(status_code=400, detail="Statut invalide")

    return requete.order_by(models.Demande.date_demande.desc()).all()


@app.get("/api/demandes/export")
def exporter_demandes(
    statut: str | None = None,
    utilisateur: models.Utilisateur = Depends(get_utilisateur_courant),
    db: Session = Depends(get_db),
):
    if utilisateur.role != models.RoleUtilisateur.ADMINISTRATEUR:
        raise HTTPException(status_code=403, detail="Accès réservé aux administrateurs")

    requete = db.query(models.Demande)
    if statut:
        try:
            requete = requete.filter(models.Demande.statut == models.StatutDemande(statut))
        except ValueError:
            raise HTTPException(status_code=400, detail="Statut de filtre invalide")

    sorties = io.StringIO(newline="")
    writer = csv.writer(sorties, delimiter=';')
    writer.writerow([
        "Reference",
        "Technicien",
        "Superviseur",
        "Date demande",
        "Equipement",
        "OT SAP",
        "Type intervention",
        "Statut",
        "Heures travaillees",
        "Heures normales",
        "Heures supplementaires",
        "Regle OT appliquee",
    ])
    for demande in db.query(models.Demande).order_by(models.Demande.date_demande.desc()).all():
        writer.writerow([
            demande.reference,
            f"{demande.technicien.prenom} {demande.technicien.nom}" if demande.technicien else "",
            f"{demande.superviseur.prenom} {demande.superviseur.nom}" if demande.superviseur else "",
            demande.date_demande,
            demande.equipement or "",
            demande.ordre_travail_sap or "",
            demande.type_intervention.value if demande.type_intervention else "",
            demande.statut.value,
            str(demande.heures_travaillees),
            str(demande.heures_normales),
            str(demande.heures_supplementaires),
            str(demande.regle_ot_appliquee_id or ""),
        ])
    contenus = sorties.getvalue().encode("utf-8")
    return StreamingResponse(
        io.BytesIO(contenus),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=export_demandes.csv"},
    )




@app.delete("/api/demandes/{demande_id}")
def supprimer_demande(
    demande_id: int,
    utilisateur: models.Utilisateur = Depends(get_utilisateur_courant),
    db: Session = Depends(get_db),
):
    """Marque une demande brouillon comme archivée (soft delete). Seul le technicien
    propriétaire peut archiver sa demande s'il est en brouillon. L'administrateur
    peut aussi archiver n'importe quelle demande.
    """
    demande = db.get(models.Demande, demande_id)
    if not demande:
        raise HTTPException(status_code=404, detail="Demande introuvable")

    # Technicien owners can only archive their own brouillons
    if utilisateur.role == models.RoleUtilisateur.TECHNICIEN:
        if demande.technicien_id != utilisateur.id:
            raise HTTPException(status_code=403, detail="Accès refusé")
        if demande.statut != models.StatutDemande.BROUILLON:
            raise HTTPException(status_code=400, detail="Seules les demandes en brouillon peuvent être supprimées")
    else:
        # Administrateurs peuvent archiver n'importe quelle demande
        if utilisateur.role != models.RoleUtilisateur.ADMINISTRATEUR:
            raise HTTPException(status_code=403, detail="Accès réservé")

    demande.est_archive = True
    db.commit()
    return {"detail": "Demande archivée"}


@app.put("/api/demandes/{demande_id}/restore", response_model=schemas.DemandeReponse)
def restaurer_demande(
    demande_id: int,
    utilisateur: models.Utilisateur = Depends(get_utilisateur_courant),
    db: Session = Depends(get_db),
):
    if utilisateur.role != models.RoleUtilisateur.ADMINISTRATEUR:
        raise HTTPException(status_code=403, detail="Accès réservé aux administrateurs")
    demande = db.get(models.Demande, demande_id)
    if not demande:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    if not demande.est_archive:
        raise HTTPException(status_code=400, detail="La demande n'est pas archivée")
    demande.est_archive = False
    db.commit()
    db.refresh(demande)
    return demande


@app.put("/api/demandes/{demande_id}/allow_retro", response_model=schemas.DemandeReponse)
def autoriser_modification_retro(
    demande_id: int,
    utilisateur: models.Utilisateur = Depends(get_utilisateur_courant),
    db: Session = Depends(get_db),
):
    """Endpoint réservé aux administrateurs pour autoriser la modification rétroactive d'une demande."""
    if utilisateur.role != models.RoleUtilisateur.ADMINISTRATEUR:
        raise HTTPException(status_code=403, detail="Accès réservé aux administrateurs")
    demande = db.get(models.Demande, demande_id)
    if not demande:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    demande.autorise_modification_retro = True
    db.commit()
    db.refresh(demande)
    return demande


@app.put("/api/demandes/{demande_id}", response_model=schemas.DemandeReponse)
def modifier_demande(
    demande_id: int,
    donnees: schemas.DemandeCreation,
    utilisateur: models.Utilisateur = Depends(get_utilisateur_courant),
    db: Session = Depends(get_db),
):
    # Autorise les techniciens normaux et shift à modifier leurs demandes
    if utilisateur.role not in {models.RoleUtilisateur.TECHNICIEN, models.RoleUtilisateur.TECHNICIEN_SHIFT}:
        raise HTTPException(status_code=403, detail="Seul un technicien peut modifier une demande")

    demande = db.get(models.Demande, demande_id)
    if not demande or demande.technicien_id != utilisateur.id:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    if demande.statut not in {models.StatutDemande.BROUILLON, models.StatutDemande.RETOUR_MODIFICATION}:
        raise HTTPException(status_code=400, detail="Cette demande ne peut être modifiée que si elle est en brouillon ou en retour de supervision")

    date_demande = donnees.date_demande or demande.date_demande

    conges_candidate = normaliser_conges_par_jour(donnees.conges_par_jour)
    normales_candidate = normaliser_heures_par_jour(donnees.heures_normales_par_jour)
    supps_candidate = normaliser_heures_par_jour(donnees.heures_supplementaires_par_jour)
    jours_nommes = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    jour_courant = jours_nommes[date_demande.weekday()]

    if utilisateur.role in {models.RoleUtilisateur.TECHNICIEN, models.RoleUtilisateur.SUPERVISEUR}:
        if not utilisateur.autorise_weekend:
            for cle in set((normales_candidate or {}).keys()) | set((supps_candidate or {}).keys()) | set((conges_candidate or {}).keys()):
                if not jour_a_du_contenu(normales_candidate, supps_candidate, conges_candidate, cle):
                    continue
                date_jour = date_depuis_cle(cle, date_demande)
                if date_jour and est_weekend(date_jour):
                    raise HTTPException(status_code=403, detail="Saisie le week-end non autorisée pour votre rôle")

    if utilisateur.role == models.RoleUtilisateur.TECHNICIEN_SHIFT:
        if payload_contient_jours_passes(normales_candidate, supps_candidate, conges_candidate, date_demande) and not peut_saisir_jours_passes(utilisateur):
            raise HTTPException(status_code=403, detail="La modification des jours passés nécessite l'autorisation d'un administrateur")

    if utilisateur.role == models.RoleUtilisateur.TECHNICIEN_SHIFT:
        if demande.date_demande < date_type.today() and not demande.autorise_modification_retro:
            raise HTTPException(status_code=403, detail="Modification d'une journée passée non autorisée. Contactez un administrateur.")

    if utilisateur.role == models.RoleUtilisateur.TECHNICIEN:
        if jour_a_du_contenu(normales_candidate, supps_candidate, conges_candidate, jour_courant):
            if demande.date_demande != date_type.today():
                raise HTTPException(status_code=403, detail="Les techniciens ne peuvent saisir que la date du jour")
            if est_weekend(demande.date_demande) and not utilisateur.autorise_weekend:
                raise HTTPException(status_code=403, detail="Saisie le week-end non autorisée pour votre rôle")

    regle = trouver_regle_ot(db, utilisateur.departement_id, date_demande)
    heures_normales_dict = normales_candidate
    heures_supplementaires_dict = supps_candidate
    breakdown = calculer_heures_depuis_breakdown(
        heures_normales_dict,
        heures_supplementaires_dict,
        conges_candidate,
        db,
        utilisateur,
        date_demande,
    )
    if breakdown is None:
        if donnees.heure_debut is not None and donnees.heure_fin is not None:
            seuil_normal = regle.seuil_heures_normales_jour if regle else Decimal("9.00")
            total, normales, supp = calculer_heures(donnees.heure_debut, donnees.heure_fin, seuil_normal)
        else:
            total, normales, supp = demande.heures_travaillees, demande.heures_normales, demande.heures_supplementaires
    else:
        total, normales, supp = breakdown

    demande.date_demande = date_demande
    demande.heure_debut = donnees.heure_debut or demande.heure_debut
    demande.heure_fin = donnees.heure_fin or demande.heure_fin
    demande.heures_travaillees = total
    demande.heures_normales = normales
    demande.heures_supplementaires = supp
    demande.heures_normales_par_jour = heures_normales_dict
    demande.heures_supplementaires_par_jour = heures_supplementaires_dict
    demande.conges_par_jour = normaliser_conges_par_jour(donnees.conges_par_jour)
    demande.equipement = donnees.equipement
    demande.ordre_travail_sap = donnees.ordre_travail_sap
    demande.type_intervention = donnees.type_intervention
    demande.description_travaux = donnees.description_travaux
    demande.justification_ot = donnees.justification_ot
    demande.commentaires = donnees.commentaires
    if donnees.soumettre:
        demande.statut = models.StatutDemande.EN_ATTENTE
    elif demande.statut == models.StatutDemande.RETOUR_MODIFICATION:
        demande.statut = models.StatutDemande.RETOUR_MODIFICATION
    else:
        demande.statut = models.StatutDemande.BROUILLON
    demande.envoyee_le = datetime.utcnow() if donnees.soumettre else None
    demande.traitee_le = None

    db.add(models.HistoriqueDemande(
        demande_id=demande.id,
        version=1 if not demande.historique else max(item.version for item in demande.historique) + 1,
        type_action=models.TypeActionHistorique.SOUMISSION if donnees.soumettre else models.TypeActionHistorique.MODIFICATION,
        instantane_donnees={
            "statut": demande.statut.value,
            "heures_travaillees": str(total),
            "heures_normales": str(normales),
            "heures_supplementaires": str(supp),
        },
        modifie_par_id=utilisateur.id,
    ))
    if donnees.soumettre:
        notifier_utilisateur(
            db,
            db.get(models.Utilisateur, demande.superviseur_id),
            demande.id,
            models.TypeNotification.NOUVELLE_DEMANDE,
            f"Modification de demande de {utilisateur.prenom} {utilisateur.nom} pour le {demande.date_demande}",
        )
    db.commit()
    db.refresh(demande)
    return demande


@app.put("/api/demandes/{demande_id}/valider", response_model=schemas.DemandeReponse)
def valider_demande(
    demande_id: int,
    requete: schemas.ValidationRequete,
    utilisateur: models.Utilisateur = Depends(get_utilisateur_courant),
    db: Session = Depends(get_db),
):
    if utilisateur.role != models.RoleUtilisateur.SUPERVISEUR and utilisateur.role != models.RoleUtilisateur.SUPERVISEUR_SHIFT:
        raise HTTPException(status_code=403, detail="Seul un superviseur peut valider une demande")

    demande = db.get(models.Demande, demande_id)
    if not demande or demande.superviseur_id != utilisateur.id:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    if demande.statut != models.StatutDemande.EN_ATTENTE:
        raise HTTPException(status_code=400, detail="Cette demande n'est plus en attente")
    if not requete.commentaire or not requete.commentaire.strip():
        raise HTTPException(status_code=400, detail="Un commentaire est requis pour confirmer une demande")

    action_map = {
        "approuver": (models.StatutDemande.APPROUVEE, models.ActionValidation.APPROUVER, models.TypeNotification.DEMANDE_APPROUVEE),
        "rejeter": (models.StatutDemande.REJETEE, models.ActionValidation.REJETER, models.TypeNotification.DEMANDE_REJETEE),
        "retourner": (models.StatutDemande.RETOUR_MODIFICATION, models.ActionValidation.RETOURNER, models.TypeNotification.RETOUR_MODIFICATION),
    }
    if requete.action not in action_map:
        raise HTTPException(status_code=400, detail="Action invalide (approuver / rejeter / retourner)")

    nouveau_statut, action_enum, type_notif = action_map[requete.action]

    if requete.heures_travaillees_modifiees is not None:
        demande.heures_travaillees = requete.heures_travaillees_modifiees
    if requete.heures_supplementaires_modifiees is not None:
        demande.heures_supplementaires = requete.heures_supplementaires_modifiees

    demande.statut = nouveau_statut
    demande.traitee_le = datetime.utcnow()

    db.add(models.Validation(
        demande_id=demande.id,
        validateur_id=utilisateur.id,
        action=action_enum,
        commentaire=requete.commentaire,
        heures_travaillees_modifiees=requete.heures_travaillees_modifiees,
        heures_supplementaires_modifiees=requete.heures_supplementaires_modifiees,
    ))
    notifier_utilisateur(
        db,
        db.get(models.Utilisateur, demande.technicien_id),
        demande.id,
        type_notif,
        f"Votre demande du {demande.date_demande} a été mise à jour : {nouveau_statut.value}",
    )
    db.commit()
    db.refresh(demande)
    return demande


# --------------------------------------------------------------------------- #
# Sert le fichier HTML du frontend (un seul serveur, un seul port)
# --------------------------------------------------------------------------- #

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/uploads/{filename}", include_in_schema=False)
def servir_upload(filename: str):
    fichier = UPLOADS_DIR / filename
    if not fichier.exists():
        raise HTTPException(status_code=404, detail="Fichier introuvable")
    return FileResponse(fichier)


@app.get("/", include_in_schema=False)
@app.get("/{full_path:path}", include_in_schema=False)
def servir_frontend(full_path: str = ""):
    return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    reload_mode = os.environ.get("RELOAD", "0") == "1" and not os.environ.get("WEBSITE_SITE_NAME")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload_mode)
