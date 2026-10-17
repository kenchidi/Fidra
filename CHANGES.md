# Security patch: what changed and how to apply it

Replace the files listed below in your repo (same paths), then set the environment variables.

## Files
- app/api/deps.py            hashed key lookup, new require_admin check
- app/api/routes.py          admin-only institution creation, 409 on duplicates, ownership check on GET /reference-requests/{id}
- app/api/public.py          self-attested answers no longer claim the account was verified
- app/core/config.py         new settings (ADMIN_API_KEY, SEED_DEMO_DATA, CORS_ORIGINS); unused secret_key removed
- app/core/database.py       Postgres support
- app/main.py                demo seeding only when SEED_DEMO_DATA=true, CORS from settings
- app/models/models.py       API keys stored as a hash
- app/models/schemas.py      account_exists can be null (not verified)
- app/services/institution_service.py   NEW: creates an institution and returns its key once
- app/services/mock_bank.py  mock responses marked "simulated": true
- app/services/reference_service.py     self-attested responses carry "account_verified": false
- app/templates/home.html   NEW homepage: Path A / Path B, in-browser example, assurance levels
- app/static/fidra-docs.html docs: Path A / Path B flow, admin header, key shown once
- requirements.txt, render.yaml, README.md

## Environment variables (set in Render)
- ADMIN_API_KEY  a long random string. Without it, institution creation is disabled.
- DATABASE_URL   Postgres connection string (render.yaml wires this to the fidra-db database).
- SEED_DEMO_DATA leave false in production.
- CORS_ORIGINS   your real front-end origins, comma-separated. "*" allows all.

## Important
- The institution table changed (api_key -> api_key_hash). Existing databases are not migrated. Start from a fresh database, then recreate institutions through the admin endpoint.
- Any API keys that were ever returned by the old open endpoint or printed in logs should be treated as compromised.
- render.yaml now names the service "fidra" and adds a database. Your live service was created in the dashboard, so only use the blueprint if you want Render to manage it from this file.
