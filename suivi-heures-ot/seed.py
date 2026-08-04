"""
Crée des données de test : un département, une équipe, un administrateur,
un superviseur, un superviseur SHIFT et deux techniciens — pour pouvoir tester l'application
immédiatement sans passer par un formulaire d'inscription.

Le script est idempotent : il peut être relancé sans dupliquer les comptes.
"""

from database import SessionLocal, engine
import models
from auth import hasher_mot_de_passe

models.Base.metadata.create_all(bind=engine)


def ensure_default_data():
    db = SessionLocal()
    try:
        dept = db.query(models.Departement).filter(models.Departement.code == "MAINT").first()
        if not dept:
            dept = models.Departement(code="MAINT", nom="Maintenance")
            db.add(dept)
            db.flush()

        equipe = (
            db.query(models.Equipe)
            .filter(models.Equipe.nom == "Équipe A")
            .filter(models.Equipe.departement_id == dept.id)
            .first()
        )
        if not equipe:
            equipe = models.Equipe(nom="Équipe A", shift="Jour", departement_id=dept.id)
            db.add(equipe)
            db.flush()

        users = {
            "ADMIN001": dict(
                nom="Admin",
                prenom="Système",
                email="admin@exemple.com",
                numero_telephone="0600000001",
                mot_de_passe="admin123",
                role=models.RoleUtilisateur.ADMINISTRATEUR,
                superviseur_id=None,
            ),
            "SUP001": dict(
                nom="Bernard",
                prenom="Marie",
                email="marie.bernard@exemple.com",
                numero_telephone="0600000002",
                mot_de_passe="sup123",
                role=models.RoleUtilisateur.SUPERVISEUR,
                superviseur_id=None,
            ),
            "SUPS001": dict(
                nom="Shift",
                prenom="Sarah",
                email="sarah.shift@exemple.com",
                numero_telephone="0600000005",
                mot_de_passe="supshift123",
                role=models.RoleUtilisateur.SUPERVISEUR_SHIFT,
                superviseur_id=None,
            ),
            "TECH001": dict(
                nom="Dupont",
                prenom="Jean",
                email="jean.dupont@exemple.com",
                numero_telephone="0600000003",
                mot_de_passe="tech123",
                role=models.RoleUtilisateur.TECHNICIEN,
                superviseur_id="SUP001",
            ),
            "TECHS001": dict(
                nom="Shift",
                prenom="Hamid",
                email="hamid.shift@exemple.com",
                numero_telephone="0600000004",
                mot_de_passe="shift123",
                role=models.RoleUtilisateur.TECHNICIEN_SHIFT,
                superviseur_id="SUP001",
            ),
        }

        created = []
        for matricule, data in users.items():
            utilisateur = db.query(models.Utilisateur).filter(models.Utilisateur.matricule == matricule).first()
            if utilisateur:
                continue
            superviseur_id = None
            if data["superviseur_id"]:
                superviseur = db.query(models.Utilisateur).filter(models.Utilisateur.matricule == data["superviseur_id"]).first()
                superviseur_id = superviseur.id if superviseur else None

            utilisateur = models.Utilisateur(
                matricule=matricule,
                nom=data["nom"],
                prenom=data["prenom"],
                email=data["email"],
                numero_telephone=data["numero_telephone"],
                mot_de_passe_hash=hasher_mot_de_passe(data["mot_de_passe"]),
                role=data["role"],
                departement_id=dept.id,
                equipe_id=equipe.id,
                superviseur_id=superviseur_id,
            )
            db.add(utilisateur)
            created.append(matricule)

        reg = (
            db.query(models.RegleHeuresSupplementaires)
            .filter(models.RegleHeuresSupplementaires.departement_id == dept.id)
            .first()
        )
        if not reg:
            db.add(models.RegleHeuresSupplementaires(
                departement_id=dept.id,
                nom="Règle par défaut - Maintenance",
                seuil_heures_normales_jour=8,
                seuil_heures_normales_semaine=40,
                taux_majoration_ot=1.5,
            ))

        db.commit()
        if created:
            print("[OK] Test data created/updated:")
            for matricule in created:
                print(f"   {matricule}")
        else:
            print("[OK] Test data already present.")
    finally:
        db.close()


if __name__ == "__main__":
    ensure_default_data()
