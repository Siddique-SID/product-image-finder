# Catalogue Studio beta

## Owner and customers

The existing `APP_PASSWORD` still opens **Owner access**. In the owner workspace, choose **Invite user** to generate a single-use invitation that expires after seven days. Share the app address and invitation privately. Customers choose **Create account**, set their own password and save the recovery code. Invitations never reveal the owner password. Owner and customer libraries are isolated.

Recovery uses a one-time recovery code; there is no email delivery, email verification or payment integration. Recovery rotates the code and invalidates existing sessions. Do not market automated image matching as a guarantee or a licence to publish the images.

## Durable storage without changing the Render plan

The app supports PostgreSQL through `DATABASE_URL` and stores accounts, catalogue state and images in the database. Use an external database you control (a free provider is fine for a limited beta). In Render, add its SSL-enabled PostgreSQL connection URL as a secret environment variable. Use a dedicated database and a restricted database role. No database is provisioned and no paid services are added by this change.

Use your provider's recommended TLS connection settings, for example `sslmode=require`, and its connection pooler if the direct endpoint is unavailable from Render. Never commit the URL or paste it into a public issue. PostgreSQL tables are created on startup. Run with one Uvicorn worker as configured in the Dockerfile.

Without `DATABASE_URL`, the app falls back to local SQLite and displays a demo-storage warning. Render Free loses this local database on restart/redeploy/sleep. Do not invite paying customers until external storage has been configured and tested. Existing filesystem catalogues migrate into the database when available at startup, but Render cannot preserve the old container filesystem across a deployment. Export any important existing work before redeploying.

Limits: 30 catalogues per account, 500 products per catalogue, 100 MB of images per workspace, 10 MB catalogue uploads, 15 MB image uploads. Archiving is reversible and does not free quota. Keep customer volume within your database and hosting provider's free limits; no billing automation exists.

## Installable PWA

The app includes a manifest, 192/512 icons, offline app shell and an update prompt. Chrome/Edge/Android show an install action when eligible. On iOS, Safari → Share → Add to Home Screen. Private API responses and images are never cached by the service worker. Search, login and private catalogue loading require an internet connection. Sign-out clears in-memory catalogue state; old versions' shared catalogue storage/image caches are removed.

## Verification

Backend: `.venv/bin/python -m unittest discover -s backend/tests -v`
Frontend: `cd frontend && npm ci && npm run build`
Local server: `python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --workers 1`

End-to-end tests cover registration/invites, account isolation (including images and exports), recovery/session revocation, no-store headers, cross-site mutations, database reopen, pause/resume and the image-review/export workflow. Automated search remains dependent on third-party search availability; manual replacement works when no suitable images are found. No AI API calls are made by the web app.
