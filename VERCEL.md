# Vercel Hobby + Supabase Free

The app keeps its invite-only accounts, owner password and installable PWA.
Vercel runs one product search per authenticated request. Keep the app open while
searching; reopening it resumes queued work. No AI API is called by web searches.

## Database

Apply `backend/schema.sql` to your Supabase project once. Tables are in the private
`studio` schema, have RLS enabled, and grant no browser roles access. Do not expose
this schema in the Supabase Data API. Accounts use the existing application auth;
Supabase Auth is not required. Images use database space, not Storage buckets.

## Vercel settings

- Repository: `Siddique-SID/product-image-finder`; branch: `feat/product-finder-pwa`.
- Root directory: repository root. Framework: FastAPI.
- Remove stale Build Command or Output Directory overrides. The committed
  `pyproject.toml` builds the frontend and selects `backend.app:app`.
- Python 3.12 is recommended.
- Add these **server-only** variables to Production and Preview:
  - `DATABASE_URL`: Supabase Connect → Transaction pooler, port 6543. Replace the
    password placeholder with your percent-encoded database password and append
    `?sslmode=require`. Copy the actual host from Connect; don't infer it.
  - `APP_PASSWORD`: the existing owner password, entered directly in Vercel.
  - `SESSION_SECRET`: a stable random secret with at least 32 bytes of entropy.
  - `REQUIRE_AUTH`: `true` (Vercel also enforces this in code).
- Redeploy the current branch after saving variables. For a production release,
  set the project's production branch accordingly or promote the tested preview.

Vercel refuses to start without the database, owner password and session secret.
Uploads are capped at 4 MB to fit the platform request limit. Approved image ZIPs
are built in the browser from individual authenticated downloads, allowing larger
catalogue exports without a large server response. CSV/XLSX still use the API.

## Verification and rollback

Verify `/api/health` returns 200, `/api/session` reports `durable: true` and
`authenticated: false` before login, and `/api/jobs` returns 401. Then sign in,
upload the sample, search, approve, export, sign out and reopen the installed PWA.

The Render deployment and its temporary data are separate and are not copied or
deleted by this setup. Export any wanted Render work first. Roll back Vercel to its
previous deployment or continue using Render; leave the Supabase tables intact.
Free-tier quotas and inactivity rules still apply. Back up important catalogues.

For optional CLI AI/browser modes, install `requirements-cli.txt` separately.
