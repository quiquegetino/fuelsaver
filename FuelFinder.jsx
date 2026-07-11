import React, { useState, useCallback } from "react";

// ---------------------------------------------------------------------------
// FuelFinder WA — frontend. Talks ONLY to the local backend (see backend/main.py),
// which handles FuelWatch, geocoding, and routing server-side. No CORS proxies.
// Set VITE_API_BASE in a .env file to point at a deployed backend.
// ---------------------------------------------------------------------------

const API = import.meta.env.VITE_API_BASE || "http://localhost:8000";

const FUEL_PRODUCTS = [
  { code: 1, label: "Unleaded (ULP)" },
  { code: 2, label: "Premium Unleaded (PULP 95)" },
  { code: 3, label: "Diesel" },
  { code: 4, label: "LPG" },
  { code: 5, label: "98 RON (PULP 98)" },
  { code: 10, label: "E10" },
];

const ECONOMY_DEFAULTS = {
  1: { lPer100: 8.5 }, 2: { lPer100: 8.5 }, 3: { lPer100: 6.5 },
  4: { lPer100: 11.0 }, 5: { lPer100: 8.5 }, 10: { lPer100: 8.7 },
};

async function api(path, params) {
  const qs = new URLSearchParams(params).toString();
  const res = await fetch(`${API}${path}?${qs}`);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Request failed (${res.status}).`);
  }
  return res.json();
}

function fmtMoney(n) { return "$" + n.toFixed(2); }
function fmtMin(n) { return Math.round(n) + " min"; }

export default function FuelFinder() {
  const [origin, setOrigin] = useState(null);
  const [suburb, setSuburb] = useState("");
  const [product, setProduct] = useState(1);
  const [lPer100, setLPer100] = useState(8.5);
  const [fillLitres, setFillLitres] = useState(45);
  const [vehicle, setVehicle] = useState({ make: "", model: "", year: "" });
  const [loading, setLoading] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  const onProductChange = (code) => {
    setProduct(code);
    if (ECONOMY_DEFAULTS[code]) setLPer100(ECONOMY_DEFAULTS[code].lPer100);
  };

  const locate = useCallback(() => {
    setError(""); setLoading("Finding your location…");
    if (!navigator.geolocation) {
      setError("Geolocation isn't available in this browser."); setLoading(""); return;
    }
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const o = { lat: pos.coords.latitude, lng: pos.coords.longitude };
        setOrigin(o);
        try {
          setLoading("Looking up your suburb…");
          const { suburb: sub } = await api("/api/reverse", { lat: o.lat, lng: o.lng });
          setSuburb(sub); setLoading("");
        } catch (e) { setError(e.message); setLoading(""); }
      },
      () => { setError("Location permission denied or unavailable."); setLoading(""); },
      { enableHighAccuracy: true, timeout: 10000 }
    );
  }, []);

  const compare = useCallback(async () => {
    setError(""); setResult(null);
    if (!suburb) { setError("Enter a suburb, or tap \u201CUse my location\u201D."); return; }
    try {
      let from = origin;
      if (!from) {
        setLoading("Locating that suburb…");
        from = await api("/api/geocode", { suburb });
        setOrigin(from);
      }

      setLoading("Fetching FuelWatch prices…");
      const stations = await api("/api/fuel", { product, suburb });
      if (!stations.length) {
        setError("No stations returned for that suburb / fuel type."); setLoading(""); return;
      }

      setLoading("Calculating routes…");
      const straight = (s) => {
        const dLat = s.lat - from.lat, dLng = s.lng - from.lng;
        return dLat * dLat + dLng * dLng;
      };
      const byNear = [...stations].sort((a, b) => straight(a) - straight(b));
      const cheapestRaw = [...stations].sort((a, b) => a.price - b.price)[0];
      const candidates = Array.from(new Set([...byNear.slice(0, 5), cheapestRaw]));

      const routed = await Promise.all(candidates.map(async (s) => ({
        ...s,
        route: await api("/api/route", {
          from: `${from.lat},${from.lng}`, to: `${s.lat},${s.lng}`,
        }),
      })));

      const closest = [...routed].sort((a, b) => a.route.distanceKm - b.route.distanceKm)[0];
      const cheapest = [...routed].sort((a, b) =>
        a.price !== b.price ? a.price - b.price : a.route.distanceKm - b.route.distanceKm
      )[0];

      const priceDiffCents = closest.price - cheapest.price;
      const fuelSaved = (priceDiffCents / 100) * fillLitres;
      const extraKm = 2 * Math.max(0, cheapest.route.distanceKm - closest.route.distanceKm);
      const extraLitres = (extraKm / 100) * lPer100;
      const extraFuelCost = (cheapest.price / 100) * extraLitres;
      const netSaving = fuelSaved - extraFuelCost;
      const extraTimeMin = 2 * Math.max(0, cheapest.route.durationMin - closest.route.durationMin);

      setResult({
        closest, cheapest, same: closest.name === cheapest.name,
        priceDiffCents, fuelSaved, extraKm, extraFuelCost, netSaving,
        extraTimeMin, count: stations.length,
      });
      setLoading("");
    } catch (e) { setError(e.message || "Something went wrong."); setLoading(""); }
  }, [origin, suburb, product, fillLitres, lPer100]);

  return (
    <div className="ff">
      <style>{css}</style>
      <header className="ff-head">
        <h1>FuelFinder WA</h1>
        <p>Is the cheapest pump actually worth the drive?</p>
      </header>

      <section className="ff-card">
        <div className="ff-step">
          <span className="ff-num">1</span>
          <div className="ff-step-body">
            <button className="ff-btn" onClick={locate}>Use my location</button>
            <input className="ff-input" placeholder="Suburb (e.g. Bayswater)"
              value={suburb} onChange={(e) => setSuburb(e.target.value)} />
            {origin && <span className="ff-coords">{origin.lat.toFixed(4)}, {origin.lng.toFixed(4)}</span>}
          </div>
        </div>

        <div className="ff-step">
          <span className="ff-num">2</span>
          <div className="ff-step-body ff-grid">
            <label>Fuel type
              <select value={product} onChange={(e) => onProductChange(Number(e.target.value))}>
                {FUEL_PRODUCTS.map((f) => <option key={f.code} value={f.code}>{f.label}</option>)}
              </select>
            </label>
            <label>Litres this fill
              <input type="number" value={fillLitres} min={1}
                onChange={(e) => setFillLitres(Number(e.target.value))} />
            </label>
            <label>Economy (L/100km)
              <input type="number" step="0.1" value={lPer100}
                onChange={(e) => setLPer100(Number(e.target.value))} />
            </label>
          </div>
        </div>

        <div className="ff-step">
          <span className="ff-num">3</span>
          <div className="ff-step-body ff-grid">
            <label>Make
              <input value={vehicle.make} placeholder="Toyota"
                onChange={(e) => setVehicle({ ...vehicle, make: e.target.value })} />
            </label>
            <label>Model
              <input value={vehicle.model} placeholder="Corolla"
                onChange={(e) => setVehicle({ ...vehicle, model: e.target.value })} />
            </label>
            <label>Year
              <input value={vehicle.year} placeholder="2020"
                onChange={(e) => setVehicle({ ...vehicle, year: e.target.value })} />
            </label>
          </div>
          <p className="ff-hint">Make/model/year are captured for a future economy lookup — for now the L/100km field drives the maths.</p>
        </div>

        <button className="ff-btn ff-primary" onClick={compare}>Compare cheapest vs closest</button>
        {loading && <p className="ff-status">{loading}</p>}
        {error && <p className="ff-error">{error}</p>}
      </section>

      {result && (
        <section className="ff-card ff-result">
          {result.same ? (
            <p className="ff-verdict">Your closest station is also the cheapest — just go there.</p>
          ) : result.netSaving > 0 ? (
            <p className="ff-verdict ff-good">Driving to {result.cheapest.name} saves <strong>{fmtMoney(result.netSaving)}</strong> net, costing <strong>{fmtMin(result.extraTimeMin)}</strong> extra.</p>
          ) : (
            <p className="ff-verdict ff-bad">Not worth it — the detour costs more in fuel than you'd save. Stick with {result.closest.name}.</p>
          )}
          <div className="ff-compare">
            <StationCard title="Closest" s={result.closest} />
            <StationCard title="Cheapest" s={result.cheapest} />
          </div>
          <table className="ff-table"><tbody>
            <tr><td>Price difference</td><td>{result.priceDiffCents.toFixed(1)} c/L</td></tr>
            <tr><td>Saved on {fillLitres} L fill</td><td>{fmtMoney(result.fuelSaved)}</td></tr>
            <tr><td>Extra distance (round trip)</td><td>{result.extraKm.toFixed(1)} km</td></tr>
            <tr><td>Extra fuel burned</td><td>{fmtMoney(result.extraFuelCost)}</td></tr>
            <tr className="ff-net"><td>Net saving</td><td>{fmtMoney(result.netSaving)}</td></tr>
            <tr><td>Extra time (round trip)</td><td>{fmtMin(result.extraTimeMin)}</td></tr>
          </tbody></table>
          <p className="ff-hint">Compared {result.count} stations near {suburb}. Routes via OSRM.</p>
        </section>
      )}
    </div>
  );
}

function StationCard({ title, s }) {
  return (
    <div className="ff-station">
      <span className="ff-station-tag">{title}</span>
      <h3>{s.name}</h3>
      <p className="ff-price">{s.price.toFixed(1)}c</p>
      <p className="ff-addr">{s.address}, {s.suburb}</p>
      <p className="ff-dist">{s.route.distanceKm.toFixed(1)} km · {fmtMin(s.route.durationMin)}</p>
    </div>
  );
}

const css = `
.ff { max-width: 680px; margin: 0 auto; font-family: system-ui, sans-serif; color: #1a1a1a; }
.ff-head h1 { font-size: 24px; font-weight: 600; margin: 0; }
.ff-head p { color: #666; margin: 4px 0 20px; }
.ff-card { background: #fff; border: 1px solid #e5e5e5; border-radius: 12px; padding: 20px; margin-bottom: 16px; }
.ff-step { display: flex; gap: 12px; margin-bottom: 18px; }
.ff-num { flex: none; width: 24px; height: 24px; border-radius: 50%; background: #1a5fa5; color: #fff; display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 600; }
.ff-step-body { flex: 1; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.ff-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); }
.ff-step-body label { display: flex; flex-direction: column; font-size: 12px; color: #666; gap: 4px; }
.ff-input, .ff-step-body input, .ff-step-body select { height: 36px; padding: 0 10px; border: 1px solid #ccc; border-radius: 8px; font-size: 14px; }
.ff-btn { height: 36px; padding: 0 16px; border: 1px solid #1a5fa5; background: #fff; color: #1a5fa5; border-radius: 8px; font-size: 14px; font-weight: 500; cursor: pointer; }
.ff-btn:hover { background: #f0f6fc; }
.ff-primary { background: #1a5fa5; color: #fff; width: 100%; margin-top: 4px; }
.ff-primary:hover { background: #14507f; }
.ff-coords { font-size: 12px; color: #888; font-family: monospace; }
.ff-hint { font-size: 12px; color: #999; margin: 8px 0 0; }
.ff-status { color: #1a5fa5; font-size: 14px; margin: 12px 0 0; }
.ff-error { color: #c0392b; font-size: 14px; margin: 12px 0 0; }
.ff-verdict { font-size: 16px; margin: 0 0 16px; padding: 12px; border-radius: 8px; background: #f5f5f5; }
.ff-good { background: #eafaf1; color: #1d6e4f; }
.ff-bad { background: #fdecea; color: #a32d2d; }
.ff-compare { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px; }
.ff-station { border: 1px solid #e5e5e5; border-radius: 8px; padding: 12px; }
.ff-station-tag { font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: #888; }
.ff-station h3 { font-size: 15px; margin: 4px 0; }
.ff-price { font-size: 22px; font-weight: 600; margin: 4px 0; }
.ff-addr { font-size: 12px; color: #666; margin: 2px 0; }
.ff-dist { font-size: 13px; color: #1a5fa5; margin: 4px 0 0; }
.ff-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.ff-table td { padding: 8px 0; border-bottom: 1px solid #f0f0f0; }
.ff-table td:last-child { text-align: right; font-variant-numeric: tabular-nums; }
.ff-net td { font-weight: 600; }
`;
