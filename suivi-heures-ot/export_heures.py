"""
Exportation des demandes d'heures en CSV ou texte.

Usage exemple :
    python export_heures.py --statut approuvee --format csv --output heures_approuvees.csv
    python export_heures.py --statut rejetee --format txt --output heures_rejetees.txt
"""

import argparse
import csv
from datetime import datetime
from pathlib import Path

from database import SessionLocal, engine
import models


def parse_date(value: str):
    return datetime.strptime(value, "%Y-%m-%d").date()


def build_query(session, statut, date_debut, date_fin):
    query = session.query(models.Demande)
    if statut and statut != "toutes":
        statut_enum = models.StatutDemande(statut)
        query = query.filter(models.Demande.statut == statut_enum)
    if date_debut:
        query = query.filter(models.Demande.date_demande >= date_debut)
    if date_fin:
        query = query.filter(models.Demande.date_demande <= date_fin)
    return query.order_by(models.Demande.date_demande.asc())


def to_csv(demandes, output_path: Path):
    fieldnames = [
        "reference",
        "date_demande",
        "statut",
        "technicien_matricule",
        "technicien_nom",
        "superviseur_matricule",
        "superviseur_nom",
        "heures_travaillees",
        "heures_normales",
        "heures_supplementaires",
        "ordre_travail_sap",
        "type_intervention",
        "description_travaux",
        "justification_ot",
        "commentaires",
        "envoyee_le",
        "traitee_le",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for demande in demandes:
            writer.writerow({
                "reference": demande.reference,
                "date_demande": demande.date_demande,
                "statut": demande.statut.value,
                "technicien_matricule": demande.technicien.matricule if demande.technicien else "",
                "technicien_nom": f"{demande.technicien.prenom} {demande.technicien.nom}" if demande.technicien else "",
                "superviseur_matricule": demande.superviseur.matricule if demande.superviseur else "",
                "superviseur_nom": f"{demande.superviseur.prenom} {demande.superviseur.nom}" if demande.superviseur else "",
                "heures_travaillees": demande.heures_travaillees,
                "heures_normales": demande.heures_normales,
                "heures_supplementaires": demande.heures_supplementaires,
                "ordre_travail_sap": demande.ordre_travail_sap or "",
                "type_intervention": demande.type_intervention.value if demande.type_intervention else "",
                "description_travaux": demande.description_travaux or "",
                "justification_ot": demande.justification_ot or "",
                "commentaires": demande.commentaires or "",
                "envoyee_le": demande.envoyee_le.isoformat() if demande.envoyee_le else "",
                "traitee_le": demande.traitee_le.isoformat() if demande.traitee_le else "",
            })


def to_text(demandes, output_path: Path):
    with output_path.open("w", encoding="utf-8") as textfile:
        for demande in demandes:
            textfile.write(
                f"Référence : {demande.reference}\n"
                f"Date demande : {demande.date_demande}\n"
                f"Statut : {demande.statut.value}\n"
                f"Technicien : {demande.technicien.matricule if demande.technicien else ''} "
                f"{demande.technicien.prenom if demande.technicien else ''} "
                f"{demande.technicien.nom if demande.technicien else ''}\n"
                f"Superviseur : {demande.superviseur.matricule if demande.superviseur else ''} "
                f"{demande.superviseur.prenom if demande.superviseur else ''} "
                f"{demande.superviseur.nom if demande.superviseur else ''}\n"
                f"Heures travaillées : {demande.heures_travaillees}\n"
                f"Heures normales : {demande.heures_normales}\n"
                f"Heures supplémentaires : {demande.heures_supplementaires}\n"
                f"OT SAP : {demande.ordre_travail_sap or ''}\n"
                f"Type intervention : {demande.type_intervention.value if demande.type_intervention else ''}\n"
                f"Description : {demande.description_travaux or ''}\n"
                f"Justification OT : {demande.justification_ot or ''}\n"
                f"Commentaires : {demande.commentaires or ''}\n"
                f"Envoyée le : {demande.envoyee_le.isoformat() if demande.envoyee_le else ''}\n"
                f"Traitée le : {demande.traitee_le.isoformat() if demande.traitee_le else ''}\n"
                "-" * 80 + "\n"
            )


def main():
    parser = argparse.ArgumentParser(description="Exporter les demandes d'heures approuvées ou rejetées")
    parser.add_argument(
        "--statut",
        choices=[s.value for s in models.StatutDemande] + ["toutes"],
        default="toutes",
        help="Statut des demandes à exporter",
    )
    parser.add_argument(
        "--format",
        choices=["csv", "txt"],
        default="csv",
        help="Format de sortie",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Chemin du fichier de sortie (par défaut: heures_<statut>.<format>)",
    )
    parser.add_argument(
        "--date-debut",
        type=parse_date,
        help="Date de début (YYYY-MM-DD) pour filtrer les demandes",
    )
    parser.add_argument(
        "--date-fin",
        type=parse_date,
        help="Date de fin (YYYY-MM-DD) pour filtrer les demandes",
    )
    args = parser.parse_args()

    output_path = Path(args.output or f"heures_{args.statut}.{args.format}")

    session = SessionLocal()
    try:
        demandes = build_query(session, args.statut, args.date_debut, args.date_fin).all()
        if not demandes:
            print("Aucune demande trouvée pour les critères choisis.")
            return
        if args.format == "csv":
            to_csv(demandes, output_path)
        else:
            to_text(demandes, output_path)
        print(f"Export réalisé : {output_path}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
