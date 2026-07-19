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
import csv
import os
from datetime import datetime, timedelta, timezone
import httpx

app = FastAPI(title="FuelFinder WA API")

# ---------------------------------------------------------------------------
# Vehicle fuel-economy data (Green Vehicle Guide-style: manufacturer combined
# L/100km by make/model/year/variant). Loaded once at startup from CSV.
# To expand coverage, replace data/vehicles.csv with a fuller export — the
# columns are: make,model,year,variant,fuel_type,combined_l100km
# ---------------------------------------------------------------------------
VEHICLES = []

def _load_vehicles():
    path = os.path.join(os.path.dirname(__file__), "data", "vehicles.csv")
    rows = []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    r["year"] = int(r["year"])
                    r["combined_l100km"] = float(r["combined_l100km"])
                except (ValueError, KeyError):
                    continue
                rows.append(r)
    except FileNotFoundError:
        pass
    return rows

VEHICLES = _load_vehicles()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local tool; lock down if deployed publicly
    allow_methods=["GET"],
    allow_headers=["*"],
)

FUELWATCH = "https://www.fuelwatch.wa.gov.au/api/sites"

# Maps the numeric product codes used by the frontend to the text fuelType codes
# the FuelWatch /api/sites endpoint expects. Confirmed against FuelWatch's live
# API: ULP=ULP, PULP=PUP, Diesel=DSL, Brand Diesel=BDL, LPG=LPG, 98 RON=98R.
PRODUCT_TO_FUELTYPE = {
    1: "ULP",   # Unleaded
    2: "PUP",   # Premium Unleaded (95)
    4: "DSL",   # Diesel
    5: "LPG",   # LPG
    6: "98R",   # 98 RON
    11: "BDL",  # Brand Diesel
}
NOMINATIM = "https://nominatim.openstreetmap.org"
OSRM = "https://router.project-osrm.org"
HEADERS = {"User-Agent": "FuelFinderWA/0.2 (prototype)"}

