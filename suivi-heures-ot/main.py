"""
API FastAPI â€” c'est le pont entre la page HTML et la base de donnÃ©es.

Le HTML n'accÃ¨de JAMAIS directement Ã  la base de donnÃ©es : il envoie des
requÃªtes HTTP (via fetch en JavaScript) Ã  cette API, qui elle seule lit et
Ã©crit dans la base via SQLAlchemy (models.py / database.py).

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
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, status, Header, UploadFile, File, Form, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

import models
import schemas
from auth import creer_token, decoder_token, verifier_mot_de_passe, hasher_mot_de_passe
from database import SessionLocal, engine, ensure_schema, get_db, APP_DATA_DIR
from pathlib import Path

# CrÃ©e les tables si elles n'existent pas encore (ne touche jamais aux donnÃ©es existantes)
ensure_schema()

# INITIALISATION DE LA BASE DE DONNEES
db = SessionLocal()
try:
    user_count = db.query(models.Utilisateur).count()
    force_reset = os.getenv("FORCE_RESET_DB", "false").lower() == "true"
    if user_count == 0 or user_count < 5 or force_reset:
        print("[INIT] Reinitialisation de la base...")
        models.Base.metadata.drop_all(bind=engine)
        models.Base.metadata.create_all(bind=engine)
        
        dept = models.Departement(code="MAINT", nom="Maintenance")
        db.add(dept)
        db.flush()
        
        equipe = models.Equipe(nom="Equipe A", shift="Jour", departement_id=dept.id)
        db.add(equipe)
        db.flush()
        
        admin = models.Utilisateur(matricule="ADMIN001", nom="Admin", prenom="Systeme",
            email="admin@exemple.com", numero_telephone="0600000001",
            mot_de_passe_hash=hasher_mot_de_passe("admin123"),
            role=models.RoleUtilisateur.ADMINISTRATEUR, departement_id=dept.id, actif=True)
        sup = models.Utilisateur(matricule="SUP001", nom="Bernard", prenom="Marie",
            email="marie.bernard@exemple.com", numero_telephone="0600000002",
            mot_de_passe_hash=hasher_mot_de_passe("sup123"),
            role=models.RoleUtilisateur.SUPERVISEUR, departement_id=dept.id, equipe_id=equipe.id, actif=True)
        sup_shift = models.Utilisateur(matricule="SUPS001", nom="Shift", prenom="Sarah",
            email="sarah.shift@exemple.com", numero_telephone="0600000005",
            mot_de_passe_hash=hasher_mot_de_passe("sups123"),
            role=models.RoleUtilisateur.SUPERVISEUR_SHIFT, departement_id=dept.id, equipe_id=equipe.id, actif=True)
        db.add_all([admin, sup, sup_shift])
        db.flush()
        
        tech = models.Utilisateur(matricule="TECH001", nom="Dupont", prenom="Jean",
            email="jean.dupont@exemple.com", numero_telephone="0600000003",
            mot_de_passe_hash=hasher_mot_de_passe("tech123"),
            role=models.RoleUtilisateur.TECHNICIEN, departement_id=dept.id, equipe_id=equipe.id, superviseur_id=sup.id, actif=True)
        tech_shift = models.Utilisateur(matricule="TECHS001", nom="Shift", prenom="Hamid",
            email="hamid.shift@exemple.com", numero_telephone="0600000004",
            mot_de_passe_hash=hasher_mot_de_passe("shift123"),
            role=models.RoleUtilisateur.TECHNICIEN_SHIFT, departement_id=dept.id, equipe_id=equipe.id, superviseur_id=sup.id, actif=True)
        db.add_all([tech, tech_shift])
        
        reg = models.RegleHeuresSupplementaires(departement_id=dept.id, nom="Regle par defaut",
            seuil_heures_normales_jour=8, seuil_heures_normales_semaine=40, taux_majoration_ot=1.5)
        db.add(reg)
        db.commit()
        print("[INIT] OK - Comptes: ADMIN001, SUP001, SUPS001, TECH001, TECHS001")
except Exception as e:
    db.rollback()
    print(f"[INIT ERREUR] {e}")
finally:
    db.close()
UPLOADS_DIR = APP_DATA_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
MAROC_TZ = ZoneInfo("Africa/Casablanca")

app = FastAPI(title="Suivi des heures et OT")
logger = logging.getLogger(__name__)

# CORS : autorise le HTML Ã  appeler l'API mÃªme s'il n'est pas servi
# exactement depuis la mÃªme origine (utile en dÃ©veloppement).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Initialiser la base de donnees au demarrage
@app.on_event("startup")
async def startup_event():
    """Initialise la base de donnees si necessaire au demarrage de l'application."""
    db = SessionLocal()
    try:
        user_count = db.query(models.Utilisateur).count()
        if user_count >= 5:
            logger.info(f"[DB] Base de donnees valide: {user_count} utilisateurs trouves")
            return
        
        logger.warning(f"[DB] Base de donnees incomplete ({user_count} utilisateurs), reinitialisation...")
        
        # Supprimer et recreer les tables
        models.Base.metadata.drop_all(bind=engine)
        models.Base.metadata.create_all(bind=engine)
        
        # Donnees de base
        dept = models.Departement(code="MAINT", nom="Maintenance")
        db.add(dept)
        db.flush()
        
        equipe = models.Equipe(nom="Equipe A", shift="Jour", departement_id=dept.id)
        db.add(equipe)
        db.flush()
        
        # Creer les 5 comptes
        admin = models.Utilisateur(
            matricule="ADMIN001",
            nom="Admin",
            prenom="Systeme",
            email="admin@exemple.com",
            numero_telephone="0600000001",
            mot_de_passe_hash=hasher_mot_de_passe("admin123"),
            role=models.RoleUtilisateur.ADMINISTRATEUR,
            departement_id=dept.id,
            actif=True,
        )
        superviseur = models.Utilisateur(
            matricule="SUP001",
            nom="Bernard",
            prenom="Marie",
            email="marie.bernard@exemple.com",
            numero_telephone="0600000002",
            mot_de_passe_hash=hasher_mot_de_passe("sup123"),
            role=models.RoleUtilisateur.SUPERVISEUR,
            departement_id=dept.id,
            equipe_id=equipe.id,
            actif=True,
        )
        superviseur_shift = models.Utilisateur(
            matricule="SUPS001",
            nom="Shift",
            prenom="Sarah",
            email="sarah.shift@exemple.com",
            numero_telephone="0600000005",
            mot_de_passe_hash=hasher_mot_de_passe("sups123"),
            role=models.RoleUtilisateur.SUPERVISEUR_SHIFT,
            departement_id=dept.id,
            equipe_id=equipe.id,
            actif=True,
        )
        db.add_all([admin, superviseur, superviseur_shift])
        db.flush()
        
        technicien = models.Utilisateur(
            matricule="TECH001",
            nom="Dupont",
            prenom="Jean",
            email="jean.dupont@exemple.com",
            numero_telephone="0600000003",
            mot_de_passe_hash=hasher_mot_de_passe("tech123"),
            role=models.RoleUtilisateur.TECHNICIEN,
            departement_id=dept.id,
            equipe_id=equipe.id,
            superviseur_id=superviseur.id,
            actif=True,
        )
        technicien_shift = models.Utilisateur(
            matricule="TECHS001",
            nom="Shift",
            prenom="Hamid",
            email="hamid.shift@exemple.com",
            numero_telephone="0600000004",
            mot_de_passe_hash=hasher_mot_de_passe("shift123"),
            role=models.RoleUtilisateur.TECHNICIEN_SHIFT,
            departement_id=dept.id,
            equipe_id=equipe.id,
            superviseur_id=superviseur.id,
            actif=True,
        )
        db.add_all([technicien, technicien_shift])
        
        reg = models.RegleHeuresSupplementaires(
            departement_id=dept.id,
            nom="Regle par defaut - Maintenance",
            seuil_heures_normales_jour=8,
            seuil_heures_normales_semaine=40,
            taux_majoration_ot=1.5,
        )
        db.add(reg)
        
        db.commit()
        logger.info("[DB] Base de donnees reinitialisee avec succes!")
        logger.info("Comptes crees: ADMIN001, SUP001, SUPS001, TECH001, TECHS001")
        
    except Exception as e:
        db.rollback()
        logger.error(f"[DB] Erreur lors de l'initialisation: {e}", exc_info=True)
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# GÃ©olocalisation basÃ©e sur l'IP
# --------------------------------------------------------------------------- #

