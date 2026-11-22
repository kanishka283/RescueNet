from app.services.logic import haversine_km, risk_level, severity_score, prediction

def test_haversine_zero():
    assert haversine_km(13,80,13,80)==0

def test_risk_thresholds():
    assert risk_level(10)=="Low"
    assert risk_level(30)=="Moderate"
    assert risk_level(60)=="High"
    assert risk_level(90)=="Critical"

def test_severity_bounded():
    assert 0 <= severity_score(10,100,1000,100) <= 100

def test_prediction_safe_empty_inputs():
    result=prediction("Flood")
    assert result["risk_score"]==0
    assert 0 <= result["confidence"] <= 1
