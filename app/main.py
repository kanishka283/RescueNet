import asyncio, csv, io, os, json
from datetime import datetime, timezone
from fastapi import FastAPI, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func
from .database import Base, engine, get_db, SessionLocal
from .models import Admin, Incident, Resource, Team, Alert, Event
from .security import hash_password, verify_password, create_token, current_admin, require_admin, SECRET, ALGORITHM
from jose import jwt, JWTError
from .services.logic import haversine_km, risk_level, severity_score, prediction, route_points
from .providers import fetch_demo_events

app=FastAPI(title="RescueNet Nexus API", version="1.0.0")
Base.metadata.create_all(bind=engine)

def seed():
    db=SessionLocal()
    try:
        if not db.query(Admin).filter_by(email=os.getenv("BOOTSTRAP_ADMIN_EMAIL","admin@rescunet.local")).first():
            db.add(Admin(email=os.getenv("BOOTSTRAP_ADMIN_EMAIL","admin@rescunet.local"),
                         password_hash=hash_password(os.getenv("BOOTSTRAP_ADMIN_PASSWORD","ChangeMe123!")),role="admin"))
        if db.query(Resource).count()==0:
            db.add_all([Resource(name="Ambulance 01",kind="Ambulance",latitude=13.0827,longitude=80.2707,capacity=2),
                        Resource(name="Rescue Boat 02",kind="Boat",latitude=13.05,longitude=80.25,capacity=6),
                        Resource(name="Fire Unit 03",kind="Fire Truck",latitude=13.10,longitude=80.22,capacity=4)])
        if db.query(Team).count()==0:
            db.add_all([Team(name="Rapid Response Alpha",specialty="Medical",active=True),Team(name="Flood Rescue Bravo",specialty="Water rescue",active=True)])
        if db.query(Incident).count()==0:
            db.add(Incident(title="Demo urban flooding report",kind="Flood",description="Seed incident for demonstration",latitude=13.08,longitude=80.27,severity="High",score=62,status="Reported",reach_km=3))
        db.commit()
    finally: db.close()
seed()

class Login(BaseModel):
    username: str
    password: str
class IncidentIn(BaseModel):
    title: str=Field(min_length=3,max_length=160)
    kind: str=Field(min_length=2,max_length=40)
    description: str=Field(default="",max_length=3000)
    latitude: float=Field(ge=-90,le=90)
    longitude: float=Field(ge=-180,le=180)
    casualties: int=Field(default=0,ge=0,le=100000)
    reach_km: float=Field(default=1,ge=0,le=1000)
    magnitude: float=Field(default=0,ge=0,le=10)
class ResourceIn(BaseModel):
    name:str=Field(min_length=2,max_length=120)
    kind:str=Field(min_length=2,max_length=40)
    latitude:float=Field(ge=-90,le=90)
    longitude:float=Field(ge=-180,le=180)
    capacity:int=Field(default=1,ge=1,le=10000)
    status:str=Field(default="Available")
class TeamIn(BaseModel):
    name:str=Field(min_length=2,max_length=120)
    specialty:str=Field(min_length=2,max_length=80)
    active:bool=True
class AlertIn(BaseModel):
    title:str=Field(min_length=3,max_length=160)
    message:str=Field(min_length=3,max_length=3000)
    severity:str="High"
    latitude:float|None=Field(default=None,ge=-90,le=90)
    longitude:float|None=Field(default=None,ge=-180,le=180)
class PredictionIn(BaseModel):
    kind:str
    rainfall:float=Field(default=0,ge=0,le=1000)
    river_level:float=Field(default=0,ge=0,le=30)
    wind:float=Field(default=0,ge=0,le=400)
    magnitude:float=Field(default=0,ge=0,le=10)
    fire_index:float=Field(default=0,ge=0,le=10)

# SSE notifications are deliberately lightweight and clients reconnect using EventSource.
subscribers=set()
async def publish(event_type="refresh"):
    stale=[]
    for q in list(subscribers):
        try: q.put_nowait(event_type)
        except asyncio.QueueFull: stale.append(q)
    for q in stale: subscribers.discard(q)

@app.get("/api/health")
def health(): return {"status":"ok","database":"connected"}

@app.post("/api/auth/login")
def login(data:Login, db:Session=Depends(get_db)):
    admin=db.query(Admin).filter(func.lower(Admin.email)==data.username.lower()).first()
    if not admin or not admin.active or not verify_password(data.password,admin.password_hash):
        raise HTTPException(401,"Incorrect username or password")
    return {"access_token":create_token(admin),"token_type":"bearer","role":admin.role,"email":admin.email}
@app.get("/api/auth/me")
def me(admin=Depends(current_admin)): return {"id":admin.id,"email":admin.email,"role":admin.role}
@app.post("/api/auth/logout")
def logout(admin=Depends(current_admin)): return {"message":"Client session cleared; discard bearer token"}

