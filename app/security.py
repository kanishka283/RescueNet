import os
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from .database import get_db
from .models import Admin

SECRET = os.getenv("SECRET_KEY", "development-only-change-this-secret")
ALGORITHM = "HS256"
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def hash_password(password): return pwd.hash(password)
def verify_password(password, hashed): return pwd.verify(password, hashed)
def create_token(admin):
    payload = {"sub": str(admin.id), "role": admin.role, "exp": datetime.now(timezone.utc) + timedelta(minutes=int(os.getenv("ACCESS_TOKEN_MINUTES", "480")))}
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)

def current_admin(token: str = Depends(oauth2), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM])
        admin = db.get(Admin, int(payload["sub"]))
        if not admin or not admin.active: raise ValueError()
        return admin
    except (JWTError, ValueError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid or expired session")

def require_admin(admin=Depends(current_admin)):
    if admin.role != "admin": raise HTTPException(status_code=403, detail="Administrator role required")
    return admin
