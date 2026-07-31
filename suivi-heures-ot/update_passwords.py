from database import SessionLocal
import models
from auth import hasher_mot_de_passe

matricules = ['000002','000003','000004','000005','000006']

db = SessionLocal()
try:
    changed = []
    for m in matricules:
        u = db.query(models.Utilisateur).filter(models.Utilisateur.matricule==m).first()
        if not u:
            print(f"No user with matricule {m}")
            continue
        u.mot_de_passe_hash = hasher_mot_de_passe('AZER1234')
        changed.append(m)
    db.commit()
    print('Updated passwords for:', changed)
finally:
    db.close()
