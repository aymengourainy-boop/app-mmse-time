"""
Connexion à la base de données.

En développement : SQLite (fichier local suivi_heures.db).
En production : il suffit de changer DATABASE_URL pour une URL PostgreSQL,
par exemple : postgresql+psycopg2://utilisateur:motdepasse@localhost:5432/suivi_heures

C'est le seul fichier à modifier pour changer de base de données.
"""

import logging
import os
import tempfile
from datetime import date
from pathlib import Path
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
import models

logger = logging.getLogger(__name__)

def _get_app_data_dir() -> Path:
    """Détermine le répertoire de stockage des données pour les fichiers uploadés."""
    candidates: list[Path] = []

    app_data_dir = os.environ.get("APP_DATA_DIR")
    if app_data_dir:
        candidates.append(Path(app_data_dir).expanduser().resolve())

    # Préférer le répertoire temporaire, puis le répertoire courant
    candidates.append(Path(tempfile.gettempdir()) / "suivi-heures-ot")
    candidates.append(Path(".").resolve())

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            if os.access(candidate, os.W_OK | os.X_OK):
                logger.info("Repertoire de donnees utilise: %s", candidate)
                return candidate
        except OSError as exc:
            logger.warning("Impossible de preparer le repertoire de donnees %s: %s", candidate, exc)
            continue

    raise RuntimeError("Aucun repertoire de stockage accessible n'a pu etre prepare")


APP_DATA_DIR = _get_app_data_dir()


def default_database_url() -> str:
    env_database_url = os.environ.get("DATABASE_URL")
    if env_database_url:
        return env_database_url

    database_path = APP_DATA_DIR / "suivi_heures.db"
    if os.name == "nt":
        return f"sqlite:///{database_path.as_posix()}"
    return f"sqlite:///{database_path.as_posix()}"


DATABASE_URL = default_database_url()
logger.info("Using DATABASE_URL: %s", DATABASE_URL[:50] if DATABASE_URL else "None")

# connect_args nécessaire uniquement pour SQLite (pas pour PostgreSQL)
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

try:
    engine = create_engine(DATABASE_URL, connect_args=connect_args)
except Exception as e:
    logger.error("Failed to create database engine with URL %s: %s", DATABASE_URL, e)
    raise

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def ensure_schema():
    """Ajoute les colonnes métier manquantes aux tables existantes."""
    inspector = inspect(engine)
    if not inspector.has_table("demandes"):
        models.Base.metadata.create_all(bind=engine)
        return

    columns = {column["name"] for column in inspector.get_columns("demandes")}
    if "heures_normales_par_jour" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE demandes ADD COLUMN heures_normales_par_jour JSON"))
    if "heures_supplementaires_par_jour" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE demandes ADD COLUMN heures_supplementaires_par_jour JSON"))
    if "conges_par_jour" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE demandes ADD COLUMN conges_par_jour JSON"))
    if "conge" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE demandes ADD COLUMN conge BOOLEAN NOT NULL DEFAULT 0"))
    if "autorise_modification_retro" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE demandes ADD COLUMN autorise_modification_retro BOOLEAN NOT NULL DEFAULT 0"))
    if "localisation" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE demandes ADD COLUMN localisation JSON"))

    if inspector.has_table("utilisateurs"):
        utilisateur_columns = {column["name"] for column in inspector.get_columns("utilisateurs")}
        if "autorise_weekend" not in utilisateur_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE utilisateurs ADD COLUMN autorise_weekend BOOLEAN NOT NULL DEFAULT 0"))
        if "autorise_jours_passes" not in utilisateur_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE utilisateurs ADD COLUMN autorise_jours_passes BOOLEAN NOT NULL DEFAULT 0"))
        if "numero_telephone" not in utilisateur_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE utilisateurs ADD COLUMN numero_telephone VARCHAR(30)"))

    if not inspector.has_table("jours_feries"):
        models.Base.metadata.create_all(bind=engine, tables=[models.JourFerie.__table__])

    if inspector.has_table("jours_feries"):
        jours_feries_columns = {column["name"] for column in inspector.get_columns("jours_feries")}
        if "comptabilise_pour_techniciens_normaux" not in jours_feries_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE jours_feries ADD COLUMN comptabilise_pour_techniciens_normaux BOOLEAN NOT NULL DEFAULT 1"))
        with engine.begin() as conn:
            count = conn.execute(text("SELECT COUNT(1) FROM jours_feries")).scalar()
            if count == 0:
                current_year = int(os.environ.get("ANNEE_FERIES", date.today().year))
                conn.execute(
                    text(
                        "INSERT INTO jours_feries (date, nom, est_musulman, actif, comptabilise_pour_techniciens_normaux, description) VALUES "
                        "(:d1, :n1, false, true, true, :desc1), "
                        "(:d2, :n2, false, true, true, :desc2), "
                        "(:d3, :n3, false, true, true, :desc3), "
                        "(:d4, :n4, false, true, true, :desc4), "
                        "(:d5, :n5, false, true, true, :desc5), "
                        "(:d6, :n6, false, true, true, :desc6)"
                    ),
                    {
                        "d1": f"{current_year}-01-01",
                        "n1": "Nouvel An",
                        "desc1": "Jour de l'An",
                        "d2": f"{current_year}-05-01",
                        "n2": "Fête du Travail",
                        "desc2": "Fête du travail",
                        "d3": f"{current_year}-07-30",
                        "n3": "Fête du Trône",
                        "desc3": "Fête du Trône",
                        "d4": f"{current_year}-08-14",
                        "n4": "Révolution du Roi et du Peuple",
                        "desc4": "Jour de la Révolution du Roi et du Peuple",
                        "d5": f"{current_year}-08-20",
                        "n5": "Fête de la Jeunesse",
                        "desc5": "Fête de la Jeunesse",
                        "d6": f"{current_year}-11-06",
                        "n6": "Fête de l'Indépendance",
                        "desc6": "Fête de l'Indépendance",
                    },
                )

    if not inspector.has_table("mots_de_passe_oublies"):
        models.Base.metadata.create_all(bind=engine, tables=[models.MotDePasseOublie.__table__])

    if not inspector.has_table("validations"):
        models.Base.metadata.create_all(bind=engine, tables=[models.Validation.__table__])

    if inspector.has_table("validations"):
        validations_columns = {column["name"] for column in inspector.get_columns("validations")}
        if "motif_validation" not in validations_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE validations ADD COLUMN motif_validation VARCHAR(30)"))

    if not inspector.has_table("plannings_shift"):
        models.Base.metadata.create_all(bind=engine, tables=[models.PlanningShift.__table__])


def get_db():
    """Dépendance FastAPI : ouvre une session DB pour la requête, la ferme ensuite."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
