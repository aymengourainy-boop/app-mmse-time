"""
Modèle de données - Application de suivi des heures de travail et heures supplémentaires (OT)
================================================================================================

Stack : SQLAlchemy 2.0 (style déclaratif moderne avec `Mapped` / `mapped_column`)
Compatible PostgreSQL (production) et SQLite (développement).

Principes de conception :
- Aucune suppression physique des données métier : les demandes, validations,
  commentaires et pièces jointes ne sont jamais DELETE, seulement archivés
  (champ `est_archive`) si besoin. L'historique (HistoriqueDemande) capture
  un instantané complet à chaque modification.
- Les enums métier sont des `Enum` Python natifs, mappés en `sa.Enum` SQLAlchemy
  (portable Postgres/SQLite).
- Horodatage systématique (`cree_le`, `modifie_le`) sur toutes les tables
  transactionnelles, via un mixin `TimestampMixin`.
- Les heures sont stockées en `Numeric(5, 2)` (ex: 8.50 h) pour éviter les
  erreurs d'arrondi des flottants.
- Les règles de calcul des heures supplémentaires sont paramétrables par
  département (table `RegleHeuresSupplementaires`), pas codées en dur.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    JSON,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    Index,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

# Type JSON portable : JSONB natif sous PostgreSQL, JSON générique sous SQLite
# (et tout autre moteur) pour rester compatible dev/prod comme demandé.
JSONVariant = JSON().with_variant(JSONB(), "postgresql")


# --------------------------------------------------------------------------- #
# Base & mixins
# --------------------------------------------------------------------------- #

class Base(DeclarativeBase):
    """Base déclarative commune à tous les modèles."""
    pass


class TimestampMixin:
    """Ajoute des colonnes d'horodatage systématiques à un modèle."""

    cree_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    modifie_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


def gen_uuid() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- #
# Enums métier
# --------------------------------------------------------------------------- #

class RoleUtilisateur(str, enum.Enum):
    TECHNICIEN = "technicien"
    SUPERVISEUR = "superviseur"
    TECHNICIEN_SHIFT = "technicien_shift"
    SUPERVISEUR_SHIFT = "superviseur_shift"
    ADMINISTRATEUR = "administrateur"


class StatutDemande(str, enum.Enum):
    BROUILLON = "brouillon"                        # 🔵
    EN_ATTENTE = "en_attente"                       # 🟡
    APPROUVEE = "approuvee"                         # 🟢
    REJETEE = "rejetee"                             # 🔴
    RETOUR_MODIFICATION = "retour_modification"     # 🟠


class TypeIntervention(str, enum.Enum):
    PREVENTIVE = "preventive"
    CORRECTIVE = "corrective"
    DEPANNAGE = "depannage"
    INSPECTION = "inspection"
    AUTRE = "autre"


class ActionValidation(str, enum.Enum):
    APPROUVER = "approuver"
    REJETER = "rejeter"
    RETOURNER = "retourner"


class TypePieceJointe(str, enum.Enum):
    PHOTO = "photo"
    DOCUMENT = "document"
    RAPPORT = "rapport"


class TypeNotification(str, enum.Enum):
    NOUVELLE_DEMANDE = "nouvelle_demande"           # -> superviseur
    DEMANDE_APPROUVEE = "demande_approuvee"         # -> technicien
    DEMANDE_REJETEE = "demande_rejetee"              # -> technicien
    RETOUR_MODIFICATION = "retour_modification"      # -> technicien


class TypeActionHistorique(str, enum.Enum):
    CREATION = "creation"
    MODIFICATION = "modification"
    SOUMISSION = "soumission"
    VALIDATION = "validation"
    REJET = "rejet"
    RETOUR = "retour"
    COMMENTAIRE = "commentaire"


# --------------------------------------------------------------------------- #
# Organisation : départements & équipes
# --------------------------------------------------------------------------- #

class Departement(Base, TimestampMixin):
    __tablename__ = "departements"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    nom: Mapped[str] = mapped_column(String(150), nullable=False)
    actif: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    equipes: Mapped[list["Equipe"]] = relationship(back_populates="departement")
    utilisateurs: Mapped[list["Utilisateur"]] = relationship(back_populates="departement")
    regles_ot: Mapped[list["RegleHeuresSupplementaires"]] = relationship(
        back_populates="departement"
    )


