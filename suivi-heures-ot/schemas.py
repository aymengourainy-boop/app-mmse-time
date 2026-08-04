"""
Schémas Pydantic : définissent la forme des données échangées avec l'API
(ce que le frontend envoie et ce que l'API renvoie). Séparés des modèles
SQLAlchemy (models.py) qui définissent, eux, la structure de la base de données.
"""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from pydantic import BaseModel, ConfigDict

from models import MotifValidation


# --------------------------------------------------------------------------- #
# Authentification
# --------------------------------------------------------------------------- #

class LoginRequete(BaseModel):
    matricule: str
    mot_de_passe: str


class TokenReponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    nom: str
    prenom: str
    utilisateur_id: int
    autorise_weekend: bool = False
    autorise_jours_passes: bool = False


class MotDePasseOublieRequete(BaseModel):
    email: str


class MotDePasseReinitialisationRequete(BaseModel):
    email: str
    code: str
    nouveau_mot_de_passe: str


# --------------------------------------------------------------------------- #
# Demande
# --------------------------------------------------------------------------- #

class DemandeCreation(BaseModel):
    date_demande: date | None = None
    heure_debut: time | None = None
    heure_fin: time | None = None
    equipement: str | None = None
    ordre_travail_sap: str | None = None
    type_intervention: str | None = None
    description_travaux: str | None = None
    justification_ot: str | None = None
    commentaires: str | None = None
    conge: bool = False
    heures_normales_par_jour: dict[str, float | None] | None = None
    heures_supplementaires_par_jour: dict[str, float | None] | None = None
    conges_par_jour: dict[str, bool | None] | None = None
    soumettre: bool = False  # False = brouillon, True = envoi direct au superviseur
    localisation: dict[str, Any] | None = None  # {"ville": "Rabat", "pays": "Maroc", "ip": "..."}


class AutoriseWeekendRequete(BaseModel):
    autorise_weekend: bool


class AutoriseJoursPassesRequete(BaseModel):
    autorise_jours_passes: bool


class TelephoneUtilisateurRequete(BaseModel):
    numero_telephone: str | None = None


class JourFerieCreation(BaseModel):
    date: date
    nom: str
    est_musulman: bool = False
    actif: bool = True
    comptabilise_pour_techniciens_normaux: bool = True
    description: str | None = None


class JourFerieReponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: date
    nom: str
    est_musulman: bool
    actif: bool
    comptabilise_pour_techniciens_normaux: bool
    description: str | None


class ValidationRequete(BaseModel):
    action: str  # "approuver" | "rejeter" | "retourner"
    motif_validation: MotifValidation | None = None
    commentaire: str | None = None
    heures_travaillees_modifiees: Decimal | None = None
    heures_supplementaires_modifiees: Decimal | None = None


class DemandeReponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    reference: str
    technicien_id: int
    technicien_prenom: str | None = None
    technicien_nom: str | None = None
    superviseur_id: int
    date_demande: date
    heure_debut: time
    heure_fin: time
    heures_travaillees: Decimal
    heures_normales: Decimal
    heures_supplementaires: Decimal
    equipement: str | None
    ordre_travail_sap: str | None
    type_intervention: str | None
    description_travaux: str | None
    justification_ot: str | None
    commentaires: str | None
    conge: bool
    heures_normales_par_jour: dict[str, float | None] | None = None
    heures_supplementaires_par_jour: dict[str, float | None] | None = None
    conges_par_jour: dict[str, bool | None] | None = None
    localisation: dict[str, Any] | str | None = None
    statut: str
    cree_le: datetime
    regle_ot_appliquee_id: int | None


# --------------------------------------------------------------------------- #
# Planning Shift (pour techniciens SHIFT)
# --------------------------------------------------------------------------- #

class PlanningShiftCreation(BaseModel):
    utilisateur_id: int
    date: date
    type_shift: str  # "matin", "apres_midi", "nuit"
    heure_debut: time | None = None
    heure_fin: time | None = None


class PlanningShiftReponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    utilisateur_id: int
    date: date
    type_shift: str
    heure_debut: time | None
    heure_fin: time | None
    cree_le: datetime
