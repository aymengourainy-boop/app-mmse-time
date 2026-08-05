"""
Script d'initialisation de la base de données.
Vérifie si la base existe et est valide, sinon la réinitialise.
"""

import sys
from pathlib import Path
from database import SessionLocal, engine, APP_DATA_DIR
import models
from auth import hasher_mot_de_passe

def init_database():
    """Initialise la base de données si elle n'existe pas ou est vide."""
    
    db_path = APP_DATA_DIR / "suivi_heures.db"
    print(f"[DB] Chemin: {db_path}")
    print(f"[DB] Existe: {db_path.exists()}")
    
    db = SessionLocal()
    try:
        # Vérifier si les tables existent
        user_count = db.query(models.Utilisateur).count()
        print(f"[DB] Utilisateurs trouvés: {user_count}")
        
        if user_count >= 5:
            print("[DB] Base de données valide avec tous les comptes")
            return True
        
        print("[DB] Base de données invalide ou incomplète, réinitialisation...")
        
    except Exception as e:
        print(f"[DB] Erreur lors de la vérification: {e}")
        print("[DB] Réinitialisation complète...")
    finally:
        db.close()
    
    # Réinitialiser la base complètement
    print("[DB] Suppression des tables...")
    models.Base.metadata.drop_all(bind=engine)
    
    print("[DB] Création des tables...")
    models.Base.metadata.create_all(bind=engine)
    
    # Insérer les données
    print("[DB] Population de la base de données...")
    db = SessionLocal()
    
    try:
        dept = models.Departement(code="MAINT", nom="Maintenance")
        db.add(dept)
        db.flush()
        
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
        
        print("[OK] Base de donnees initialisee avec succes!")
        print("\n=== COMPTES CREES ===")
        print("Admin              -> matricule: ADMIN001  / password: admin123")
        print("Supervisor         -> matricule: SUP001    / password: sup123")
        print("Supervisor Shift   -> matricule: SUPS001   / password: sups123")
        print("Technician         -> matricule: TECH001   / password: tech123")
        print("Shift Technician   -> matricule: TECHS001  / password: shift123")
        
        return True
        
    except Exception as e:
        db.rollback()
        print(f"[ERREUR] Erreur lors de l'initialisation: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()

if __name__ == "__main__":
    success = init_database()
    sys.exit(0 if success else 1)