class Equipe(Base, TimestampMixin):
    """Équipe / Shift (ex: Équipe A - Quart de nuit)."""
    __tablename__ = "equipes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    nom: Mapped[str] = mapped_column(String(150), nullable=False)
    shift: Mapped[str | None] = mapped_column(String(50))  # ex: "Jour", "Nuit", "Rotatif"
    departement_id: Mapped[int] = mapped_column(ForeignKey("departements.id"), nullable=False)
    actif: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    departement: Mapped["Departement"] = relationship(back_populates="equipes")
    membres: Mapped[list["Utilisateur"]] = relationship(back_populates="equipe")


# --------------------------------------------------------------------------- #
# Utilisateurs
# --------------------------------------------------------------------------- #

class Utilisateur(Base, TimestampMixin):
    __tablename__ = "utilisateurs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    matricule: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    nom: Mapped[str] = mapped_column(String(100), nullable=False)
    prenom: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    numero_telephone: Mapped[str | None] = mapped_column(String(30))
    mot_de_passe_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[RoleUtilisateur] = mapped_column(
        SAEnum(RoleUtilisateur, name="role_utilisateur"), nullable=False
    )

    departement_id: Mapped[int | None] = mapped_column(ForeignKey("departements.id"))
    equipe_id: Mapped[int | None] = mapped_column(ForeignKey("equipes.id"))

    # Superviseur hiérarchique direct (auto-référence). NULL pour un admin ou
    # un superviseur sans hiérarchie déclarée dans l'app.
    superviseur_id: Mapped[int | None] = mapped_column(ForeignKey("utilisateurs.id"))

    actif: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    autorise_weekend: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    autorise_jours_passes: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    derniere_connexion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    departement: Mapped["Departement"] = relationship(back_populates="utilisateurs")
    equipe: Mapped["Equipe"] = relationship(back_populates="membres")
    superviseur: Mapped["Utilisateur | None"] = relationship(
        remote_side=[id], back_populates="techniciens"
    )
    techniciens: Mapped[list["Utilisateur"]] = relationship(back_populates="superviseur")

    demandes: Mapped[list["Demande"]] = relationship(
        back_populates="technicien", foreign_keys="Demande.technicien_id"
    )
    notifications: Mapped[list["Notification"]] = relationship(back_populates="utilisateur")

    __table_args__ = (
        Index("ix_utilisateurs_role_departement", "role", "departement_id"),
    )


# --------------------------------------------------------------------------- #
# Règles de calcul des heures supplémentaires (paramétrable par l'admin)
# --------------------------------------------------------------------------- #

class RegleHeuresSupplementaires(Base, TimestampMixin):
    """
    Règle appliquée pour déterminer le seuil à partir duquel les heures
    travaillées basculent en heures supplémentaires. Paramétrable par
    département ; une règle NULL sur departement_id = règle par défaut globale.
    """
    __tablename__ = "regles_heures_supplementaires"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    departement_id: Mapped[int | None] = mapped_column(ForeignKey("departements.id"))
    nom: Mapped[str] = mapped_column(String(150), nullable=False)

    seuil_heures_normales_jour: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, default=Decimal("9.00")
    )
    seuil_heures_normales_semaine: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("44.00")
    )
    taux_majoration_ot: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, default=Decimal("1.50")
    )  # ex: 1.5x, 2x le week-end, etc.

    actif: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    date_debut_validite: Mapped[date] = mapped_column(Date, nullable=False, server_default=func.current_date())
    date_fin_validite: Mapped[date | None] = mapped_column(Date)

    departement: Mapped["Departement | None"] = relationship(back_populates="regles_ot")


class JourFerie(Base, TimestampMixin):
    __tablename__ = "jours_feries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    nom: Mapped[str] = mapped_column(String(150), nullable=False)
    est_musulman: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    actif: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    comptabilise_pour_techniciens_normaux: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        Index("ix_jours_feries_date", "date"),
    )


# --------------------------------------------------------------------------- #
# Demande d'heures (cœur du système)
# --------------------------------------------------------------------------- #

