# FuelFinder WA

Finds the cheapest fuel near you on [FuelWatch](https://www.fuelwatch.wa.gov.au/),
compares it against the closest station, and tells you whether the cheaper pump
is actually worth the drive — netting the fuel you'd burn and time you'd spend
getting there against the saving at the bowser.

## Why there's a backend

FuelWatch's RSS feed and the geocoding/routing services don't send CORS headers,
so a browser can't call them directly. Public CORS proxies don't solve it either:
they're now either locked to specific dev origins or blocked by FuelWatch itself.
The fix is a tiny backend that makes those calls server-side, where CORS doesn't
apply. The browser only ever talks to this backend.

```
Browser (React)  ->  Backend (FastAPI)  ->  FuelWatch RSS
                                         ->  Nominatim (geocoding)
                                         ->  OSRM (routing)
```

## Run it

### 1. Backend

```bash
cd backend
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Check it's up: open http://localhost:8000 — you should see `{"status":"ok"}`.
Try a live query: http://localhost:8000/api/fuel?product=1&suburb=Bayswater

### 2. Frontend

The `FuelFinder.jsx` component drops into any Vite + React app. Quick version:

```bash
npm create vite@latest fuelfinder-web -- --template react
cd fuelfinder-web
npm install
# copy FuelFinder.jsx into src/, then import it in App.jsx:
#   import FuelFinder from "./FuelFinder";
#   export default () => <FuelFinder />;
npm run dev
```

Open the Vite URL (usually http://localhost:5173). The component defaults to
`http://localhost:8000` for the API. To point at a deployed backend, add a
`.env` file with `VITE_API_BASE=https://your-backend.example.com`.

## API

| Endpoint | Params | Returns |
|---|---|---|
| `GET /api/geocode` | `suburb` | `{lat, lng}` |
| `GET /api/reverse` | `lat`, `lng` | `{suburb}` |
| `GET /api/fuel` | `product`, `suburb`, `surrounding` | `[{name, brand, address, suburb, price, lat, lng}]` |
| `GET /api/route` | `from=lat,lng`, `to=lat,lng` | `{distanceKm, durationMin}` |

FuelWatch product codes: 1 = ULP, 2 = PULP 95, 3 = Diesel, 4 = LPG, 5 = PULP 98, 10 = E10.

## How the savings maths works

- **Gross saving** = (closest price − cheapest price) × litres you're putting in
- **Detour cost** = extra round-trip distance to the cheaper station × your
  L/100km × the cheaper station's price
- **Net saving** = gross saving − detour cost

If net saving is negative, the app tells you to stay at the closest station.

## Known limitations / next steps

- **Vehicle economy**: make/model/year are collected but not yet used. The
  L/100km field drives the maths for now. A future step is mapping vehicle ->
  economy (a static dataset is more reliable than any free AU vehicle API).
- **Routing rate limits**: the public OSRM demo server is fine for testing but
  asks you not to hammer it. For production, self-host OSRM or use a keyed
  provider (Mapbox, Google, OpenRouteService).
- **Nominatim usage policy**: max 1 request/second and a valid User-Agent
  (already set). For volume, self-host or use a paid geocoder.
- **Caching**: FuelWatch prices change at most daily — cache the feed per
  suburb to cut requests and speed things up.