def obtenir_ip_client(request: Request) -> str:
    """RÃ©cupÃ¨re l'IP rÃ©elle du client (supporte les proxies)"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client_host = request.client.host if request.client else "127.0.0.1"
    return client_host


def geolocaliser_ip(ip: str) -> dict[str, str] | None:
    """Utilise ip-api.com (API gratuite) pour gÃ©olocaliser une IP"""
    if ip.startswith("127.") or ip.startswith("192.168.") or ip.startswith("10."):
        return None  # Skip local IPs
    try:
        url = f"http://ip-api.com/json/{ip}?fields=country,city,status"
        with urllib.request.urlopen(url, timeout=2) as response:
            data = json.loads(response.read().decode())
            if data.get("status") == "success":
                return {
                    "ville": data.get("city", ""),
                    "pays": data.get("country", ""),
                    "ip": ip
                }
    except Exception as e:
        logger.warning(f"Erreur gÃ©olocalisation IP {ip}: {e}")
    return {"ip": ip, "ville": "", "pays": ""}


def serialiser_export_json(valeur):
    def compacter(val):
        if isinstance(val, dict):
            resultat = {}
            for cle, sous_valeur in val.items():
                valeur_compactee = compacter(sous_valeur)
                if valeur_compactee in (None, "", [], {}):
                    continue
                resultat[cle] = valeur_compactee
            return resultat
        if isinstance(val, list):
            resultat = []
            for element in val:
                element_compact = compacter(element)
                if element_compact in (None, "", [], {}):
                    continue
                resultat.append(element_compact)
            return resultat
        return val

    if valeur is None:
        return ""
    if isinstance(valeur, str):
        try:
            valeur = json.loads(valeur)
        except (json.JSONDecodeError, TypeError):
            return valeur
    compacte = compacter(valeur)
    if compacte in (None, "", [], {}):
        return ""
    return json.dumps(compacte, ensure_ascii=False, default=str)


def localiser_donnees_export(demande):
    localisation = demande.localisation or {}
    if isinstance(localisation, str):
        try:
            localisation = json.loads(localisation)
        except (json.JSONDecodeError, TypeError):
            localisation = {}
    if not isinstance(localisation, dict):
        localisation = {}
    return {
        "localisation_source": localisation.get("source", ""),
        "localisation_ville": localisation.get("ville", ""),
        "localisation_pays": localisation.get("pays", ""),
        "localisation_ip": localisation.get("ip", ""),
        "localisation_latitude": localisation.get("latitude", localisation.get("lat", "")),
        "localisation_longitude": localisation.get("longitude", localisation.get("lng", "")),
        "localisation_precision_m": localisation.get("accuracy_m", localisation.get("accuracy", "")),
        "localisation_json": serialiser_export_json(localisation),
    }


def validation_la_plus_recente(demande):
    if not getattr(demande, "validations", None):
        return None
    return max(demande.validations, key=lambda validation: validation.id)


def serialiser_validation_export(validation):
    if not validation:
        return {
            "validation_action": "",
            "validation_motif": "",
            "validation_commentaire": "",
            "validation_date": "",
            "validation_valide_par": "",
        }
    return {
        "validation_action": validation.action.value if validation.action else "",
        "validation_motif": validation.motif_validation.value if validation.motif_validation else "",
        "validation_commentaire": validation.commentaire or "",
        "validation_date": validation.cree_le.isoformat() if validation.cree_le else "",
        "validation_valide_par": (
            f"{validation.validateur.prenom} {validation.validateur.nom}".strip()
            if validation.validateur
            else ""
        ),
    }


# --------------------------------------------------------------------------- #
# Authentification : rÃ©cupÃ©rer l'utilisateur courant Ã  partir du token JWT
# --------------------------------------------------------------------------- #

def get_utilisateur(
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


def maintenant_maroc() -> datetime:
    return datetime.now(MAROC_TZ)


def aujourdhui_maroc() -> date_type:
    return maintenant_maroc().date()


# --------------------------------------------------------------------------- #
# Calcul automatique des heures (rÃ¨gle simple par dÃ©faut : seuil 8h/jour)
# --------------------------------------------------------------------------- #

def calculer_heures(heure_debut, heure_fin, seuil_normal: Decimal = Decimal("9.00"), est_jour_ferie: bool = False, est_jour_de_repos: bool = False):
    debut = datetime.combine(aujourdhui_maroc(), heure_debut)
    fin = datetime.combine(aujourdhui_maroc(), heure_fin)
    if fin < debut:
        fin += timedelta(days=1)  # gÃ¨re les shifts de nuit qui passent minuit
    total_heures = Decimal(str(round((fin - debut).total_seconds() / 3600, 2)))
    if est_jour_ferie or est_jour_de_repos:
        return total_heures, Decimal("0.00"), total_heures
    heures_normales = min(total_heures, seuil_normal)
    heures_supplementaires = max(Decimal("0.00"), total_heures - seuil_normal)
    return total_heures, heures_normales, heures_supplementaires


def calculer_heures_shift(heure_debut, heure_fin, type_shift: str, est_jour_ferie: bool = False, est_jour_de_repos: bool = False):
    """Calcule heures normales vs supp pour technicien SHIFT.
    
    Shifts:
    - "matin": 6h-14h (8h normales)
    - "apres_midi": 14h-22h (8h normales)  
    - "nuit": 22h-6h (8h normales)
    
    Toute heure en dehors du shift = supplÃ©mentaire.
    """
    debut = datetime.combine(aujourdhui_maroc(), heure_debut)
    fin = datetime.combine(aujourdhui_maroc(), heure_fin)
    if fin < debut:
        fin += timedelta(days=1)  # gÃ¨re les shifts de nuit qui passent minuit
    
    total_heures = Decimal(str(round((fin - debut).total_seconds() / 3600, 2)))
    
    if est_jour_ferie or est_jour_de_repos:
        return total_heures, Decimal("0.00"), total_heures
    
    # DÃ©finir les limites du shift normal (8h)
    shifts_limites = {
        "matin": (time_type(6, 0), time_type(14, 0)),          # 6h-14h
        "apres_midi": (time_type(14, 0), time_type(22, 0)),    # 14h-22h
        "nuit": (time_type(22, 0), time_type(6, 0)),           # 22h-6h (passe minuit)
    }
    
    if type_shift not in shifts_limites:
        # Si shift inconnu, traiter comme normal
        heures_normales = min(total_heures, Decimal("8.00"))
        heures_supplementaires = max(Decimal("0.00"), total_heures - Decimal("8.00"))
        return total_heures, heures_normales, heures_supplementaires
    
    shift_debut, shift_fin = shifts_limites[type_shift]
    
    # Calculer le chevauchement entre [heure_debut, heure_fin] et [shift_debut, shift_fin]
    heures_normales = Decimal("0.00")
    
    if type_shift == "nuit":
        # Cas spÃ©cial : shift de nuit 22h-6h passe minuit
        # Heures normales si entre 22h et minuit OU entre 0h et 6h
        if heure_debut >= shift_debut:  # entre 22h et minuit
            normal_fin = min(heure_fin, time_type(23, 59, 59))
            heures_normales += Decimal(str(round((datetime.combine(date_type.today(), normal_fin) - 
                                                   datetime.combine(date_type.today(), heure_debut)).total_seconds() / 3600, 2)))
        if heure_fin <= shift_fin or heure_fin < heure_debut:  # entre 0h et 6h (ou aprÃ¨s minuit)
            normal_debut = max(heure_debut, time_type(0, 0))
            heures_normales += Decimal(str(round((datetime.combine(date_type.today(), heure_fin) - 
                                                   datetime.combine(date_type.today(), normal_debut)).total_seconds() / 3600, 2)))
    else:
        # Cas normal : shift diurne
        overlap_debut = max(heure_debut, shift_debut)
        overlap_fin = min(heure_fin, shift_fin)
        
        if overlap_debut < overlap_fin:
            heures_normales = Decimal(str(round((datetime.combine(date_type.today(), overlap_fin) - 
                                                  datetime.combine(date_type.today(), overlap_debut)).total_seconds() / 3600, 2)))
    
    # Limiter Ã  8h de normales max
    heures_normales = min(heures_normales, Decimal("8.00"))
    heures_supplementaires = max(Decimal("0.00"), total_heures - heures_normales)
    
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
        if date_jour and date_jour < aujourdhui_maroc():
            return True
    return False


def verifier_date_demande_technicien_shift(date_demande: date_type, utilisateur: models.Utilisateur):
    if utilisateur.role == models.RoleUtilisateur.TECHNICIEN_SHIFT:
        if date_demande != aujourdhui_maroc():
            raise HTTPException(
                status_code=400,
                detail="Un technicien shift ne peut saisir que la date du jour",
            )


def verifier_demande_modifiable(demande: models.Demande, utilisateur: models.Utilisateur):
    if utilisateur.role == models.RoleUtilisateur.TECHNICIEN_SHIFT:
        if demande.date_demande < aujourdhui_maroc() and not demande.autorise_modification_retro:
            raise HTTPException(
                status_code=403,
                detail="Cette demande ne peut plus Ãªtre modifiÃ©e aprÃ¨s la fin de la journÃ©e",
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
        logger.warning("SMS non envoyÃ©: configuration Twilio absente pour %s", numero)
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
        logger.warning("Ã‰chec de l'envoi SMS vers %s: %s", numero, exc)
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
        logger.warning("Email de rÃ©initialisation non envoyÃ©: configuration SMTP absente pour %s", adresse)
        return False

    message = EmailMessage()
    message["Subject"] = "RÃ©initialisation de votre mot de passe"
    message["From"] = from_email
    message["To"] = adresse
    message.set_content(
        "Votre code de rÃ©initialisation Time MMSE est : {code}\n\n"
        "Ce code expire dans 15 minutes. Si vous n'Ãªtes pas Ã  l'origine de cette demande, ignorez ce message.".format(code=code)
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(message)
        return True
    except Exception as exc:
        logger.warning("Ã‰chec de l'envoi email vers %s: %s", adresse, exc)
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
        reference = aujourdhui_maroc()
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
        raise HTTPException(status_code=403, detail="Compte dÃ©sactivÃ©")

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
        return {"message": "Si ce compte existe, un code de rÃ©initialisation a Ã©tÃ© envoyÃ©."}

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
    return {"message": "Si ce compte existe, un code de rÃ©initialisation a Ã©tÃ© envoyÃ©."}


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
        raise HTTPException(status_code=400, detail="Code invalide ou expirÃ©")
    if not verifier_mot_de_passe(requete.code.strip(), reset.code_hash):
        raise HTTPException(status_code=400, detail="Code invalide ou expirÃ©")

    utilisateur.mot_de_passe_hash = hasher_mot_de_passe(requete.nouveau_mot_de_passe)
    reset.utilise_le = datetime.utcnow()
    db.commit()
    return {"message": "Mot de passe rÃ©initialisÃ© avec succÃ¨s."}


@app.get("/api/me")
def me(utilisateur: models.Utilisateur = Depends(get_utilisateur)):
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
    utilisateur: models.Utilisateur = Depends(get_utilisateur),
    db: Session = Depends(get_db),
):
    if utilisateur.role != models.RoleUtilisateur.ADMINISTRATEUR:
        raise HTTPException(status_code=403, detail="AccÃ¨s rÃ©servÃ© aux administrateurs")
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
    utilisateur: models.Utilisateur = Depends(get_utilisateur),
    db: Session = Depends(get_db),
):
    if utilisateur.role != models.RoleUtilisateur.ADMINISTRATEUR:
        raise HTTPException(status_code=403, detail="AccÃ¨s rÃ©servÃ© aux administrateurs")
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
    utilisateur: models.Utilisateur = Depends(get_utilisateur),
    db: Session = Depends(get_db),
):
    if utilisateur.role != models.RoleUtilisateur.ADMINISTRATEUR:
        raise HTTPException(status_code=403, detail="AccÃ¨s rÃ©servÃ© aux administrateurs")
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
    utilisateur: models.Utilisateur = Depends(get_utilisateur),
    db: Session = Depends(get_db),
):
    if utilisateur.role != models.RoleUtilisateur.ADMINISTRATEUR:
        raise HTTPException(status_code=403, detail="AccÃ¨s rÃ©servÃ© aux administrateurs")
    cible = db.get(models.Utilisateur, utilisateur_id)
    if not cible:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    cible.numero_telephone = nettoyer_telephone(payload.numero_telephone)
    db.commit()
    db.refresh(cible)
    return {"id": cible.id, "numero_telephone": cible.numero_telephone}


@app.get("/api/jours_feries", response_model=list[schemas.JourFerieReponse])
def lister_jours_feries(
    utilisateur: models.Utilisateur = Depends(get_utilisateur),
    db: Session = Depends(get_db),
):
    return db.query(models.JourFerie).order_by(models.JourFerie.date).all()


@app.post("/api/jours_feries", response_model=schemas.JourFerieReponse)
def creer_jour_ferie(
    jour: schemas.JourFerieCreation,
    utilisateur: models.Utilisateur = Depends(get_utilisateur),
    db: Session = Depends(get_db),
):
    if utilisateur.role != models.RoleUtilisateur.ADMINISTRATEUR:
        raise HTTPException(status_code=403, detail="AccÃ¨s rÃ©servÃ© aux administrateurs")
    existing = db.query(models.JourFerie).filter(models.JourFerie.date == jour.date).first()
    if existing:
        raise HTTPException(status_code=400, detail="Un jour fÃ©riÃ© existe dÃ©jÃ  Ã  cette date")
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
    utilisateur: models.Utilisateur = Depends(get_utilisateur),
    db: Session = Depends(get_db),
):
    if utilisateur.role != models.RoleUtilisateur.ADMINISTRATEUR:
        raise HTTPException(status_code=403, detail="AccÃ¨s rÃ©servÃ© aux administrateurs")
    entree = db.get(models.JourFerie, jour_ferie_id)
    if not entree:
        raise HTTPException(status_code=404, detail="Jour fÃ©riÃ© introuvable")
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
    request: Request,
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
    localisation_json: str | None = Form(None),
    fichiers: list[UploadFile] | None = File(None),
    utilisateur: models.Utilisateur = Depends(get_utilisateur),
    db: Session = Depends(get_db),
):
    # Autorise la crÃ©ation pour les techniciens et superviseurs (normaux et shift)
    allowed_roles = {
        models.RoleUtilisateur.TECHNICIEN,
        models.RoleUtilisateur.TECHNICIEN_SHIFT,
        models.RoleUtilisateur.SUPERVISEUR,
        models.RoleUtilisateur.SUPERVISEUR_SHIFT,
    }
    if utilisateur.role not in allowed_roles:
        raise HTTPException(status_code=403, detail="AccÃ¨s refusÃ© : rÃ´le non autorisÃ© Ã  crÃ©er une demande")

    # Pour les techniciens (non-supervisors) on exige qu'un superviseur soit associÃ©
    if utilisateur.role in {models.RoleUtilisateur.TECHNICIEN, models.RoleUtilisateur.TECHNICIEN_SHIFT} and not utilisateur.superviseur_id:
        raise HTTPException(status_code=400, detail="Aucun superviseur n'est associÃ© Ã  ce technicien")

    date_demande = aujourdhui_maroc()
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
            detail="Une seule demande est autorisÃ©e par jour. Ouvrez la demande existante pour la modifier.",
        )

    if utilisateur.role in {models.RoleUtilisateur.TECHNICIEN, models.RoleUtilisateur.SUPERVISEUR}:
        if not peut_saisir_weekend(utilisateur):
            for cle in set((normales_dict or {}).keys()) | set((supps_dict or {}).keys()) | set((conges_dict or {}).keys()):
                if not jour_a_du_contenu(normales_dict, supps_dict, conges_dict, cle):
                    continue
                date_jour = trouver_date_dans_demande(cle)
                if date_jour and est_weekend(date_jour):
                    raise HTTPException(status_code=403, detail="Saisie le week-end non autorisÃ©e pour votre rÃ´le")

    if utilisateur.role == models.RoleUtilisateur.TECHNICIEN_SHIFT:
        if payload_contient_jours_passes(normales_dict, supps_dict, conges_dict, date_demande) and not peut_saisir_jours_passes(utilisateur):
            raise HTTPException(status_code=403, detail="La saisie des jours passÃ©s nÃ©cessite l'autorisation d'un administrateur")

    if utilisateur.role == models.RoleUtilisateur.TECHNICIEN_SHIFT:
        if jour_a_du_contenu(normales_dict, supps_dict, conges_dict, jour_courant):
            if date_demande != aujourdhui_maroc():
                raise HTTPException(status_code=403, detail="Les techniciens Shift ne peuvent saisir que la date du jour")

    if utilisateur.role == models.RoleUtilisateur.TECHNICIEN:
        if jour_a_du_contenu(normales_dict, supps_dict, conges_dict, jour_courant):
            if date_demande != aujourdhui_maroc():
                raise HTTPException(status_code=403, detail="Les techniciens ne peuvent saisir que la date du jour")
            if est_weekend(date_demande) and not peut_saisir_weekend(utilisateur):
                raise HTTPException(status_code=403, detail="Saisie le week-end non autorisÃ©e pour votre rÃ´le")

    regle = trouver_regle_ot(db, utilisateur.departement_id, date_demande)

    # Calcule le total / normales / supp Ã  partir du breakdown envoyÃ©
    breakdown = calculer_heures_depuis_breakdown(normales_dict, supps_dict, conges_dict, db, utilisateur, date_demande)
    if breakdown is None:
        total, normales, supp = Decimal("0.00"), Decimal("0.00"), Decimal("0.00")
    else:
        total, normales, supp = breakdown

     # Stocke la justification globale si fournie.
    justification_stockee = justification_ot if justification_ot else None

    # RÃ©cupÃ¨re et gÃ©olocalise l'IP du client
    localisation = None
    if localisation_json:
        try:
            localisation = json.loads(localisation_json)
        except (json.JSONDecodeError, TypeError):
            localisation = None
    
    if not localisation:
        ip_client = obtenir_ip_client(request)
        localisation = geolocaliser_ip(ip_client)

    demande = models.Demande(
        reference=f"DEM-{maintenant_maroc():%Y%m%d%H%M%S}-{utilisateur.id}",
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
        envoyee_le=maintenant_maroc() if soumettre else None,
        localisation=localisation,
    )
    db.add(demande)
    db.commit()
    db.refresh(demande)

    if fichiers:
        uploads_dir = UPLOADS_DIR
        for fichier in fichiers:
            original_name = os.path.basename(fichier.filename)
            safe_name = f"{maintenant_maroc():%Y%m%d%H%M%S}_{original_name}"
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
    utilisateur: models.Utilisateur = Depends(get_utilisateur),
    db: Session = Depends(get_db),
):
    demande = db.get(models.Demande, demande_id)
    if not demande or demande.technicien_id != utilisateur.id:
        raise HTTPException(status_code=404, detail="Demande introuvable")

    if not fichiers:
        return {"detail": "Aucun fichier envoyÃ©"}

    uploads_dir = UPLOADS_DIR
    for fichier in fichiers:
        original_name = os.path.basename(fichier.filename)
        safe_name = f"{maintenant_maroc():%Y%m%d%H%M%S}_{original_name}"
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
    return {"detail": "Fichiers enregistrÃ©s"}


@app.get("/api/demandes", response_model=list[schemas.DemandeReponse])
def lister_demandes(
    statut: str | None = None,
    archived: bool = False,
    utilisateur: models.Utilisateur = Depends(get_utilisateur),
    db: Session = Depends(get_db),
):
    requete = db.query(models.Demande)
    if utilisateur.role in {models.RoleUtilisateur.TECHNICIEN, models.RoleUtilisateur.TECHNICIEN_SHIFT}:
        requete = requete.filter(models.Demande.technicien_id == utilisateur.id)
    elif utilisateur.role in {models.RoleUtilisateur.SUPERVISEUR, models.RoleUtilisateur.SUPERVISEUR_SHIFT}:
        requete = requete.filter(models.Demande.superviseur_id == utilisateur.id)
    # l'administrateur voit tout, pas de filtre

    # Filtre sur archivÃ© / non archivÃ© (par dÃ©faut on montre seulement non-archivÃ©)
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
    utilisateur: models.Utilisateur = Depends(get_utilisateur),
    db: Session = Depends(get_db),
):
    if utilisateur.role != models.RoleUtilisateur.ADMINISTRATEUR:
        raise HTTPException(status_code=403, detail="AccÃ¨s rÃ©servÃ© aux administrateurs")

    requete = db.query(models.Demande)
    if statut:
        try:
            requete = requete.filter(models.Demande.statut == models.StatutDemande(statut))
        except ValueError:
            raise HTTPException(status_code=400, detail="Statut de filtre invalide")

    demandes = requete.order_by(models.Demande.date_demande.desc()).all()
    sorties = io.StringIO(newline="")
    writer = csv.writer(sorties, delimiter=';')
    writer.writerow([
        "Reference",
        "Cree le",
        "Modifie le",
        "Technicien",
        "Superviseur",
        "Date demande",
        "Est archive",
        "Conge",
        "Autorise modification retro",
        "Departement ID",
        "Equipe ID",
        "Equipement",
        "OT SAP",
        "Type intervention",
        "Statut",
        "Heures travaillees",
        "Heures normales",
        "Heures supplementaires",
        "Heure debut",
        "Heure fin",
        "Heures normales par jour",
        "Heures supplementaires par jour",
        "Conges par jour",
        "Regle OT appliquee",
        "Description travaux",
        "Justification OT",
        "Validation action",
        "Validation motif",
        "Validation commentaire",
        "Validation date",
        "Validation valide par",
        "Commentaires",
        "Envoyee le",
        "Traitee le",
        "Localisation source",
        "Localisation ville",
        "Localisation pays",
        "Localisation IP",
        "Localisation latitude",
        "Localisation longitude",
        "Localisation precision m",
        "Localisation JSON",
        "Donnees compactes JSON",
    ])
    for demande in demandes:
        localisation = localiser_donnees_export(demande)
        validation = validation_la_plus_recente(demande)
        validation_export = serialiser_validation_export(validation)
        donnees_compactes = {
            "reference": demande.reference,
            "cree_le": demande.cree_le.isoformat() if demande.cree_le else "",
            "modifie_le": demande.modifie_le.isoformat() if demande.modifie_le else "",
            "technicien": {
                "matricule": demande.technicien.matricule if demande.technicien else "",
                "nom": f"{demande.technicien.prenom} {demande.technicien.nom}".strip() if demande.technicien else "",
            },
            "superviseur": {
                "matricule": demande.superviseur.matricule if demande.superviseur else "",
                "nom": f"{demande.superviseur.prenom} {demande.superviseur.nom}".strip() if demande.superviseur else "",
            },
            "date_demande": demande.date_demande.isoformat() if demande.date_demande else "",
            "est_archive": demande.est_archive,
            "conge": demande.conge,
            "autorise_modification_retro": demande.autorise_modification_retro,
            "departement_id": demande.departement_id,
            "equipe_id": demande.equipe_id,
            "heures_travaillees": str(demande.heures_travaillees),
            "heures_normales": str(demande.heures_normales),
            "heures_supplementaires": str(demande.heures_supplementaires),
            "heure_debut": demande.heure_debut.isoformat() if demande.heure_debut else "",
            "heure_fin": demande.heure_fin.isoformat() if demande.heure_fin else "",
            "heures_normales_par_jour": demande.heures_normales_par_jour,
            "heures_supplementaires_par_jour": demande.heures_supplementaires_par_jour,
            "conges_par_jour": demande.conges_par_jour,
            "regle_ot_appliquee_id": demande.regle_ot_appliquee_id,
            "equipement": demande.equipement or "",
            "ordre_travail_sap": demande.ordre_travail_sap or "",
            "type_intervention": demande.type_intervention.value if demande.type_intervention else "",
            "description_travaux": demande.description_travaux or "",
            "justification_ot": demande.justification_ot or "",
            "commentaires": demande.commentaires or "",
            "statut": demande.statut.value,
            "envoyee_le": demande.envoyee_le.isoformat() if demande.envoyee_le else "",
            "traitee_le": demande.traitee_le.isoformat() if demande.traitee_le else "",
            "localisation": demande.localisation,
            "validation": {
                "action": validation_export["validation_action"],
                "motif": validation_export["validation_motif"],
                "commentaire": validation_export["validation_commentaire"],
                "date": validation_export["validation_date"],
                "valide_par": validation_export["validation_valide_par"],
            },
        }
        writer.writerow([
            demande.reference,
            demande.cree_le.isoformat() if demande.cree_le else "",
            demande.modifie_le.isoformat() if demande.modifie_le else "",
            f"{demande.technicien.prenom} {demande.technicien.nom}" if demande.technicien else "",
            f"{demande.superviseur.prenom} {demande.superviseur.nom}" if demande.superviseur else "",
            demande.date_demande.isoformat() if demande.date_demande else "",
            str(bool(demande.est_archive)),
            str(bool(demande.conge)),
            str(bool(demande.autorise_modification_retro)),
            str(demande.departement_id or ""),
            str(demande.equipe_id or ""),
            demande.equipement or "",
            demande.ordre_travail_sap or "",
            demande.type_intervention.value if demande.type_intervention else "",
            demande.statut.value,
            str(demande.heures_travaillees),
            str(demande.heures_normales),
            str(demande.heures_supplementaires),
            demande.heure_debut.isoformat() if demande.heure_debut else "",
            demande.heure_fin.isoformat() if demande.heure_fin else "",
            serialiser_export_json(demande.heures_normales_par_jour),
            serialiser_export_json(demande.heures_supplementaires_par_jour),
            serialiser_export_json(demande.conges_par_jour),
            str(demande.regle_ot_appliquee_id or ""),
            demande.description_travaux or "",
            demande.justification_ot or "",
            validation_export["validation_action"],
            validation_export["validation_motif"],
            validation_export["validation_commentaire"],
            validation_export["validation_date"],
            validation_export["validation_valide_par"],
            demande.commentaires or "",
            demande.envoyee_le.isoformat() if demande.envoyee_le else "",
            demande.traitee_le.isoformat() if demande.traitee_le else "",
            localisation["localisation_source"],
            localisation["localisation_ville"],
            localisation["localisation_pays"],
            localisation["localisation_ip"],
            localisation["localisation_latitude"],
            localisation["localisation_longitude"],
            localisation["localisation_precision_m"],
            localisation["localisation_json"],
            serialiser_export_json(donnees_compactes),
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
    utilisateur: models.Utilisateur = Depends(get_utilisateur),
    db: Session = Depends(get_db),
):
    """Marque une demande brouillon comme archivÃ©e (soft delete). Seul le technicien
    propriÃ©taire peut archiver sa demande s'il est en brouillon. L'administrateur
    peut aussi archiver n'importe quelle demande.
    """
    demande = db.get(models.Demande, demande_id)
    if not demande:
        raise HTTPException(status_code=404, detail="Demande introuvable")

    # Technicien owners can only archive their own brouillons
    if utilisateur.role == models.RoleUtilisateur.TECHNICIEN:
        if demande.technicien_id != utilisateur.id:
            raise HTTPException(status_code=403, detail="AccÃ¨s refusÃ©")
        if demande.statut != models.StatutDemande.BROUILLON:
            raise HTTPException(status_code=400, detail="Seules les demandes en brouillon peuvent Ãªtre supprimÃ©es")
    else:
        # Administrateurs peuvent archiver n'importe quelle demande
        if utilisateur.role != models.RoleUtilisateur.ADMINISTRATEUR:
            raise HTTPException(status_code=403, detail="AccÃ¨s rÃ©servÃ©")

    demande.est_archive = True
    db.commit()
    return {"detail": "Demande archivÃ©e"}


@app.put("/api/demandes/{demande_id}/restore", response_model=schemas.DemandeReponse)
def restaurer_demande(
    demande_id: int,
    utilisateur: models.Utilisateur = Depends(get_utilisateur),
    db: Session = Depends(get_db),
):
    if utilisateur.role != models.RoleUtilisateur.ADMINISTRATEUR:
        raise HTTPException(status_code=403, detail="AccÃ¨s rÃ©servÃ© aux administrateurs")
    demande = db.get(models.Demande, demande_id)
    if not demande:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    if not demande.est_archive:
        raise HTTPException(status_code=400, detail="La demande n'est pas archivÃ©e")
    demande.est_archive = False
    db.commit()
    db.refresh(demande)
    return demande


@app.put("/api/demandes/{demande_id}/allow_retro", response_model=schemas.DemandeReponse)
def autoriser_modification_retro(
    demande_id: int,
    utilisateur: models.Utilisateur = Depends(get_utilisateur),
    db: Session = Depends(get_db),
):
    """Endpoint rÃ©servÃ© aux administrateurs pour autoriser la modification rÃ©troactive d'une demande."""
    if utilisateur.role != models.RoleUtilisateur.ADMINISTRATEUR:
        raise HTTPException(status_code=403, detail="AccÃ¨s rÃ©servÃ© aux administrateurs")
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
    utilisateur: models.Utilisateur = Depends(get_utilisateur),
    db: Session = Depends(get_db),
):
    # Autorise les techniciens normaux et shift Ã  modifier leurs demandes
    if utilisateur.role not in {models.RoleUtilisateur.TECHNICIEN, models.RoleUtilisateur.TECHNICIEN_SHIFT}:
        raise HTTPException(status_code=403, detail="Seul un technicien peut modifier une demande")

    demande = db.get(models.Demande, demande_id)
    if not demande or demande.technicien_id != utilisateur.id:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    if demande.statut not in {models.StatutDemande.BROUILLON, models.StatutDemande.RETOUR_MODIFICATION}:
        raise HTTPException(status_code=400, detail="Cette demande ne peut Ãªtre modifiÃ©e que si elle est en brouillon ou en retour de supervision")

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
                    raise HTTPException(status_code=403, detail="Saisie le week-end non autorisÃ©e pour votre rÃ´le")

    if utilisateur.role == models.RoleUtilisateur.TECHNICIEN_SHIFT:
        if payload_contient_jours_passes(normales_candidate, supps_candidate, conges_candidate, date_demande) and not peut_saisir_jours_passes(utilisateur):
            raise HTTPException(status_code=403, detail="La modification des jours passÃ©s nÃ©cessite l'autorisation d'un administrateur")

    if utilisateur.role == models.RoleUtilisateur.TECHNICIEN_SHIFT:
        if demande.date_demande < aujourdhui_maroc() and not demande.autorise_modification_retro:
            raise HTTPException(status_code=403, detail="Modification d'une journÃ©e passÃ©e non autorisÃ©e. Contactez un administrateur.")

    if utilisateur.role == models.RoleUtilisateur.TECHNICIEN:
        if jour_a_du_contenu(normales_candidate, supps_candidate, conges_candidate, jour_courant):
            if demande.date_demande != aujourdhui_maroc():
                raise HTTPException(status_code=403, detail="Les techniciens ne peuvent saisir que la date du jour")
            if est_weekend(demande.date_demande) and not utilisateur.autorise_weekend:
                raise HTTPException(status_code=403, detail="Saisie le week-end non autorisÃ©e pour votre rÃ´le")

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
    if donnees.localisation is not None:
        demande.localisation = donnees.localisation
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
    demande.envoyee_le = maintenant_maroc() if donnees.soumettre else None
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
    utilisateur: models.Utilisateur = Depends(get_utilisateur),
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
    if requete.action == "approuver" and not requete.motif_validation:
        raise HTTPException(status_code=400, detail="Un motif est requis pour approuver une demande")

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
    demande.traitee_le = maintenant_maroc()

    db.add(models.Validation(
        demande_id=demande.id,
        validateur_id=utilisateur.id,
        action=action_enum,
        motif_validation=requete.motif_validation,
        commentaire=requete.commentaire,
        heures_travaillees_modifiees=requete.heures_travaillees_modifiees,
        heures_supplementaires_modifiees=requete.heures_supplementaires_modifiees,
    ))
    notifier_utilisateur(
        db,
        db.get(models.Utilisateur, demande.technicien_id),
        demande.id,
        type_notif,
        f"Votre demande du {demande.date_demande} a Ã©tÃ© mise Ã  jour : {nouveau_statut.value}",
    )
    db.commit()
    db.refresh(demande)
    return demande


