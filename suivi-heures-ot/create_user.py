"""
Script CLI pour créer un utilisateur dans la base de données.
Usage examples:
  python create_user.py --matricule M001 --nom Dupont --prenom Alice --email alice@example.com --telephone 0600000000 --role technicien
  python create_user.py            # invite à saisir les champs interactifs
"""

import argparse
import getpass
import sys

from database import SessionLocal, engine
import models
from auth import hasher_mot_de_passe


models.Base.metadata.create_all(bind=engine)


def prompt_if_none(value, prompt_text):
    if value:
        return value
    try:
        return input(prompt_text).strip()
    except EOFError:
        return None


def main():
    parser = argparse.ArgumentParser(description="Créer un utilisateur dans la base")
    parser.add_argument("--matricule", help="Matricule unique de l'utilisateur")
    parser.add_argument("--nom", help="Nom")
    parser.add_argument("--prenom", help="Prénom")
    parser.add_argument("--email", help="Email (unique)")
    parser.add_argument("--telephone", help="Numéro de téléphone (optionnel, partagé autorisé)")
    parser.add_argument(
        "--role",
        choices=[r.value for r in models.RoleUtilisateur],
        default=models.RoleUtilisateur.TECHNICIEN.value,
        help="Rôle (technicien, superviseur, administrateur)",
    )
    parser.add_argument("--departement", help="Code du département (optionnel)")
    parser.add_argument("--equipe", help="Nom de l'équipe (optionnel)")
    parser.add_argument("--superviseur", help="Matricule du superviseur (optionnel)")

    args = parser.parse_args()

    matricule = prompt_if_none(args.matricule, "Matricule: ")
    nom = prompt_if_none(args.nom, "Nom: ")
    prenom = prompt_if_none(args.prenom, "Prénom: ")
    email = prompt_if_none(args.email, "Email: ")
    telephone = prompt_if_none(args.telephone, "Téléphone (optionnel): ")
    role = args.role

    if not matricule or not nom or not prenom or not email:
        print("Erreur: matricule, nom, prénom et email sont requis.")
        sys.exit(1)

    # mot de passe
    mot_de_passe = getpass.getpass("Mot de passe: ")
    mot_de_passe_confirm = getpass.getpass("Confirmez le mot de passe: ")
    if mot_de_passe != mot_de_passe_confirm:
        print("Les mots de passe ne correspondent pas.")
        sys.exit(1)

    db = SessionLocal()
    try:
        # vérifications d'unicité
        if db.query(models.Utilisateur).filter(models.Utilisateur.matricule == matricule).first():
            print(f"Erreur: un utilisateur avec le matricule '{matricule}' existe déjà.")
            sys.exit(1)
        if db.query(models.Utilisateur).filter(models.Utilisateur.email == email).first():
            print(f"Erreur: un utilisateur avec l'email '{email}' existe déjà.")
            sys.exit(1)

        # résoudre département si fourni
        departement_id = None
        if args.departement:
            dept = db.query(models.Departement).filter(models.Departement.code == args.departement).first()
            if not dept:
                print(f"Aucun département trouvé pour le code '{args.departement}'. Le compte sera créé sans département.")
            else:
                departement_id = dept.id

        # résoudre équipe si fourni (par nom)
        equipe_id = None
        if args.equipe:
            equipe = db.query(models.Equipe).filter(models.Equipe.nom == args.equipe).first()
            if not equipe:
                print(f"Aucune équipe trouvée nommée '{args.equipe}'. Le compte sera créé sans équipe.")
            else:
                equipe_id = equipe.id

        # résoudre superviseur par matricule si fourni
        superviseur_id = None
        if args.superviseur:
            sup = db.query(models.Utilisateur).filter(models.Utilisateur.matricule == args.superviseur).first()
            if not sup:
                print(f"Aucun superviseur trouvé pour le matricule '{args.superviseur}'.")
            else:
                superviseur_id = sup.id

        utilisateur = models.Utilisateur(
            matricule=matricule,
            nom=nom,
            prenom=prenom,
            email=email,
            numero_telephone=telephone or None,
            mot_de_passe_hash=hasher_mot_de_passe(mot_de_passe),
            role=models.RoleUtilisateur(role),
            departement_id=departement_id,
            equipe_id=equipe_id,
            superviseur_id=superviseur_id,
        )
        db.add(utilisateur)
        db.commit()
        db.refresh(utilisateur)

        print("Utilisateur créé avec succès :")
        print(f"  id: {utilisateur.id}  matricule: {utilisateur.matricule}  role: {utilisateur.role.value}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