# FuelWatch-covered suburbs and towns used to seed searches. Covers the full
# Perth metro area plus regional centres from Geraldton (north) to Esperance
# (south), and the wheatbelt/goldfields in between. Used two ways:
#  - fallback when the user's own suburb returns nothing
#  - the metro/region-wide search pools stations across nearby anchors
# Extend freely; more anchors = better coverage but more FuelWatch calls.
ANCHOR_SUBURBS = [
    # --- Perth metro: north ---
    ("Two Rocks", -31.4980, 115.5880),
    ("Yanchep", -31.5480, 115.6330),
    ("Butler", -31.6410, 115.7050),
    ("Clarkson", -31.6790, 115.7290),
    ("Joondalup", -31.7448, 115.7661),
    ("Wanneroo", -31.7500, 115.8000),
    ("Hillarys", -31.8090, 115.7440),
    ("Ellenbrook", -31.7700, 115.9660),
    # --- Perth metro: central-north ---
    ("Scarborough", -31.8940, 115.7590),
    ("Innaloo", -31.8930, 115.7960),
    ("Balcatta", -31.8720, 115.8280),
    ("Osborne Park", -31.8990, 115.8130),
    ("Morley", -31.8880, 115.9090),
    ("Bayswater", -31.9195, 115.9290),
    ("Midland", -31.8880, 116.0100),
    ("Midvale", -31.8860, 116.0230),
    ("Mundaring", -31.9010, 116.1660),
    # --- Perth metro: central ---
    ("Perth", -31.9505, 115.8605),
    ("Subiaco", -31.9490, 115.8260),
    ("Victoria Park", -31.9740, 115.9000),
    ("Belmont", -31.9450, 115.9330),
    # --- Perth metro: south ---
    ("Fremantle", -32.0569, 115.7439),
    ("Cannington", -32.0170, 115.9340),
    ("Canning Vale", -32.0620, 115.9200),
    ("Cockburn", -32.1240, 115.8410),
    ("Success", -32.1400, 115.8500),
    ("Armadale", -32.1490, 116.0140),
    ("Byford", -32.2200, 116.0090),
    ("Kwinana", -32.2390, 115.8290),
    ("Rockingham", -32.2770, 115.7290),
    ("Mandurah", -32.5290, 115.7230),
    ("Pinjarra", -32.6290, 115.8730),
    # --- Regional: north (toward Geraldton) ---
    ("Gingin", -31.3480, 115.9060),
    ("Jurien Bay", -30.3060, 115.0400),
    ("Moora", -30.6410, 116.0060),
    ("Dongara", -29.2560, 114.9320),
    ("Geraldton", -28.7744, 114.6089),
    # --- Regional: wheatbelt / inland ---
    ("Northam", -31.6530, 116.6720),
    ("York", -31.8880, 116.7660),
    ("Merredin", -31.4820, 118.2790),
    ("Narrogin", -32.9360, 117.1780),
    ("Katanning", -33.6910, 117.5550),
    # --- Regional: goldfields ---
    ("Kalgoorlie", -30.7490, 121.4660),
    ("Coolgardie", -30.9540, 121.1640),
    ("Norseman", -32.1970, 121.7780),
    # --- Regional: south-west ---
    ("Harvey", -33.0790, 115.8960),
    ("Bunbury", -33.3270, 115.6410),
    ("Busselton", -33.6550, 115.3490),
    ("Margaret River", -33.9550, 115.0760),
    ("Collie", -33.3620, 116.1560),
    ("Manjimup", -34.2410, 116.1460),
    # --- Regional: south coast (toward Esperance) ---
    ("Albany", -35.0270, 117.8840),
    ("Mount Barker", -34.6300, 117.6660),
    ("Ravensthorpe", -33.5820, 120.0480),
    ("Esperance", -33.8610, 121.8910),
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


def _parse_stations_json(data):
    """Parse the FuelWatch /api/sites JSON response into the station format the
    rest of the app uses. Each entry has siteName, address{line1,location,
    latitude,longitude}, product{priceToday}, brandName. Stations with no price
    today or no coordinates are skipped."""
    out = []
    for s in data:
        try:
            addr = s.get("address") or {}
            prod = s.get("product") or {}
            price = prod.get("priceToday")
            lat = addr.get("latitude")
            lng = addr.get("longitude")
            if price is None or lat is None or lng is None:
                continue
            price = float(price)
            lat = float(lat)
            lng = float(lng)
        except (ValueError, TypeError):
            continue
        line1 = (addr.get("line1") or "").strip()
        suburb = (addr.get("location") or "").strip()
        full_addr = f"{line1}, {suburb}".strip(", ") if line1 or suburb else ""
        out.append({
            "name": (s.get("siteName") or "").strip(),
            "brand": (s.get("brandName") or "").strip(),
            "address": full_addr,
            "suburb": suburb.title(),
            "price": price,
            "lat": lat,
            "lng": lng,
        })
    return out


# ---------------------------------------------------------------------------
# Daily price cache.
# FuelWatch publishes new prices once a day (around 2:30pm WA time), so there's
# no point re-fetching per request. We cache each (product, suburb) result in
# memory keyed by the current "FuelWatch day". The day rolls over at 2:30pm
# Perth time: before 2:30pm we're still on the previous published set, after
# 2:30pm we move to today's. When the key changes, the whole cache is dropped
# and rebuilt lazily as suburbs are requested.
#
# Note: this cache lives in memory, so a process restart (e.g. Render free-tier
# sleep) clears it and it rebuilds on next use. A keep-awake pinger keeps the
# instance warm so the cache effectively persists through the day. For true
# persistence across restarts, swap this for Cloudflare KV or a small store.
# ---------------------------------------------------------------------------
WA_TZ = timezone(timedelta(hours=8))  # Perth is UTC+8, no daylight saving
FUELWATCH_ROLLOVER_HOUR = 14
FUELWATCH_ROLLOVER_MIN = 30

_price_cache = {}          # {(day_str, product, suburb): [stations]}
_price_cache_day = None    # the day_str the cache currently holds


def _fuelwatch_day():
    """Return a string identifying the current FuelWatch price day (rolls at
    2:30pm WA time)."""
    now = datetime.now(WA_TZ)
    rollover = now.replace(hour=FUELWATCH_ROLLOVER_HOUR, minute=FUELWATCH_ROLLOVER_MIN,
                           second=0, microsecond=0)
    day = now if now >= rollover else now - timedelta(days=1)
    return day.strftime("%Y-%m-%d")


async def _fetch_all_stations(product):
    """Fetch ALL WA stations for a fuel product in a single call to FuelWatch's
    /api/sites endpoint, using the daily cache. The new API returns every
    station statewide for the given fuel type, so one call covers everything —
    endpoints then just filter by distance. Only calls FuelWatch when this
    product isn't already cached for the current FuelWatch day."""
    global _price_cache, _price_cache_day
    day = _fuelwatch_day()
    if day != _price_cache_day:
        # New FuelWatch day — drop the old cache.
        _price_cache = {}
        _price_cache_day = day
    key = (day, product, "all")
    if key in _price_cache:
        return _price_cache[key]
    fuel_type = PRODUCT_TO_FUELTYPE.get(product, "ULP")
    r = await _get(FUELWATCH, params={"fuelType": fuel_type})
    try:
        data = r.json()
    except Exception:
        data = []
    stations = _parse_stations_json(data if isinstance(data, list) else [])
    _price_cache[key] = stations
    return stations


# ---------------------------------------------------------------------------
# FuelWatch regions. This is the authoritative list of every area FuelWatch
# covers (62 regions spanning all metro sub-regions plus every regional town/
# shire in the scheme), with approximate centre coordinates. Querying by region
# returns ALL stations in that whole area in a single call, so region-based
# search gives complete coverage of FuelWatch's footprint far more efficiently
# than pooling many individual suburbs. Source: FuelWatch RSS region IDs.
# ---------------------------------------------------------------------------
FUELWATCH_REGIONS = [
    (1, "Boulder", -30.78, 121.49),
    (2, "Broome", -17.96, 122.24),
    (3, "Busselton (Townsite)", -33.65, 115.35),
    (4, "Carnarvon", -24.88, 113.66),
    (5, "Collie", -33.36, 116.16),
    (6, "Dampier", -20.66, 116.71),
    (7, "Esperance", -33.86, 121.89),
    (8, "Kalgoorlie", -30.75, 121.47),
    (9, "Karratha", -20.74, 116.85),
    (10, "Kununurra", -15.77, 128.74),
    (11, "Narrogin", -32.94, 117.18),
    (12, "Northam", -31.65, 116.67),
    (13, "Port Hedland", -20.31, 118.61),
    (14, "South Hedland", -20.41, 118.60),
    (15, "Albany", -35.03, 117.88),
    (16, "Bunbury", -33.33, 115.64),
    (17, "Geraldton", -28.77, 114.61),
    (18, "Mandurah", -32.53, 115.72),
    (19, "Capel", -33.56, 115.57),
    (20, "Dardanup", -33.40, 115.76),
    (21, "Greenough", -28.95, 114.74),
    (22, "Harvey", -33.08, 115.90),
    (23, "Murray", -32.65, 115.88),
    (24, "Waroona", -32.84, 115.92),
    (25, "Metro : North of River", -31.90, 115.83),
    (26, "Metro : South of River", -32.05, 115.86),
    (27, "Metro : East/Hills", -31.92, 116.05),
    (28, "Augusta / Margaret River", -33.95, 115.07),
    (29, "Busselton (Shire)", -33.65, 115.35),
    (30, "Bridgetown / Greenbushes", -33.96, 116.14),
    (31, "Donnybrook / Balingup", -33.57, 115.82),
    (32, "Manjimup", -34.24, 116.15),
    (33, "Cataby", -30.75, 115.55),
    (34, "Coolgardie", -30.95, 121.16),
    (35, "Cunderdin", -31.65, 117.24),
    (36, "Dalwallinu", -30.28, 116.66),
    (37, "Denmark", -34.96, 117.35),
    (38, "Derby", -17.30, 123.63),
    (39, "Dongara", -29.26, 114.93),
    (40, "Exmouth", -21.93, 114.13),
    (41, "Fitzroy Crossing", -18.19, 125.56),
    (42, "Jurien", -30.31, 115.04),
    (43, "Kambalda", -31.20, 121.66),
    (44, "Kellerberrin", -31.63, 117.72),
    (45, "Kojonup", -33.83, 117.15),
    (46, "Meekatharra", -26.59, 118.49),
    (47, "Moora", -30.64, 116.01),
    (48, "Mount Barker", -34.63, 117.67),
    (49, "Newman", -23.36, 119.73),
    (50, "Norseman", -32.20, 121.78),
    (51, "Ravensthorpe", -33.58, 120.05),
    (53, "Tammin", -31.64, 117.49),
    (54, "Williams", -33.02, 116.88),
    (55, "Wubin", -30.11, 116.63),
    (56, "York", -31.89, 116.77),
    (57, "Regans Ford", -30.98, 115.70),
    (58, "Meckering", -31.63, 117.01),
    (59, "Wundowie", -31.76, 116.38),
    (60, "North Bannister", -32.58, 116.46),
    (61, "Munglinup", -33.71, 120.79),
    (62, "Northam (Shire)", -31.65, 116.67),
    (63, "Bodallin", -31.36, 118.86),
]


async def _fetch_region(product, region_id):
    """Fetch all stations in a FuelWatch region, using the daily cache."""
    global _price_cache, _price_cache_day
    day = _fuelwatch_day()
    if day != _price_cache_day:
        _price_cache = {}
        _price_cache_day = day
    key = (day, product, f"region:{region_id}")
    if key in _price_cache:
        return _price_cache[key]
    r = await _get(FUELWATCH, params={"Product": product, "Region": region_id})
    stations = _parse_stations(r.text)
    _price_cache[key] = stations
    return stations


def _nearest_regions(lat, lng, n=3):
    """Return the n nearest FuelWatch regions to a point, closest first."""
    ranked = sorted(FUELWATCH_REGIONS, key=lambda r: _haversine(lat, lng, r[2], r[3]))
    return ranked[:n]


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


def _sample_route(coords, interval_km=25.0):
    """
    Given an ordered list of [lng, lat] route coordinates (GeoJSON order),
    return sample points roughly every interval_km along the path. Always
    includes the first and last points. Used to seed fuel searches along a
    route so we find stations near the whole trip, not just its endpoints.
    """
    if not coords:
        return []
    samples = [coords[0]]
    acc = 0.0
    for i in range(1, len(coords)):
        lng1, lat1 = coords[i - 1]
        lng2, lat2 = coords[i]
        acc += _haversine(lat1, lng1, lat2, lng2)
        if acc >= interval_km:
            samples.append(coords[i])
            acc = 0.0
    if coords[-1] not in samples:
        samples.append(coords[-1])
    return samples


def _dist_point_to_route(lat, lng, route_coords, step=8):
    """
    Approximate shortest straight-line distance (km) from a point to the route
    polyline, by checking route vertices (every `step`th, for speed). Good
    enough to estimate how far off-route a station sits. route_coords is
    [lng, lat] pairs.
    """
    best = float("inf")
    for i in range(0, len(route_coords), step):
        rlng, rlat = route_coords[i]
        d = _haversine(lat, lng, rlat, rlng)
        if d < best:
            best = d
    return best


@app.get("/api/geocode")
async def geocode(suburb: str):
    # Basic input sanity: reject empty or obviously non-place input.
    q = (suburb or "").strip()
    if len(q) < 2 or len(q) > 60:
        raise HTTPException(404, f'Please enter a valid WA suburb or town.')

    r = await _get(f"{NOMINATIM}/search",
                   params={
                       "format": "json",
                       "q": f"{q}, Western Australia, Australia",
                       "limit": 5,
                       "addressdetails": 1,
                   })
    data = r.json()
    if not data:
        raise HTTPException(404, f'Could not find "{q}" in WA. Try a nearby suburb or town.')

    # WA bounding box (approximate): lat -35.3..-13.5, lng 112.8..129.0.
    # Reject anything geocoded outside the state.
    def in_wa(lat, lng):
        return -35.4 <= lat <= -13.4 and 112.5 <= lng <= 129.2

    # Only accept results that are actual populated places / boundaries, not
    # arbitrary points of interest (which is how nonsense like a joke phrase can
    # sometimes latch onto an unrelated POI). Nominatim tags these via "class"
    # and "type"/"addresstype".
    ACCEPT_CLASSES = {"place", "boundary"}
    ACCEPT_TYPES = {
        "suburb", "town", "city", "village", "hamlet", "locality",
        "neighbourhood", "administrative", "municipality",
    }

    for cand in data:
        try:
            lat = float(cand["lat"]); lng = float(cand["lon"])
        except (KeyError, ValueError):
            continue
        if not in_wa(lat, lng):
            continue
        cls = cand.get("class", "")
        typ = cand.get("type", "")
        addrtype = cand.get("addresstype", "")
        if cls in ACCEPT_CLASSES or typ in ACCEPT_TYPES or addrtype in ACCEPT_TYPES:
            return {"lat": lat, "lng": lng, "name": cand.get("display_name", q).split(",")[0]}

    # Nothing matched as a real WA locality.
    raise HTTPException(404, f'"{q}" doesn\'t look like a WA suburb or town. Try a nearby one.')


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
    product: int = Query(1, ge=1, le=11),
    suburb: str = Query(...),
    lat: float | None = None,
    lng: float | None = None,
):
    """
    Fetch FuelWatch prices near a suburb. Uses the statewide /api/sites feed
    (one cached call returns every station for the fuel type), then sorts by
    distance from the supplied coordinates, or by price if none are given.
    """
    try:
        stations = await _fetch_all_stations(product)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"FuelWatch request failed: {e}")

    # Sort by distance from the user if we have coords, else by price.
    if lat is not None and lng is not None:
        stations = sorted(stations, key=lambda s: _haversine(lat, lng, s["lat"], s["lng"]))
    else:
        stations = sorted(stations, key=lambda s: s["price"])

    return {"stations": stations, "searchedSuburb": suburb, "fallbackUsed": False}