# --------------------------------------------------------------------------- #
# Planning Shift â€” Gestion des shifts pour techniciens SHIFT
# --------------------------------------------------------------------------- #

@app.post("/api/planning-shift", response_model=schemas.PlanningShiftReponse)
def creer_planning_shift(
    data: schemas.PlanningShiftCreation,
    db: Session = Depends(get_db),
    utilisateur: models.Utilisateur = Depends(get_utilisateur),
):
    """CrÃ©er/assigner un shift Ã  un utilisateur pour une date spÃ©cifique."""
    # Seul l'administrateur ou superviseur SHIFT peut assigner des shifts
    if utilisateur.role not in {
        models.RoleUtilisateur.ADMINISTRATEUR,
        models.RoleUtilisateur.SUPERVISEUR_SHIFT,
    }:
        raise HTTPException(status_code=403, detail="Non autorisÃ©")

    # VÃ©rifier si un shift existe dÃ©jÃ  pour cet utilisateur ce jour-lÃ 
    shift_existant = db.query(models.PlanningShift).filter(
        models.PlanningShift.utilisateur_id == data.utilisateur_id,
        models.PlanningShift.date == data.date,
    ).first()

    if shift_existant:
        # Modifier le shift existant
        shift_existant.type_shift = data.type_shift
        shift_existant.heure_debut = data.heure_debut
        shift_existant.heure_fin = data.heure_fin
        db.commit()
        db.refresh(shift_existant)
        return shift_existant

    # CrÃ©er un nouveau shift avec limites horaires par dÃ©faut selon le type
    shift_limites = {
        "matin": (time_type(6, 0), time_type(14, 0)),
        "apres_midi": (time_type(14, 0), time_type(22, 0)),
        "nuit": (time_type(22, 0), time_type(6, 0)),
    }
    
    if data.type_shift in shift_limites and not data.heure_debut:
        data.heure_debut, data.heure_fin = shift_limites[data.type_shift]

    nouveau_shift = models.PlanningShift(
        utilisateur_id=data.utilisateur_id,
        date=data.date,
        type_shift=data.type_shift,
        heure_debut=data.heure_debut,
        heure_fin=data.heure_fin,
    )
    db.add(nouveau_shift)
    db.commit()
    db.refresh(nouveau_shift)
    return nouveau_shift


