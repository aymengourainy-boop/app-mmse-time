from database import SessionLocal
import models

db = SessionLocal()
rows = db.query(models.Utilisateur).order_by(models.Utilisateur.id).all()
print('id matricule role superviseur_matricule email')
for u in rows:
    sup = db.query(models.Utilisateur).filter(models.Utilisateur.id == u.superviseur_id).first() if u.superviseur_id else None
    supm = sup.matricule if sup else None
    print(u.id, u.matricule, u.role.value, supm, u.email)

db.close()