@app.get("/api/dashboard")
def dashboard(db:Session=Depends(get_db), admin=Depends(current_admin)):
    incidents=db.query(Incident).filter_by(archived=False).all()
    resources=db.query(Resource).all()
    return {"active_incidents":sum(i.status!="Resolved" for i in incidents),
      "critical_incidents":sum(i.severity=="Critical" for i in incidents),
      "available_resources":sum(r.status=="Available" for r in resources),
      "total_resources":len(resources),"active_alerts":db.query(Alert).count(),
      "incidents_by_status":{s:sum(i.status==s for i in incidents) for s in ["Reported","Dispatched","In Progress","Resolved"]},
      "resources_by_type":{k:sum(r.kind==k and r.status=="Available" for r in resources) for k in sorted({r.kind for r in resources})}}

@app.get("/api/incidents")
def list_incidents(q:str|None=None,status:str|None=None,kind:str|None=None,archived:bool=False,db:Session=Depends(get_db),admin=Depends(current_admin)):
    query=db.query(Incident).filter(Incident.archived==archived)
    if q: query=query.filter(Incident.title.ilike(f"%{q}%"))
    if status: query=query.filter(Incident.status==status)
    if kind: query=query.filter(Incident.kind==kind)
    return [incident_dict(i) for i in query.order_by(Incident.created_at.desc()).all()]
def incident_dict(i):
    return {"id":i.id,"title":i.title,"kind":i.kind,"description":i.description,"latitude":i.latitude,"longitude":i.longitude,
      "severity":i.severity,"score":i.score,"status":i.status,"casualties":i.casualties,"reach_km":i.reach_km,"magnitude":i.magnitude,
      "created_at":i.created_at.isoformat() if i.created_at else None,"dispatched_at":i.dispatched_at.isoformat() if i.dispatched_at else None,
      "resolved_at":i.resolved_at.isoformat() if i.resolved_at else None,"archived":i.archived}
@app.post("/api/incidents")
async def create_incident(data:IncidentIn,db:Session=Depends(get_db),admin=Depends(current_admin)):
    score=severity_score(data.magnitude,data.reach_km,data.casualties)
    i=Incident(**data.model_dump(),score=score,severity=risk_level(score))
    db.add(i); db.commit(); db.refresh(i); await publish()
    return incident_dict(i)
@app.patch("/api/incidents/{incident_id}/status")
async def update_status(incident_id:int,status:str=Query(...,pattern="^(Reported|Dispatched|In Progress|Resolved)$"),db:Session=Depends(get_db),admin=Depends(current_admin)):
    i=db.get(Incident,incident_id)
    if not i: raise HTTPException(404,"Incident not found")
    i.status=status
    if status=="Dispatched" and not i.dispatched_at: i.dispatched_at=datetime.now(timezone.utc)
    if status=="Resolved": i.resolved_at=datetime.now(timezone.utc)
    db.commit(); await publish(); return incident_dict(i)
@app.post("/api/incidents/{incident_id}/archive")
async def archive_incident(incident_id:int,db:Session=Depends(get_db),admin=Depends(require_admin)):
    i=db.get(Incident,incident_id)
    if not i: raise HTTPException(404,"Incident not found")
    i.archived=True; db.commit(); await publish(); return {"archived":True}

@app.get("/api/resources")
def resources(db:Session=Depends(get_db),admin=Depends(current_admin)):
    return [{"id":r.id,"name":r.name,"kind":r.kind,"latitude":r.latitude,"longitude":r.longitude,"status":r.status,"capacity":r.capacity,"team_id":r.team_id} for r in db.query(Resource).all()]
@app.post("/api/resources")
async def add_resource(data:ResourceIn,db:Session=Depends(get_db),admin=Depends(require_admin)):
    r=Resource(**data.model_dump()); db.add(r); db.commit(); db.refresh(r); await publish(); return {"id":r.id}
@app.patch("/api/resources/{rid}/status")
async def resource_status(rid:int,status:str=Query(...,pattern="^(Available|Dispatched|Busy|Offline)$"),db:Session=Depends(get_db),admin=Depends(current_admin)):
    r=db.get(Resource,rid)
    if not r: raise HTTPException(404,"Resource not found")
    r.status=status; db.commit(); await publish(); return {"id":rid,"status":status}
@app.get("/api/resources/nearest/{incident_id}")
def nearest(incident_id:int,db:Session=Depends(get_db),admin=Depends(current_admin)):
    i=db.get(Incident,incident_id)
    if not i: raise HTTPException(404,"Incident not found")
    available=[r for r in db.query(Resource).filter_by(status="Available").all()]
    available.sort(key=lambda r:haversine_km(i.latitude,i.longitude,r.latitude,r.longitude))
    return [{"id":r.id,"name":r.name,"kind":r.kind,"distance_km":round(haversine_km(i.latitude,i.longitude,r.latitude,r.longitude),2)} for r in available]
