# FuelSave WA

A web app that helps drivers in Western Australia find the cheapest fuel near them — and works out whether the detour to a cheaper station is actually worth it once you factor in the extra distance and your vehicle's fuel economy.

Live at **[fuelsavewa.com](https://fuelsavewa.com)**.

## What it does

1. Detects your location (or you enter a suburb).
2. Pulls current fuel prices from **FuelWatch WA**.
3. Asks your fuel type and, optionally, your car (make → model → year) to estimate consumption.
4. Finds the **cheapest** station within your chosen radius and the **closest** station.
5. Calculates the real saving: price difference × fill volume, minus the cost of the extra distance to reach the cheaper station. If the detour costs more than it saves, it tells you it's not worth it.

Coverage spans the full Perth metro area plus regional WA — from Geraldton in the north to Esperance in the south, and the wheatbelt and goldfields in between.

## How it's built

The app is two pieces because FuelWatch, geocoding and routing services don't send CORS headers, so a browser can't call them directly.

- **Backend** — FastAPI (Python), in `backend/main.py`. Makes the FuelWatch, geocoding (Nominatim) and routing (OSRM) calls server-side. Deployed on Render.
- **Frontend** — a single self-contained `index.html` (React via CDN). Deployed as a Cloudflare Worker.

The API base URL auto-switches: `localhost:8000` in development, the Render URL in production.

### Key endpoints

- `GET /api/geocode`, `GET /api/reverse` — suburb/coordinate lookup
- `GET /api/fuel`, `GET /api/fuel-metro` — fuel prices (per-suburb and area-wide)
- `GET /api/route` — road distance between two points
- `GET /api/vehicle/makes|models|years|variants` — vehicle economy lookup

## Vehicle data

`backend/data/vehicles.csv` holds the fuel-economy figures used to estimate consumption. Columns:

```
make,model,year,variant,fuel_type,combined_l100km
```

The dataset is engine-variant based (not trim levels) and combines:

- **Australian data** from the Green Vehicle Guide (manufacturer-certified combined L/100km, 2004+), which takes priority for Australian-market models.
- **Canadian open data** from Natural Resources Canada (Open Government Licence), filtered to Australian-market makes, covering **1995–2026** and filling gaps the Australian export missed. Note: 1995–2014 Canadian figures are approximate 5-cycle-adjusted values.
- A curated list of common Australian vehicles (including popular utes across their model generations) to guarantee coverage of high-selling cars.

Fuel-type note: 91/95/98 RON all map to the "Petrol" economy category (octane affects knock resistance, not consumption — the RON choice matters for the price search, not the car's figure). LPG has no manufacturer economy data, so the app asks the user to enter their own L/100km and shows a note explaining why.

## Running locally

Backend:

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Frontend: open `index.html` (it points at `localhost:8000` when run locally).

## Deploying

- **Backend (Render):** push to the connected repo; Render auto-redeploys. Root directory `backend`, start command `uvicorn main:app --host 0.0.0.0 --port $PORT`.
- **Frontend (Cloudflare Worker):** auto-redeploys on push. Hard-refresh (Ctrl+Shift+R) after a deploy to clear the cache.

Updating vehicle data is a data-only change: replace `backend/data/vehicles.csv`, commit and push — no code redeploy needed, though the push does trigger Render to reload.

## Data sources & disclaimer

Fuel prices are sourced from FuelWatch WA and update daily (tomorrow's prices appear after 2:30pm). Travel times and distances are estimates and don't account for traffic or roadworks. Vehicle consumption figures are manufacturer laboratory values (Green Vehicle Guide / NRCan) and will vary with driving style, load, and vehicle condition; older figures (pre-2015) use a different, less directly comparable test standard.

FuelSave WA is an independent tool and is not affiliated with FuelWatch, the Green Vehicle Guide, Natural Resources Canada, or any government body.