@app.get("/api/planning-shift/{utilisateur_id}", response_model=list[schemas.PlanningShiftReponse])
def recuperer_shifts_utilisateur(
    utilisateur_id: int,
    semaine: str | None = None,  # Format: "2024-W05" (ISO 8601 week)
    db: Session = Depends(get_db),
    utilisateur: models.Utilisateur = Depends(get_utilisateur),
):
    """RÃ©cupÃ©rer les shifts assignÃ©s Ã  un utilisateur.
    
    Si semaine est fourni (format ISO 8601: 2024-W05), retourne les shifts de cette semaine.
    Sinon, retourne tous les shifts.
    """
    # VÃ©rifier que l'utilisateur peut accÃ©der Ã  ces donnÃ©es
    
    # L'utilisateur peut voir ses propres shifts OU l'administrateur/superviseur peut voir tout
    if utilisateur.id != utilisateur_id and utilisateur.role not in {
        models.RoleUtilisateur.ADMINISTRATEUR,
        models.RoleUtilisateur.SUPERVISEUR_SHIFT,
        models.RoleUtilisateur.SUPERVISEUR,
    }:
        raise HTTPException(status_code=403, detail="Non autorisÃ©")

    query = db.query(models.PlanningShift).filter(
        models.PlanningShift.utilisateur_id == utilisateur_id
    )

    # Filtrer par semaine si fournie
    if semaine:
        try:
            # Format: "2024-W05"
            parts = semaine.split("-W")
            year = int(parts[0])
            week = int(parts[1])
            # Calculer la date de dÃ©but de la semaine (lundi)
            jan_4 = date_type(year, 1, 4)
            week_1_monday = jan_4 - timedelta(days=jan_4.weekday())
            week_start = week_1_monday + timedelta(weeks=week - 1)
            week_end = week_start + timedelta(days=6)
            query = query.filter(
                models.PlanningShift.date >= week_start,
                models.PlanningShift.date <= week_end,
            )
        except Exception:
            pass  # Si erreur de format, retourner tous les shifts

    return query.order_by(models.PlanningShift.date).all()