@app.post("/api/dispatch/{incident_id}/{resource_id}")
async def dispatch(incident_id:int,resource_id:int,db:Session=Depends(get_db),admin=Depends(current_admin)):
    i=db.get(Incident,incident_id); r=db.get(Resource,resource_id)
    if not i or not r: raise HTTPException(404,"Incident or resource not found")
    if r.status!="Available": raise HTTPException(409,"Resource is not available")
    r.status="Dispatched"; i.status="Dispatched"; i.dispatched_at=datetime.now(timezone.utc)
    db.commit(); await publish(); return {"message":"Resource dispatched","incident_id":i.id,"resource_id":r.id}
@app.get("/api/routes/{incident_id}/{resource_id}")
def route(incident_id:int,resource_id:int,db:Session=Depends(get_db),admin=Depends(current_admin)):
    i=db.get(Incident,incident_id); r=db.get(Resource,resource_id)
    if not i or not r: raise HTTPException(404,"Incident or resource not found")
    pts=route_points([r.latitude,r.longitude],[i.latitude,i.longitude])
    return {"provider":"straight-line-fallback","coordinates":pts,"note":"Connect OSRM or a road graph for road-network routing."}

@app.get("/api/teams")
def teams(db:Session=Depends(get_db),admin=Depends(current_admin)):
    return [{"id":t.id,"name":t.name,"specialty":t.specialty,"active":t.active} for t in db.query(Team).all()]
@app.post("/api/teams")
async def add_team(data:TeamIn,db:Session=Depends(get_db),admin=Depends(require_admin)):
    t=Team(**data.model_dump()); db.add(t); db.commit(); db.refresh(t); await publish(); return {"id":t.id}
@app.patch("/api/teams/{tid}")
async def edit_team(tid:int,data:TeamIn,db:Session=Depends(get_db),admin=Depends(require_admin)):
    t=db.get(Team,tid)
    if not t: raise HTTPException(404,"Team not found")
    for k,v in data.model_dump().items(): setattr(t,k,v)
    db.commit(); await publish(); return {"updated":True}
@app.get("/api/alerts")
def alerts(db:Session=Depends(get_db),admin=Depends(current_admin)):
    return [{"id":a.id,"title":a.title,"message":a.message,"severity":a.severity,"delivery_status":a.delivery_status,"created_at":a.created_at.isoformat()} for a in db.query(Alert).order_by(Alert.id.desc()).limit(100).all()]
@app.post("/api/alerts")
async def create_alert(data:AlertIn,db:Session=Depends(get_db),admin=Depends(current_admin)):
    a=Alert(**data.model_dump()); db.add(a); db.commit(); db.refresh(a); await publish(); return {"id":a.id,"delivery_status":a.delivery_status}

@app.post("/api/predictions")
def predict(data:PredictionIn,admin=Depends(current_admin)): return prediction(**data.model_dump())
@app.get("/api/events")
def events(db:Session=Depends(get_db),admin=Depends(current_admin)):
    return [{"id":e.id,"kind":e.kind,"title":e.title,"latitude":e.latitude,"longitude":e.longitude,"magnitude":e.magnitude,"occurred_at":e.occurred_at.isoformat(),"source":e.source} for e in db.query(Event).order_by(Event.occurred_at.desc()).all()]
@app.post("/api/events/refresh")
async def refresh_events(db:Session=Depends(get_db),admin=Depends(current_admin)):
    added=0
    for item in fetch_demo_events():
        if not db.query(Event).filter_by(provider_id=item["provider_id"]).first():
            db.add(Event(**item)); added+=1
    db.commit(); await publish(); return {"added":added,"provider":"demo-adapter"}
@app.get("/api/events/nearby")
def nearby(lat:float=Query(ge=-90,le=90),lon:float=Query(ge=-180,le=180),radius_km:float=Query(ge=0,le=5000),db:Session=Depends(get_db),admin=Depends(current_admin)):
    return [e for e in [{"id":x.id,"title":x.title,"kind":x.kind,"distance_km":round(haversine_km(lat,lon,x.latitude,x.longitude),2)} for x in db.query(Event).all()] if e["distance_km"]<=radius_km]

