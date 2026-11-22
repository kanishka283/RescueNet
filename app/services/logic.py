import math
from datetime import datetime, timezone

def haversine_km(lat1, lon1, lat2, lon2):
    r=6371
    p1,p2=math.radians(lat1),math.radians(lat2)
    dp=math.radians(lat2-lat1); dl=math.radians(lon2-lon1)
    a=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(math.sqrt(a))

def risk_level(score):
    return "Low" if score < 25 else "Moderate" if score < 50 else "High" if score < 75 else "Critical"

def severity_score(magnitude=0, reach=1, casualties=0, strain=0):
    return min(100, max(0, magnitude*8 + reach*4 + casualties*2 + strain*0.25))

def prediction(kind, rainfall=0, river_level=0, wind=0, magnitude=0, fire_index=0):
    # Transparent rule-based baseline; replace this adapter with a trained model when labelled data exists.
    signals = {"Flood": rainfall*0.35 + river_level*8, "Cyclone": wind*1.2,
               "Earthquake": magnitude*15, "Fire": fire_index*10}
    score=min(100,max(0,signals.get(kind, rainfall*0.2+wind*0.3)))
    return {"risk_score":round(score,1),"risk_level":risk_level(score),
            "confidence":round(min(.95,.55+score/250),2),
            "explanation":"Rule-based baseline from supplied hazard indicators; confidence is a heuristic, not a calibrated probability."}

def route_points(origin, destination):
    # Straight-line fallback route; connect an OSRM/road-graph provider here for road routing.
    return [origin, destination]