class Demande(Base, TimestampMixin):
    __tablename__ = "demandes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    reference: Mapped[str] = mapped_column(
        String(40), unique=True, nullable=False, default=gen_uuid
    )  # référence publique/traçable, ex: DEM-2026-000123 (généré côté service)

    # --- Informations générales ---
    technicien_id: Mapped[int] = mapped_column(ForeignKey("utilisateurs.id"), nullable=False)
    superviseur_id: Mapped[int] = mapped_column(ForeignKey("utilisateurs.id"), nullable=False)
    departement_id: Mapped[int] = mapped_column(ForeignKey("departements.id"), nullable=False)
    equipe_id: Mapped[int | None] = mapped_column(ForeignKey("equipes.id"))
    date_demande: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # --- Heures de travail ---
    heure_debut: Mapped[time] = mapped_column(Time, nullable=False)
    heure_fin: Mapped[time] = mapped_column(Time, nullable=False)
    heures_travaillees: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    heures_normales: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    heures_supplementaires: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    conge: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    autorise_modification_retro: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    heures_normales_par_jour: Mapped[dict | None] = mapped_column(JSONVariant)
    heures_supplementaires_par_jour: Mapped[dict | None] = mapped_column(JSONVariant)
    conges_par_jour: Mapped[dict | None] = mapped_column(JSONVariant)
    regle_ot_appliquee_id: Mapped[int | None] = mapped_column(
        ForeignKey("regles_heures_supplementaires.id")
    )  # traçabilité : quelle règle a servi au calcul

    # --- Détails de l'intervention ---
    equipement: Mapped[str | None] = mapped_column(String(200))
    ordre_travail_sap: Mapped[str | None] = mapped_column(String(50), index=True)
    type_intervention: Mapped[TypeIntervention | None] = mapped_column(
        SAEnum(TypeIntervention, name="type_intervention")
    )
    description_travaux: Mapped[str | None] = mapped_column(Text)
    justification_ot: Mapped[str | None] = mapped_column(Text)
    commentaires: Mapped[str | None] = mapped_column(Text)

    # --- Statut & workflow ---
    statut: Mapped[StatutDemande] = mapped_column(
        SAEnum(StatutDemande, name="statut_demande"),
        nullable=False,
        default=StatutDemande.BROUILLON,
    )
    envoyee_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    traitee_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Aucune suppression physique : on archive plutôt que supprimer.
    est_archive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- Relations ---
    technicien: Mapped["Utilisateur"] = relationship(
        foreign_keys=[technicien_id], back_populates="demandes"
    )
    superviseur: Mapped["Utilisateur"] = relationship(foreign_keys=[superviseur_id])
    departement: Mapped["Departement"] = relationship()
    equipe: Mapped["Equipe | None"] = relationship()
    regle_ot_appliquee: Mapped["RegleHeuresSupplementaires | None"] = relationship()

    pieces_jointes: Mapped[list["PieceJointe"]] = relationship(
        back_populates="demande", cascade="all, delete-orphan"
    )
    commentaires_liste: Mapped[list["Commentaire"]] = relationship(
        back_populates="demande", cascade="all, delete-orphan"
    )
    validations: Mapped[list["Validation"]] = relationship(
        back_populates="demande", cascade="all, delete-orphan"
    )

    @property
    def technicien_prenom(self) -> str | None:
        return self.technicien.prenom if self.technicien else None

    @property
    def technicien_nom(self) -> str | None:
        return self.technicien.nom if self.technicien else None
    historique: Mapped[list["HistoriqueDemande"]] = relationship(
        back_populates="demande", cascade="all, delete-orphan"
    )
    notifications: Mapped[list["Notification"]] = relationship(back_populates="demande")

    __table_args__ = (
        Index("ix_demandes_technicien_date", "technicien_id", "date_demande"),
        Index("ix_demandes_superviseur_statut", "superviseur_id", "statut"),
        Index("ix_demandes_departement_date", "departement_id", "date_demande"),
    )


# --------------------------------------------------------------------------- #
# Pièces jointes
# --------------------------------------------------------------------------- #

class PieceJointe(Base, TimestampMixin):
    __tablename__ = "pieces_jointes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    demande_id: Mapped[int] = mapped_column(ForeignKey("demandes.id"), nullable=False)
    type: Mapped[TypePieceJointe] = mapped_column(
        SAEnum(TypePieceJointe, name="type_piece_jointe"), nullable=False
    )
    nom_fichier: Mapped[str] = mapped_column(String(255), nullable=False)
    chemin_stockage: Mapped[str] = mapped_column(String(500), nullable=False)  # chemin/objet S3
    taille_octets: Mapped[int | None] = mapped_column()
    type_mime: Mapped[str | None] = mapped_column(String(100))
    televerse_par_id: Mapped[int] = mapped_column(ForeignKey("utilisateurs.id"), nullable=False)

    demande: Mapped["Demande"] = relationship(back_populates="pieces_jointes")
    televerse_par: Mapped["Utilisateur"] = relationship()


# --------------------------------------------------------------------------- #
# Commentaires
# --------------------------------------------------------------------------- #