@app.get("/api/analytics")
def analytics(db:Session=Depends(get_db),admin=Depends(current_admin)):
    items=db.query(Incident).filter_by(archived=False).all()
    by_kind={}
    for i in items: by_kind[i.kind]=by_kind.get(i.kind,0)+1
    response=[(i.dispatched_at-i.created_at).total_seconds()/60 for i in items if i.dispatched_at and i.created_at]
    resolution=[(i.resolved_at-i.created_at).total_seconds()/60 for i in items if i.resolved_at and i.created_at]
    return {"by_type":by_kind,"by_status":{s:sum(i.status==s for i in items) for s in ["Reported","Dispatched","In Progress","Resolved"]},
      "average_dispatch_minutes":round(sum(response)/len(response),1) if response else None,
      "average_resolution_minutes":round(sum(resolution)/len(resolution),1) if resolution else None,
      "resource_utilization_percent":round(100*sum(r.status!="Available" for r in db.query(Resource).all())/max(1,db.query(Resource).count()),1)}
@app.get("/api/affected-area/{incident_id}")
def affected_area(incident_id:int,db:Session=Depends(get_db),admin=Depends(current_admin)):
    i=db.get(Incident,incident_id)
    if not i: raise HTTPException(404,"Incident not found")
    # Approximate circular impact zone polygon; latitude/longitude degree conversion.
    coords=[]
    for n in range(33):
        angle=2*3.141592653589793*n/32
        lat=i.latitude+(i.reach_km/111)*__import__("math").cos(angle)
        lon=i.longitude+(i.reach_km/(111*max(.1,__import__("math").cos(i.latitude*3.14159/180))))*__import__("math").sin(angle)
        coords.append([lon,lat])
    return {"type":"Feature","geometry":{"type":"Polygon","coordinates":[coords]},"properties":{"incident_id":i.id,"radius_km":i.reach_km}}

@app.get("/api/admins")
def admins(db:Session=Depends(get_db),admin=Depends(require_admin)):
    return [{"id":a.id,"email":a.email,"role":a.role,"active":a.active} for a in db.query(Admin).all()]
@app.post("/api/admins")
def add_admin(data:Login,role:str="operator",db:Session=Depends(get_db),admin=Depends(require_admin)):
    if role not in ("admin","operator","viewer"): raise HTTPException(422,"Invalid role")
    if db.query(Admin).filter_by(email=data.username).first(): raise HTTPException(409,"Email already exists")
    a=Admin(email=data.username,password_hash=hash_password(data.password),role=role); db.add(a); db.commit(); return {"created":True}
@app.get("/api/database/integrity")
def integrity(db:Session=Depends(get_db),admin=Depends(require_admin)):
    return {"admins":db.query(Admin).count(),"incidents":db.query(Incident).count(),"resources":db.query(Resource).count(),"teams":db.query(Team).count(),"alerts":db.query(Alert).count(),"events":db.query(Event).count(),"status":"ok"}
@app.get("/api/reports/incidents.csv")
def csv_report(db:Session=Depends(get_db),admin=Depends(current_admin)):
    output=io.StringIO(); w=csv.writer(output); w.writerow(["ID","Title","Type","Severity","Status","Latitude","Longitude","Reported"])
    for i in db.query(Incident).all(): w.writerow([i.id,i.title,i.kind,i.severity,i.status,i.latitude,i.longitude,i.created_at])
    return Response(output.getvalue(),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=rescuenet-incidents.csv"})
@app.get("/api/reports/summary.pdf")
def pdf_report(db:Session=Depends(get_db),admin=Depends(current_admin)):
    from reportlab.pdfgen import canvas
    buf=io.BytesIO(); c=canvas.Canvas(buf); c.setFont("Helvetica-Bold",16); c.drawString(50,800,"RescueNet Nexus - Incident Summary")
    c.setFont("Helvetica",10); y=770
    for i in db.query(Incident).order_by(Incident.id.desc()).limit(35).all():
        c.drawString(50,y,f"#{i.id} {i.title[:45]} | {i.kind} | {i.severity} | {i.status}"); y-=18
        if y<50: c.showPage(); y=800
    c.save(); return Response(buf.getvalue(),media_type="application/pdf",headers={"Content-Disposition":"attachment; filename=rescuenet-summary.pdf"})

@app.get("/api/stream")
async def stream(request:Request, access_token:str=Query(...)):
    try:
        payload=jwt.decode(access_token,SECRET,algorithms=[ALGORITHM])
        with SessionLocal() as check_db:
            a=check_db.get(Admin,int(payload["sub"]))
            if not a or not a.active: raise ValueError()
    except (JWTError,ValueError,KeyError,TypeError):
        raise HTTPException(401,"Invalid stream token")
    async def gen():
        q=asyncio.Queue(maxsize=20); subscribers.add(q)
        try:
            yield "event: connected\ndata: {}\n\n"
            while True:
                if await request.is_disconnected(): break
                try: msg=await asyncio.wait_for(q.get(),timeout=20)
                except asyncio.TimeoutError: msg="heartbeat"
                yield f"event: {msg}\ndata: {{}}\n\n"
        finally: subscribers.discard(q)
    return StreamingResponse(gen(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

app.mount("/",StaticFiles(directory="static",html=True),name="static")
