"""
Exportation des demandes d'heures en CSV ou texte.

Usage exemple :
    python export_heures.py --statut approuvee --format csv --output heures_approuvees.csv
    python export_heures.py --statut rejetee --format txt --output heures_rejetees.txt
"""

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

from database import SessionLocal, engine, ensure_schema
import models

ensure_schema()


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


def compacter_donnees_export(demande):
    localisation = demande.localisation
    if isinstance(localisation, str):
        try:
            localisation = json.loads(localisation)
        except (json.JSONDecodeError, TypeError):
            localisation = {}
    return {
        "reference": demande.reference,
        "date_demande": demande.date_demande.isoformat() if demande.date_demande else "",
        "statut": demande.statut.value,
        "technicien": {
            "matricule": demande.technicien.matricule if demande.technicien else "",
            "nom": f"{demande.technicien.prenom} {demande.technicien.nom}".strip() if demande.technicien else "",
        },
        "superviseur": {
            "matricule": demande.superviseur.matricule if demande.superviseur else "",
            "nom": f"{demande.superviseur.prenom} {demande.superviseur.nom}".strip() if demande.superviseur else "",
        },
        "heures_travaillees": str(demande.heures_travaillees),
        "heures_normales": str(demande.heures_normales),
        "heures_supplementaires": str(demande.heures_supplementaires),
        "equipement": demande.equipement or "",
        "ordre_travail_sap": demande.ordre_travail_sap or "",
        "type_intervention": demande.type_intervention.value if demande.type_intervention else "",
        "description_travaux": demande.description_travaux or "",
        "justification_ot": demande.justification_ot or "",
        "validation": serialiser_validation_export(validation_la_plus_recente(demande)),
        "commentaires": demande.commentaires or "",
        "envoyee_le": demande.envoyee_le.isoformat() if demande.envoyee_le else "",
        "traitee_le": demande.traitee_le.isoformat() if demande.traitee_le else "",
        "heures_normales_par_jour": demande.heures_normales_par_jour,
        "heures_supplementaires_par_jour": demande.heures_supplementaires_par_jour,
        "conges_par_jour": demande.conges_par_jour,
        "localisation": localisation,
    }

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
        "validation_action",
        "validation_motif",
        "validation_commentaire",
        "validation_date",
        "validation_valide_par",
        "commentaires",
        "envoyee_le",
        "traitee_le",
        "localisation_source",
        "localisation_ville",
        "localisation_pays",
        "localisation_ip",
        "localisation_latitude",
        "localisation_longitude",
        "localisation_precision_m",
        "localisation_json",
        "donnees_compactes_json",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for demande in demandes:
            localisation = localiser_donnees_export(demande)
            donnees_compactes = compacter_donnees_export(demande)
            validation = serialiser_validation_export(validation_la_plus_recente(demande))
            writer.writerow({
                "reference": demande.reference,
                "date_demande": demande.date_demande.isoformat() if demande.date_demande else "",
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
                "validation_action": validation["validation_action"],
                "validation_motif": validation["validation_motif"],
                "validation_commentaire": validation["validation_commentaire"],
                "validation_date": validation["validation_date"],
                "validation_valide_par": validation["validation_valide_par"],
                "commentaires": demande.commentaires or "",
                "envoyee_le": demande.envoyee_le.isoformat() if demande.envoyee_le else "",
                "traitee_le": demande.traitee_le.isoformat() if demande.traitee_le else "",
                "localisation_source": localisation["localisation_source"],
                "localisation_ville": localisation["localisation_ville"],
                "localisation_pays": localisation["localisation_pays"],
                "localisation_ip": localisation["localisation_ip"],
                "localisation_latitude": localisation["localisation_latitude"],
                "localisation_longitude": localisation["localisation_longitude"],
                "localisation_precision_m": localisation["localisation_precision_m"],
                "localisation_json": localisation["localisation_json"],
                "donnees_compactes_json": serialiser_export_json(donnees_compactes),
            })


def to_text(demandes, output_path: Path):
    with output_path.open("w", encoding="utf-8") as textfile:
        for demande in demandes:
            localisation = localiser_donnees_export(demande)
            donnees_compactes = compacter_donnees_export(demande)
            validation = serialiser_validation_export(validation_la_plus_recente(demande))
            lignes = [
                ("Référence", demande.reference),
                ("Date demande", demande.date_demande.isoformat() if demande.date_demande else ""),
                ("Statut", demande.statut.value),
                ("Technicien", " ".join(filter(None, [
                    demande.technicien.matricule if demande.technicien else "",
                    demande.technicien.prenom if demande.technicien else "",
                    demande.technicien.nom if demande.technicien else "",
                ]))),
                ("Superviseur", " ".join(filter(None, [
                    demande.superviseur.matricule if demande.superviseur else "",
                    demande.superviseur.prenom if demande.superviseur else "",
                    demande.superviseur.nom if demande.superviseur else "",
                ]))),
                ("Heures travaillées", str(demande.heures_travaillees)),
                ("Heures normales", str(demande.heures_normales)),
                ("Heures supplémentaires", str(demande.heures_supplementaires)),
                ("OT SAP", demande.ordre_travail_sap or ""),
                ("Type intervention", demande.type_intervention.value if demande.type_intervention else ""),
                ("Description", demande.description_travaux or ""),
                ("Justification OT", demande.justification_ot or ""),
                ("Validation action", validation["validation_action"]),
                ("Validation motif", validation["validation_motif"]),
                ("Validation commentaire", validation["validation_commentaire"]),
                ("Validation date", validation["validation_date"]),
                ("Validation valide par", validation["validation_valide_par"]),
                ("Commentaires", demande.commentaires or ""),
                ("Envoyée le", demande.envoyee_le.isoformat() if demande.envoyee_le else ""),
                ("Traitée le", demande.traitee_le.isoformat() if demande.traitee_le else ""),
                ("Localisation", localisation["localisation_json"]),
                ("Données compactes", serialiser_export_json(donnees_compactes)),
            ]
            for libelle, valeur in lignes:
                if valeur in (None, "", [], {}):
                    continue
                textfile.write(f"{libelle} : {valeur}\n")
            textfile.write("-" * 80 + "\n")


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