@app.get("/api/fuel-metro")
async def fuel_metro(
    product: int = Query(1, ge=1, le=11),
    lat: float = Query(..., ge=-35.4, le=-13.4),
    lng: float = Query(..., ge=112.5, le=129.2),
    radius_km: float = Query(20.0, gt=0, le=200),
):
    """
    Area-wide search across all of WA. Fetches every station for the fuel type
    in one cached call, annotates each with distance from the user, and returns
    those within radius_km (sorted nearest first). If nothing falls within the
    radius, returns the nearest few so the user isn't dead-ended — flagged so the
    frontend can note they're beyond the chosen radius.
    """
    try:
        merged = await _fetch_all_stations(product)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"FuelWatch request failed: {e}")

    # Annotate every station with distance from the user (work on copies so the
    # cached list isn't mutated).
    annotated = []
    for s in merged:
        annotated.append({**s, "distanceKm": round(_haversine(lat, lng, s["lat"], s["lng"]), 2)})
    annotated.sort(key=lambda s: s["distanceKm"])

    within = [s for s in annotated if s["distanceKm"] <= radius_km]

    beyond_radius = False
    if not within and annotated:
        within = annotated[:5]
        beyond_radius = True

    return {
        "stations": within,
        "radiusKm": radius_km,
        "totalFound": len(annotated),
        "withinRadius": len([s for s in annotated if s["distanceKm"] <= radius_km]),
        "beyondRadius": beyond_radius,
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


@app.get("/api/fuel-route")
async def fuel_route(
    product: int = Query(1, ge=1, le=11),
    from_: str = Query(..., alias="from"),
    to: str = Query(...),
    max_detour_km: float = Query(5.0, gt=0, le=50),
    fill_litres: float = Query(50.0, gt=0, le=500),
    l_per_100km: float = Query(8.0, gt=0, le=40),
):
    """
    Find the cheapest fuel *along a route* from origin to destination.

    Strategy: get the road geometry from OSRM, sample points every ~25km along
    it, pool FuelWatch stations near each sample point, then keep only stations
    within max_detour_km of the route line. Each surviving station is ranked by
    REAL cost for this trip: the pump cost of a fill, plus the cost of the extra
    driving to detour off the route and back (approx: off-route distance x2 x
    economy x price). This surfaces stations that are cheap AND barely off the
    highway, rather than cheap-but-far.

    Note: this makes many FuelWatch calls for long routes. Caching daily price
    data is the natural next optimisation to keep it fast.
    """
    f_lat, f_lng = (float(x) for x in from_.split(","))
    t_lat, t_lng = (float(x) for x in to.split(","))
    coords_param = f"{f_lng},{f_lat};{t_lng},{t_lat}"

    # 1. Get the route geometry (GeoJSON = [lng, lat] pairs).
    route_coords = []
    total_km = _haversine(f_lat, f_lng, t_lat, t_lng) * 1.35
    estimated = True
    try:
        r = await _get(
            f"{OSRM}/route/v1/driving/{coords_param}",
            params={"overview": "full", "geometries": "geojson"},
        )
        leg = r.json()["routes"][0]
        route_coords = leg["geometry"]["coordinates"]
        total_km = leg["distance"] / 1000
        estimated = False
    except (httpx.HTTPError, KeyError, IndexError):
        # Fallback: straight line between endpoints as a crude 2-point route.
        route_coords = [[f_lng, f_lat], [t_lng, t_lat]]

    # 2. Sample points along the route (used only for the detour calc below).
    samples = _sample_route(route_coords, interval_km=25.0)

    # 3. Fetch every WA station for this fuel in one cached call, then keep only
    #    those close to the route.
    try:
        merged = await _fetch_all_stations(product)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"FuelWatch request failed: {e}")

    # 4. Keep only stations within max_detour_km of the route, and compute the
    #    real trip cost for each.
    on_route = []
    for s in merged:
        off = _dist_point_to_route(s["lat"], s["lng"], route_coords)
        if off > max_detour_km:
            continue
        # Detour: off-route distance, there and back, with a road factor.
        detour_km = off * 2 * 1.3
        fuel_cost = (s["price"] / 100.0) * fill_litres  # price is in cents/L
        detour_cost = (detour_km * (l_per_100km / 100.0)) * (s["price"] / 100.0)
        real_cost = fuel_cost + detour_cost
        on_route.append({
            **s,
            "offRouteKm": round(off, 2),
            "detourKm": round(detour_km, 2),
            "fillCost": round(fuel_cost, 2),
            "detourCost": round(detour_cost, 2),
            "realCost": round(real_cost, 2),
        })

    on_route.sort(key=lambda s: s["realCost"])

    return {
        "stations": on_route[:15],
        "totalFound": len(on_route),
        "routeKm": round(total_km, 1),
        "samplePoints": len(samples),
        "stationsScanned": len(merged),
        "maxDetourKm": max_detour_km,
        "estimatedRoute": estimated,
    }


