"""
Authentification : hachage des mots de passe (bcrypt) et jetons JWT.
"""

from datetime import datetime, timedelta, timezone

from jose import jwt, JWTError
from passlib.context import CryptContext

SECRET_KEY = "CHANGEZ-CETTE-CLE-EN-PRODUCTION-utilisez-une-valeur-aleatoire-longue"
ALGORITHM = "HS256"
DUREE_TOKEN_MINUTES = 60 * 8  # 8 heures

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hasher_mot_de_passe(mot_de_passe: str) -> str:
    return pwd_context.hash(mot_de_passe)


def verifier_mot_de_passe(mot_de_passe: str, mot_de_passe_hash: str) -> bool:
    return pwd_context.verify(mot_de_passe, mot_de_passe_hash)


def creer_token(donnees: dict) -> str:
    a_encoder = donnees.copy()
    expiration = datetime.now(timezone.utc) + timedelta(minutes=DUREE_TOKEN_MINUTES)
    a_encoder.update({"exp": expiration})
    return jwt.encode(a_encoder, SECRET_KEY, algorithm=ALGORITHM)


def decoder_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