class Commentaire(Base, TimestampMixin):
    __tablename__ = "commentaires"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    demande_id: Mapped[int] = mapped_column(ForeignKey("demandes.id"), nullable=False)
    auteur_id: Mapped[int] = mapped_column(ForeignKey("utilisateurs.id"), nullable=False)
    contenu: Mapped[str] = mapped_column(Text, nullable=False)

    demande: Mapped["Demande"] = relationship(back_populates="commentaires_liste")
    auteur: Mapped["Utilisateur"] = relationship()


# --------------------------------------------------------------------------- #
# Validations (approbation / rejet / retour par le superviseur)
# --------------------------------------------------------------------------- #

class Validation(Base, TimestampMixin):
    __tablename__ = "validations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    demande_id: Mapped[int] = mapped_column(ForeignKey("demandes.id"), nullable=False)
    validateur_id: Mapped[int] = mapped_column(ForeignKey("utilisateurs.id"), nullable=False)
    action: Mapped[ActionValidation] = mapped_column(
        SAEnum(ActionValidation, name="action_validation"), nullable=False
    )
    commentaire: Mapped[str | None] = mapped_column(Text)

    # Si le superviseur corrige les heures avant validation
    heures_travaillees_modifiees: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    heures_supplementaires_modifiees: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    demande: Mapped["Demande"] = relationship(back_populates="validations")
    validateur: Mapped["Utilisateur"] = relationship()


# --------------------------------------------------------------------------- #
# Historique complet (audit trail / versioning des demandes)
# --------------------------------------------------------------------------- #

class HistoriqueDemande(Base):
    """
    Instantané complet de la demande à chaque action. Permet de reconstituer
    l'état exact de la demande à n'importe quel moment de son cycle de vie.
    Cette table n'est jamais modifiée ni supprimée (append-only).
    """
    __tablename__ = "historique_demandes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    demande_id: Mapped[int] = mapped_column(ForeignKey("demandes.id"), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)  # incrémenté à chaque action
    type_action: Mapped[TypeActionHistorique] = mapped_column(
        SAEnum(TypeActionHistorique, name="type_action_historique"), nullable=False
    )
    instantane_donnees: Mapped[dict] = mapped_column(JSONVariant, nullable=False)  # snapshot JSON complet
    modifie_par_id: Mapped[int] = mapped_column(ForeignKey("utilisateurs.id"), nullable=False)
    modifie_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    demande: Mapped["Demande"] = relationship(back_populates="historique")
    modifie_par: Mapped["Utilisateur"] = relationship()

    __table_args__ = (
        UniqueConstraint("demande_id", "version", name="uq_historique_demande_version"),
    )


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #

class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    utilisateur_id: Mapped[int] = mapped_column(ForeignKey("utilisateurs.id"), nullable=False)
    demande_id: Mapped[int | None] = mapped_column(ForeignKey("demandes.id"))
    type: Mapped[TypeNotification] = mapped_column(
        SAEnum(TypeNotification, name="type_notification"), nullable=False
    )
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    lu: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cree_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    lu_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    utilisateur: Mapped["Utilisateur"] = relationship(back_populates="notifications")
    demande: Mapped["Demande | None"] = relationship(back_populates="notifications")

    __table_args__ = (
        Index("ix_notifications_utilisateur_lu", "utilisateur_id", "lu"),
    )


class MotDePasseOublie(Base, TimestampMixin):
    __tablename__ = "mots_de_passe_oublies"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    utilisateur_id: Mapped[int] = mapped_column(ForeignKey("utilisateurs.id"), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expire_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    utilise_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    utilisateur: Mapped["Utilisateur"] = relationship()


# --------------------------------------------------------------------------- #
# Journal d'activité (logs techniques / sécurité, distinct de l'historique métier)
# --------------------------------------------------------------------------- #

class JournalActivite(Base):
    """
    Log générique de toutes les actions effectuées dans le système (connexions,
    exports, changements de droits, etc.), à des fins de sécurité et d'audit
    technique — complémentaire à HistoriqueDemande qui ne couvre que le
    cycle de vie métier des demandes.
    """
    __tablename__ = "journal_activite"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    utilisateur_id: Mapped[int | None] = mapped_column(ForeignKey("utilisateurs.id"))
    action: Mapped[str] = mapped_column(String(100), nullable=False)  # ex: "LOGIN", "EXPORT_CSV"
    entite: Mapped[str | None] = mapped_column(String(100))  # ex: "Demande"
    entite_id: Mapped[int | None] = mapped_column()
    details: Mapped[dict | None] = mapped_column(JSONVariant)
    adresse_ip: Mapped[str | None] = mapped_column(String(45))
    cree_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    utilisateur: Mapped["Utilisateur | None"] = relationship()
