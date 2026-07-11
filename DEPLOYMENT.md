# FuelSaver WA — Deployment Guide

This gets your app online for **$0/month + ~$15/year** (domain only), using:

- **Backend** → Render.com (free tier)
- **Frontend** → Cloudflare Pages (free)
- **Domain** → your registrar (Cloudflare recommended, at-cost pricing)

The one tradeoff of "free": Render's free backend sleeps after ~15 min idle and
takes ~30 seconds to wake on the next request. Fine for a personal tool; upgrade
to Render's ~US$7/mo tier later if you want it always-on.

---

## Prerequisites (one-time)

1. **GitHub account** — free at github.com. Render and Cloudflare both deploy by
   watching a GitHub repo, so this is the delivery mechanism for your code.
2. **Git installed** on your PC — get it at git-scm.com (Windows: accept the
   defaults during install).

---

## Step 1 — Put the project on GitHub

Your project folder should contain:

```
fuelsaver/
├── index.html          (frontend)
├── render.yaml         (Render config)
├── .gitignore
├── DEPLOYMENT.md       (this file)
└── backend/
    ├── main.py
    └── requirements.txt
```

Open a terminal in the `fuelsaver` folder and run, one line at a time:

```
git init
git add .
git commit -m "Initial FuelSaver WA app"
```

Then create an empty repo on github.com (click **New**, name it `fuelsaver`,
leave everything unticked, click **Create repository**). GitHub shows you a URL
like `https://github.com/YOURNAME/fuelsaver.git`. Connect and push:

```
git remote add origin https://github.com/YOURNAME/fuelsaver.git
git branch -M main
git push -u origin main
```

Your code is now on GitHub.

---

## Step 2 — Deploy the backend to Render

1. Sign up at render.com with your GitHub account.
2. Click **New → Blueprint**. Render detects `render.yaml` automatically.
3. Select your `fuelsaver` repo → **Apply**. Render builds and deploys.
4. When done, you get a URL like `https://fuelsaver-api.onrender.com`.
5. Test it: open `https://fuelsaver-api.onrender.com/` — you should see
   `{"status":"ok"}`. (First load may take ~30s while it wakes.)

**Copy your real backend URL** — you need it in the next step.

---

## Step 3 — Point the frontend at the live backend

Open `index.html` and find this near the top (~line 55):

```javascript
    : "https://fuelsaver-api.onrender.com";
```

Replace that URL with YOUR actual Render URL from Step 2 (they may differ if the
name was taken). Save. Commit and push the change:

```
git add index.html
git commit -m "Point frontend at live backend"
git push
```

---

## Step 4 — Deploy the frontend to Cloudflare Pages

1. Sign up at dash.cloudflare.com (free).
2. **Workers & Pages → Create → Pages → Connect to Git.**
3. Pick your `fuelsaver` repo.
4. Build settings: leave build command **empty**, set output directory to `/`
   (root). Your app is a single static file, so there's nothing to build.
5. **Save and Deploy.** You get a URL like `fuelsaver.pages.dev`.

Open it — the app should load and work (give the backend a moment to wake).

---

## Step 5 — Connect your domain

1. Register your domain. Cheapest at-cost option: in the Cloudflare dashboard,
   **Domain Registration → Register Domains**. A `.com` is ~US$10/yr; a
   `.com.au` (more fitting for a WA tool) is ~AU$15–20/yr.
   - Note: `.com.au` requires an ABN or is restricted to AU entities. If you
     don't have one, a `.com` or `.net` works fine.
2. Once registered (and, if bought elsewhere, its nameservers pointed at
   Cloudflare), go to your Pages project → **Custom domains → Set up a custom
   domain** → enter your domain. Cloudflare wires up DNS and HTTPS automatically.

Your app is now live at your own domain with HTTPS.

---

## Updating the app later

Any change is just: edit the file, then

```
git add .
git commit -m "describe the change"
git push
```

Both Render and Cloudflare auto-redeploy on push. No manual uploads.

---

## Tightening security (optional, later)

- The backend currently allows all origins (`allow_origins=["*"]`). Once your
  domain is set, you can lock it to just your domain in `backend/main.py` for
  good hygiene — not urgent for a read-only public API.
- Consider caching FuelWatch responses (they change at most daily). The metro
  search makes ~25 calls per request; caching cuts that dramatically and is
  kinder to FuelWatch's servers if traffic grows.

---

## Mobile, when you're ready

The cleanest path from here is a **PWA** (Progressive Web App): a few small
additions let users "Add to Home Screen" and run it like an app — no app-store
process, works on both iOS and Android, reuses this exact code. If you later
want true native apps, **Capacitor** wraps this web app into installable
iOS/Android packages. Both are far less work than rebuilding natively, because
the app is already web-based. We can tackle this when you get there.