@app.get("/api/vehicle/makes")
def vehicle_makes():
    """All makes, sorted."""
    return sorted({v["make"] for v in VEHICLES})


@app.get("/api/vehicle/models")
def vehicle_models(make: str = Query(...)):
    """Models for a make, sorted."""
    return sorted({v["model"] for v in VEHICLES if v["make"] == make})


@app.get("/api/vehicle/years")
def vehicle_years(make: str = Query(...), model: str = Query(...)):
    """Years for a make+model, newest first."""
    return sorted(
        {v["year"] for v in VEHICLES if v["make"] == make and v["model"] == model},
        reverse=True,
    )


@app.get("/api/vehicle/variants")
def vehicle_variants(
    make: str = Query(...),
    model: str = Query(...),
    year: int = Query(...),
    fuel_category: str = Query(None),
):
    """
    Variants for a specific make+model+year, each with its combined L/100km.
    If fuel_category is given (e.g. "Diesel" or "Petrol"), only variants of that
    category are used for the min/max/avg summary — so a diesel search doesn't
    average in petrol variants. Petrol category also includes Hybrid, since
    hybrids run on petrol. All variants are still returned for display.
    """
    matches = [
        v for v in VEHICLES
        if v["make"] == make and v["model"] == model and v["year"] == year
    ]
    if not matches:
        raise HTTPException(404, "No data for that vehicle.")

    # Decide which variants feed the economy summary.
    def in_category(vft):
        if not fuel_category:
            return True
        cat = fuel_category.lower()
        vft = vft.lower()
        if cat == "diesel":
            return "diesel" in vft
        if cat == "petrol":
            # Petrol-family fuels (ULP/PULP/98) power petrol and hybrid cars.
            return vft in ("petrol", "hybrid")
        return cat in vft

    summary_variants = [v for v in matches if in_category(v["fuel_type"])]
    values = [v["combined_l100km"] for v in summary_variants if v["combined_l100km"] > 0]
    summary = None
    if values:
        summary = {
            "min": min(values),
            "max": max(values),
            "avg": round(sum(values) / len(values), 1),
            "basedOn": fuel_category or "all fuel types",
            "variantCount": len(values),
        }
    return {
        "variants": [
            {"variant": v["variant"], "fuelType": v["fuel_type"],
             "combined": v["combined_l100km"]}
            for v in matches
        ],
        "summary": summary,
    }


@app.get("/api/cache-status")
def cache_status():
    """Report cache state. Also handy as a lightweight keep-warm ping target."""
    return {
        "fuelwatchDay": _fuelwatch_day(),
        "cachedDay": _price_cache_day,
        "cachedEntries": len(_price_cache),
    }


@app.get("/")
def health():
    return {"status": "ok", "service": "FuelFinder WA API"}
