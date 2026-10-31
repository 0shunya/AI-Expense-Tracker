# Deployment

Target architecture (the simplest one that fits the code as written):

| Layer | Service | Why |
|---|---|---|
| Frontend | **Vercel** (static Vite build) | Free Hobby plan for personal projects, SPA rewrites via `frontend/vercel.json` |
| API + extraction worker | **Render** web service (Docker, `render.yaml`) | Runs `backend/Dockerfile`; the worker runs as a thread inside the API (`WORKER_MODE=inline`), so one service is enough |
| Database | **Neon** PostgreSQL (or any managed Postgres) | Migrations run on every boot; receipt originals are stored in Postgres (`STORAGE_BACKEND=database`) |
| AI | **Google Gemini API** via AI Studio key | Called only by the backend |

> Status: this repository is **deployment-ready but not deployed by me** — no live URL exists yet. The
> commands below were verified locally against the production Docker image and a fresh PostgreSQL database
> (see "What has been verified"). Provider plans and limits change; check them before you rely on a free tier.

---

## 1. Accounts you need

1. **Google AI Studio** — create an API key at <https://aistudio.google.com/apikey>.
   *Free tier caveats:* low per-minute request limits (the evaluation run hit `429` at 3 parallel calls), and
   Google's terms for the free tier allow inputs to be used to improve its products. Do not send real personal
   receipts through a free-tier key; enable billing (and set a budget alert) for anything beyond a demo.
