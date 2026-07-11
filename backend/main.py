"""
FuelFinder WA — backend proxy (v2).

New in v2: when a suburb search returns no stations (common for residential
suburbs like Sorrento that have no FuelWatch station of their own, and where
FuelWatch's Surrounding expansion doesn't always reach), the /api/fuel endpoint
automatically retries against the nearest suburb that DOES have stations,
picked from the user's coordinates. No region-ID table needed.

Endpoints:
  GET /api/geocode?suburb=Sorrento                     -> {lat, lng}
  GET /api/reverse?lat=-31.82&lng=115.75               -> {suburb}
  GET /api/fuel?product=3&suburb=Sorrento&lat=..&lng=.. -> [{...}], with fallback
  GET /api/route?from=lat,lng&to=lat,lng               -> {distanceKm, durationMin}

Run:
  pip install fastapi uvicorn httpx
  uvicorn main:app --reload --port 8000
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from math import radians, sin, cos, asin, sqrt
import xml.etree.ElementTree as ET
import asyncio
import httpx

app = FastAPI(title="FuelFinder WA API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local tool; lock down if deployed publicly
    allow_methods=["GET"],
    allow_headers=["*"],
)

FUELWATCH = "https://www.fuelwatch.wa.gov.au/fuelwatch/fuelWatchRSS"
NOMINATIM = "https://nominatim.openstreetmap.org"
OSRM = "https://router.project-osrm.org"
HEADERS = {"User-Agent": "FuelFinderWA/0.2 (prototype)"}

# Major Perth-metro suburbs that reliably have FuelWatch stations, with rough
# coordinates. Used only as a fallback: if the user's own suburb returns
# nothing, we find the nearest anchor here and search that instead. This is a
# pragmatic seed list — extend it freely.
ANCHOR_SUBURBS = [
    ("Joondalup", -31.7448, 115.7661),
    ("Hillarys", -31.8090, 115.7440),
    ("Wanneroo", -31.7500, 115.8000),
    ("Balcatta", -31.8720, 115.8280),
    ("Morley", -31.8880, 115.9090),
    ("Osborne Park", -31.8990, 115.8130),
    ("Scarborough", -31.8940, 115.7590),
    ("Innaloo", -31.8930, 115.7960),
    ("Midland", -31.8880, 116.0100),
    ("Bayswater", -31.9195, 115.9290),
    ("Perth", -31.9505, 115.8605),
    ("Subiaco", -31.9490, 115.8260),
    ("Fremantle", -32.0569, 115.7439),
    ("Cannington", -32.0170, 115.9340),
    ("Cockburn", -32.1240, 115.8410),
    ("Rockingham", -32.2770, 115.7290),
    ("Mandurah", -32.5290, 115.7230),
    ("Armadale", -32.1490, 116.0140),
    ("Midvale", -31.8860, 116.0230),
    ("Ellenbrook", -31.7700, 115.9660),
    ("Kwinana", -32.2390, 115.8290),
    ("Canning Vale", -32.0620, 115.9200),
    ("Success", -32.1400, 115.8500),
    ("Butler", -31.6410, 115.7050),
    ("Mundaring", -31.9010, 116.1660),
]


def _haversine(lat1, lng1, lat2, lng2):
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    h = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return 2 * 6371 * asin(sqrt(h))


def _nearest_anchor(lat, lng):
    return min(ANCHOR_SUBURBS, key=lambda a: _haversine(lat, lng, a[1], a[2]))


async def _get(url, params=None, headers=None):
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        r = await client.get(url, params=params, headers=headers or HEADERS)
        r.raise_for_status()
        return r


def _parse_stations(xml_text):
    root = ET.fromstring(xml_text)
    out = []
    for item in root.iter("item"):
        def txt(tag):
            el = item.find(tag)
            return el.text.strip() if el is not None and el.text else ""
        try:
            price = float(txt("price"))
            lat = float(txt("latitude"))
            lng = float(txt("longitude"))
        except ValueError:
            continue
        out.append({
            "name": txt("trading-name") or txt("title"),
            "brand": txt("brand"),
            "address": txt("address"),
            "suburb": txt("location"),
            "price": price,
            "lat": lat,
            "lng": lng,
        })
    return out


async def _fetch_stations(product, suburb):
    r = await _get(FUELWATCH, params={"Product": product, "Suburb": suburb, "Surrounding": "yes"})
    return _parse_stations(r.text)


def _nearest_anchors(lat, lng, n=6):
    """Return the n nearest anchor suburbs, closest first."""
    ranked = sorted(ANCHOR_SUBURBS, key=lambda a: _haversine(lat, lng, a[1], a[2]))
    return ranked[:n]


def _dedupe(stations):
    """Merge stations from multiple searches, keyed by name+address."""
    seen = {}
    for s in stations:
        key = (s["name"], s["address"])
        if key not in seen:
            seen[key] = s
    return list(seen.values())


@app.get("/api/geocode")
async def geocode(suburb: str):
    r = await _get(f"{NOMINATIM}/search",
                   params={"format": "json", "q": f"{suburb}, Western Australia, Australia", "limit": 1})
    data = r.json()
    if not data:
        raise HTTPException(404, f'Could not find "{suburb}" in WA.')
    return {"lat": float(data[0]["lat"]), "lng": float(data[0]["lon"])}


@app.get("/api/reverse")
async def reverse(lat: float, lng: float):
    r = await _get(f"{NOMINATIM}/reverse",
                   params={"format": "json", "lat": lat, "lon": lng, "zoom": 14})
    a = r.json().get("address", {})
    suburb = (a.get("suburb") or a.get("town") or a.get("city")
              or a.get("village") or a.get("municipality") or "")
    return {"suburb": suburb}


@app.get("/api/fuel")
async def fuel(
    product: int = 1,
    suburb: str = Query(...),
    lat: float | None = None,
    lng: float | None = None,
):
    """
    Fetch FuelWatch prices for a suburb. FuelWatch's per-suburb feed can be thin
    or empty for some fuels (diesel especially) even when stations exist nearby,
    so if the suburb yields too few results and coordinates are supplied, we pool
    results from the several nearest anchor suburbs and merge them.
    """
    try:
        stations = await _fetch_stations(product, suburb)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"FuelWatch request failed: {e}")

    used_fallback = None
    # Trigger the wider pooled search when the direct suburb is thin (0-2 results).
    if len(stations) < 3 and lat is not None and lng is not None:
        anchors = _nearest_anchors(lat, lng, n=6)
        results = await asyncio.gather(
            *[_fetch_stations(product, a[0]) for a in anchors],
            return_exceptions=True,
        )
        pooled = list(stations)
        for r in results:
            if isinstance(r, list):
                pooled.extend(r)
        merged = _dedupe(pooled)
        if len(merged) > len(stations):
            used_fallback = anchors[0][0]
            stations = merged

    # Sort by distance from the user if we have coords, else by price.
    if lat is not None and lng is not None:
        stations.sort(key=lambda s: _haversine(lat, lng, s["lat"], s["lng"]))
    else:
        stations.sort(key=lambda s: s["price"])

    return {"stations": stations, "searchedSuburb": used_fallback or suburb,
            "fallbackUsed": used_fallback is not None}


@app.get("/api/fuel-metro")
async def fuel_metro(
    product: int = 1,
    lat: float = Query(...),
    lng: float = Query(...),
    radius_km: float = 20.0,
):
    """
    Metro-wide search: query every anchor suburb across Perth, merge and dedupe,
    then return stations within radius_km of the user (each annotated with its
    straight-line distance). Lets the frontend find the cheapest station in the
    metro area within a sensible radius, and compare it to the closest one.
    """
    results = await asyncio.gather(
        *[_fetch_stations(product, a[0]) for a in ANCHOR_SUBURBS],
        return_exceptions=True,
    )
    pooled = []
    for r in results:
        if isinstance(r, list):
            pooled.extend(r)
    merged = _dedupe(pooled)

    # Annotate with distance from the user and keep those within the radius.
    within = []
    for s in merged:
        d = _haversine(lat, lng, s["lat"], s["lng"])
        if d <= radius_km:
            s = {**s, "distanceKm": round(d, 2)}
            within.append(s)

    within.sort(key=lambda s: s["distanceKm"])
    return {
        "stations": within,
        "radiusKm": radius_km,
        "totalFound": len(merged),
        "withinRadius": len(within),
    }


@app.get("/api/route")
async def route(from_: str = Query(..., alias="from"), to: str = Query(...)):
    f_lat, f_lng = (float(x) for x in from_.split(","))
    t_lat, t_lng = (float(x) for x in to.split(","))
    coords = f"{f_lng},{f_lat};{t_lng},{t_lat}"
    try:
        r = await _get(f"{OSRM}/route/v1/driving/{coords}", params={"overview": "false"})
        leg = r.json()["routes"][0]
        return {"distanceKm": leg["distance"] / 1000, "durationMin": leg["duration"] / 60}
    except (httpx.HTTPError, KeyError, IndexError):
        km = _haversine(f_lat, f_lng, t_lat, t_lng) * 1.35
        return {"distanceKm": km, "durationMin": km / 40 * 60, "estimated": True}


@app.get("/")
def health():
    return {"status": "ok", "service": "FuelFinder WA API"}
