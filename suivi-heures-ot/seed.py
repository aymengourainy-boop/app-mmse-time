"""
Crée des données de test : un département, une équipe, un administrateur,
un superviseur et deux techniciens — pour pouvoir tester l'application
immédiatement sans passer par un formulaire d'inscription.

À exécuter UNE SEULE FOIS :
    python seed.py
"""

from database import SessionLocal, engine
import models
from auth import hasher_mot_de_passe

models.Base.metadata.create_all(bind=engine)

db = SessionLocal()

if db.query(models.Utilisateur).count() > 0:
    print("WARNING: Users already exist, skipping initialization.")
else:
    dept = models.Departement(code="MAINT", nom="Maintenance")
    db.add(dept)
    db.flush()  # pour obtenir dept.id sans committer

    equipe = models.Equipe(nom="Équipe A", shift="Jour", departement_id=dept.id)
    db.add(equipe)
    db.flush()

    admin = models.Utilisateur(
        matricule="ADMIN001",
        nom="Admin",
        prenom="Système",
        email="admin@exemple.com",
        numero_telephone="0600000001",
        mot_de_passe_hash=hasher_mot_de_passe("admin123"),
        role=models.RoleUtilisateur.ADMINISTRATEUR,
        departement_id=dept.id,
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
    )
    db.add_all([admin, superviseur])
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
    )
    db.add_all([technicien, technicien_shift])

    reg = models.RegleHeuresSupplementaires(
        departement_id=dept.id,
        nom="Règle par défaut - Maintenance",
        seuil_heures_normales_jour=8,
        seuil_heures_normales_semaine=40,
        taux_majoration_ot=1.5,
    )
    db.add(reg)

    db.commit()
    print("[OK] Test data created:")
    print("   Admin       -> matricule: ADMIN001  / password: admin123")
    print("   Supervisor  -> matricule: SUP001    / password: sup123")
    print("   Technician  -> matricule: TECH001   / password: tech123")
    print("   Shift tech   -> matricule: TECHS001  / password: shift123")

db.close()
