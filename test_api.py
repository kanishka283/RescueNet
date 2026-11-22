import os
os.environ["DATABASE_URL"]="sqlite:///./test_rescuenet.db"
from fastapi.testclient import TestClient
from app.main import app
client=TestClient(app)

def token():
    r=client.post("/api/auth/login",json={"username":os.getenv("BOOTSTRAP_ADMIN_EMAIL","admin@rescunet.local"),"password":os.getenv("BOOTSTRAP_ADMIN_PASSWORD","ChangeMe123!")})
    assert r.status_code==200
    return r.json()["access_token"]

def test_health():
    assert client.get("/api/health").json()["status"]=="ok"

def test_auth_and_dashboard():
    t=token()
    assert client.get("/api/dashboard",headers={"Authorization":f"Bearer {t}"}).status_code==200

def test_incident_validation():
    t=token()
    r=client.post("/api/incidents",headers={"Authorization":f"Bearer {t}"},json={"title":"Bad coordinates","kind":"Flood","latitude":999,"longitude":0})
    assert r.status_code==422
