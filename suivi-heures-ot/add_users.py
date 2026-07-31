from database import SessionLocal
import models
from auth import hasher_mot_de_passe


def main():
    db = SessionLocal()
    users = [
        ("000002", "Superviseur", "MMSE", "superviseur@example.com", "superviseur"),
        ("000003", "Technicien", "Un", "tech1@example.com", "technicien"),
        ("000004", "Technicien", "Deux", "tech2@example.com", "technicien"),
        ("000005", "Technicien", "Trois", "tech3@example.com", "technicien"),
        ("000006", "Technicien", "Quatre", "tech4@example.com", "technicien"),
    ]
    created = []
    try:
        for matricule, nom, prenom, email, role_str in users:
            if db.query(models.Utilisateur).filter(models.Utilisateur.matricule == matricule).first():
                print(f"SKIP exists matricule {matricule}")
                continue
            if db.query(models.Utilisateur).filter(models.Utilisateur.email == email).first():
                print(f"SKIP exists email {email}")
                continue
            usr = models.Utilisateur(
                matricule=matricule,
                nom=nom,
                prenom=prenom,
                email=email,
                mot_de_passe_hash=hasher_mot_de_passe("AZER1234"),
                role=models.RoleUtilisateur(role_str),
            )
            db.add(usr)
            db.flush()
            created.append(matricule)
        db.commit()
        print("Created:", created)
    finally:
        db.close()


if __name__ == '__main__':
    main()
