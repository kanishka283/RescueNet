"""Provider adapters. Demo feed is deterministic; replace with USGS/NOAA adapters as credentials/network permit."""
from datetime import datetime, timezone
def fetch_demo_events():
    return [
      {"provider_id":"demo-quake-001","kind":"Earthquake","title":"Demo seismic event · Bay of Bengal","latitude":12.0,"longitude":82.0,"magnitude":4.2,"occurred_at":datetime.now(timezone.utc),"source":"demo"},
      {"provider_id":"demo-flood-001","kind":"Flood","title":"Demo flood watch · Chennai region","latitude":13.08,"longitude":80.27,"magnitude":0,"occurred_at":datetime.now(timezone.utc),"source":"demo"}
    ]