2. **Neon** (<https://neon.tech>) — free plan with a small storage quota, compute that scales to zero. Any
   PostgreSQL 14+ works (Render Postgres, Supabase, Railway). Render's free Postgres instances have historically
   expired after a fixed period, which is why Neon is recommended here.
3. **Render** (<https://render.com>) — free web services spin down after ~15 minutes idle and take up to about
   a minute to wake. Jobs are stored in Postgres, so a receipt uploaded just before a spin-down is processed
   when the service wakes again.
4. **Vercel** (<https://vercel.com>) — Hobby plan for the frontend.
5. A **GitHub** repository containing this project (Render and Vercel deploy from Git).

## 2. Environment variables

### Backend (Render → service → Environment)

| Variable | Required | Example / default | Notes |
|---|---|---|---|
| `APP_ENV` | yes | `production` | Disables `/docs`, enables JSON logs |
| `DATABASE_URL` | yes | `postgresql://user:pass@ep-xxx.neon.tech/neondb?sslmode=require` | `postgres://` and `postgresql://` are rewritten to the psycopg driver |
| `GEMINI_API_KEY` | yes | — | Secret. Never put it in the frontend |
| `GEMINI_MODEL` | no | `gemini-3.5-flash-lite` | Any vision model your key can use (`/api/health` shows the active one) |
| `FRONTEND_ORIGIN` | yes | `https://your-app.vercel.app` | Exact origin(s), comma-separated, no trailing slash. Drives CORS |
| `DEMO_ACCESS_CODE` | recommended | a long random string | When set, every API call needs the code (entered once in the browser) |
| `STORAGE_BACKEND` | no | `database` | Keep `database` on Render: its disk is wiped on every deploy |
| `WORKER_MODE` | no | `inline` | `off` only if you run `python -m app.worker` as a separate service |
| `DAILY_MODEL_CALL_LIMIT` | no | `15` | Hard stop on Gemini calls per UTC day (wallet guard) |
| `UPLOADS_PER_HOUR_PER_CLIENT` | no | `20` | Per-IP upload rate limit (in-process) |
| `MAX_UPLOAD_MB` / `MAX_PDF_PAGES` | no | `10` / `5` | Upload limits |
| `AUTO_APPROVE_CLEAN_RECEIPTS` | no | `false` | `true` = receipts with zero flags skip review (labelled "Auto-approved") |
| `EXTRACTION_PASSES` | no | `1` | `2` = two independent reads; disagreements are flagged (doubles cost) |

### Frontend (Vercel → Project → Settings → Environment Variables)

| Variable | Value |
|---|---|
| `VITE_API_BASE_URL` | `https://<your-render-service>.onrender.com` (no trailing slash) |

`VITE_*` values are compiled into public JavaScript. Only the API URL goes here; no secret is ever needed
by the frontend.

## 3. Provision the database (Neon)

1. Create a project → copy the **pooled or direct connection string** (it includes `sslmode=require`).
2. Nothing else: the schema is created by Alembic when the API boots (`backend/start.sh` runs
   `alembic upgrade head` before starting the server). To run migrations yourself:

```bash
cd backend
DATABASE_URL="postgresql://...neon.tech/neondb?sslmode=require" .venv/bin/python -m alembic upgrade head
```

## 4. Deploy the backend (Render)

**Option A — Blueprint (recommended):** Render dashboard → **New → Blueprint** → select the repository.
Render reads `render.yaml` (Docker runtime, root `backend/`, health check `/api/health`, free plan). Fill in the
variables marked `sync: false` (`DATABASE_URL`, `GEMINI_API_KEY`, `FRONTEND_ORIGIN`, `DEMO_ACCESS_CODE`).

**Option B — manual:** New → Web Service → repository → *Root Directory* `backend`, *Runtime* Docker,
*Health Check Path* `/api/health`, then add the variables from section 2.

Start command is the image's `CMD` (`./start.sh`): migrate, then `uvicorn` on `$PORT` with proxy headers.

When the deploy is live, open `https://<service>.onrender.com/api/health`. Expected:

```json
{"status": "ok", "database": "ok", "environment": "production", "gemini_configured": true, "model": "gemini-3.5-flash-lite", ...}
```

`database: "unavailable"` (HTTP 503) means `DATABASE_URL` is wrong or the database is unreachable.

## 5. Deploy the frontend (Vercel)

1. Vercel → **Add New → Project** → import the repository.
2. **Root Directory:** `frontend`. Framework preset: Vite (also declared in `frontend/vercel.json`).
3. Build command `npm run build`, output `dist` (defaults).
4. Add `VITE_API_BASE_URL` = your Render URL. Deploy.
5. Copy the Vercel production URL and set it as `FRONTEND_ORIGIN` on Render (then redeploy Render, or use
   *Manual Deploy → Deploy latest commit*, so the new value is loaded).

Preview deployments get different URLs; add them to `FRONTEND_ORIGIN` (comma-separated) if you want them to
call the API.

## 6. CORS

The API only allows the origins in `FRONTEND_ORIGIN`, with no credentials and only the headers the app uses.
A request from any other origin gets no `Access-Control-Allow-Origin` header and the browser blocks it.
The server logs a warning if `APP_ENV=production` and an origin contains `localhost`.

## 7. File storage

Render's filesystem is ephemeral: anything written to disk disappears on the next deploy or restart. Receipt
originals are therefore stored in the `stored_files` table (`STORAGE_BACKEND=database`), which survives
redeploys with no extra service or cost. Trade-off: database storage is more expensive per GB than object
storage and a free Neon project is small, which is why uploads are capped (`MAX_UPLOAD_MB=10`). For larger
volumes, add an S3-compatible backend (e.g. Cloudflare R2) behind the `Storage` protocol in
`backend/app/services/storage.py`; it is not implemented in this version.

## 8. Smoke test after deploying

```bash
API=https://<service>.onrender.com
CODE=<your DEMO_ACCESS_CODE>
curl -s $API/api/health
curl -s -H "X-Access-Code: $CODE" -F "file=@evaluation/dataset/files/r001.jpg" $API/api/receipts
# -> 202 {"receipt": {"id": "...", "status": "queued"}, ...}
curl -s -H "X-Access-Code: $CODE" $API/api/receipts/<id>      # after a few seconds: "needs_review"
curl -s -H "X-Access-Code: $CODE" $API/api/model-calls        # one "success" row with token counts
```

Then in the browser: open the Vercel URL → enter the access code → Upload → open the receipt → approve →
check that it appears under Expenses and on the Dashboard.

## 9. Common errors

| Symptom | Cause | Fix |
|---|---|---|
| Browser console: *blocked by CORS policy* | `FRONTEND_ORIGIN` does not exactly match the Vercel origin | Set it to `https://name.vercel.app` (scheme, no trailing slash) and redeploy the API |
| Frontend shows "Cannot reach the server" | `VITE_API_BASE_URL` missing/wrong at build time, or API asleep | Fix the variable and **redeploy** Vercel (it is baked in at build); wait for Render to wake |
| `/api/health` → 503 `database: unavailable` | Bad `DATABASE_URL` or SSL requirement | Use the provider's full URL including `?sslmode=require` |
| Deploy fails at `alembic upgrade head` | Database unreachable at boot | Same as above; check Render logs |
| Receipts go to *Failed* with `config_missing` | `GEMINI_API_KEY` not set | Add it on Render and redeploy |
| `auth_error` | Key revoked or wrong | Create a new key in AI Studio |
| `model_not_found` | `GEMINI_MODEL` not available to the key | Use a model listed by the API for your key |
| `rate_limited` | Free-tier per-minute or daily quota | Wait, lower usage, or enable billing |
| `daily_limit_reached` | `DAILY_MODEL_CALL_LIMIT` hit | Intended wallet guard; raise it deliberately |
| Every page asks for an access code | `DEMO_ACCESS_CODE` is set (by design) | Enter it; it is stored in the browser's localStorage |
| 404 on refresh of `/receipts/...` on Vercel | SPA rewrites missing | Keep `frontend/vercel.json` and Root Directory `frontend` |

## 10. Redeploying

* **Code change:** push to the default branch. Render (`autoDeploy: true`) rebuilds the image and runs pending
  migrations on boot; Vercel rebuilds the frontend.
* **Schema change:** add an Alembic revision (`alembic revision --autogenerate -m "..."`, then review it), commit it,
  push. Migrations are applied on the next boot; run `alembic upgrade head` locally first against a copy.
* **Environment change:** Render redeploys automatically when variables change; Vercel needs a redeploy because
  `VITE_*` values are compiled in.
* **Rollback:** Render → Deploys → *Rollback* to a previous image. Do not roll back across a migration that
  dropped data; this project's single migration only creates tables.

## What has been verified locally

* `docker build backend` succeeds; the image runs as a non-root user.
* Against a brand-new PostgreSQL database with a `postgres://` URL, the container applied migration `0001`,
  started, and `/api/health` returned `status: ok`, `environment: production`.
* `/docs` returns 404 in production; a preflight from a non-allowed origin receives no CORS allow header, and the
  configured origin is allowed.
* `npm run build` produces the static frontend; it contains no secrets (only `VITE_API_BASE_URL`).

Not verified: an actual Render/Vercel/Neon deployment (requires your accounts).