@app.get("/api/planning-shift/{utilisateur_id}/current-shift", response_model=schemas.PlanningShiftReponse | None)
def recuperer_shift_actuel(
    utilisateur_id: int,
    db: Session = Depends(get_db),
    utilisateur: models.Utilisateur = Depends(get_utilisateur),
):
    """RÃ©cupÃ©rer le shift assignÃ© pour aujourd'hui."""
    # MÃªme vÃ©rification d'autorisation que /api/planning-shift/{utilisateur_id}
    
    if utilisateur.id != utilisateur_id and utilisateur.role not in {
        models.RoleUtilisateur.ADMINISTRATEUR,
        models.RoleUtilisateur.SUPERVISEUR_SHIFT,
        models.RoleUtilisateur.SUPERVISEUR,
    }:
        raise HTTPException(status_code=403, detail="Non autorisÃ©")

    today = aujourdhui_maroc()
    shift = db.query(models.PlanningShift).filter(
        models.PlanningShift.utilisateur_id == utilisateur_id,
        models.PlanningShift.date == today,
    ).first()

    return shift


@app.put("/api/planning-shift/{shift_id}", response_model=schemas.PlanningShiftReponse)
def modifier_planning_shift(
    shift_id: int,
    data: schemas.PlanningShiftCreation,
    db: Session = Depends(get_db),
    utilisateur: models.Utilisateur = Depends(get_utilisateur),
):
    """Modifier un shift existant."""
    if utilisateur.role not in {
        models.RoleUtilisateur.ADMINISTRATEUR,
        models.RoleUtilisateur.SUPERVISEUR_SHIFT,
    }:
        raise HTTPException(status_code=403, detail="Non autorisÃ©")

    shift = db.query(models.PlanningShift).filter(models.PlanningShift.id == shift_id).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift non trouvÃ©")

    shift.utilisateur_id = data.utilisateur_id
    shift.date = data.date
    shift.type_shift = data.type_shift
    shift.heure_debut = data.heure_debut
    shift.heure_fin = data.heure_fin
    
    db.commit()
    db.refresh(shift)
    return shift


@app.delete("/api/planning-shift/{shift_id}")
def supprimer_planning_shift(
    shift_id: int,
    db: Session = Depends(get_db),
    utilisateur: models.Utilisateur = Depends(get_utilisateur),
):
    """Supprimer un shift."""
    if utilisateur.role not in {
        models.RoleUtilisateur.ADMINISTRATEUR,
        models.RoleUtilisateur.SUPERVISEUR_SHIFT,
    }:
        raise HTTPException(status_code=403, detail="Non autorisÃ©")

    shift = db.query(models.PlanningShift).filter(models.PlanningShift.id == shift_id).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift non trouvÃ©")

    db.delete(shift)
    db.commit()
    return {"detail": "Shift supprimÃ©"}


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

