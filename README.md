# Fidra

**The rail that moves trust between institutions.**

Fidra is a digital reference exchange network for African financial institutions.  
It lets banks, MFBs, fintechs and lenders request, exchange and track customer references instantly via API.

---

## Run Locally

```bash
cd Fidra
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- Homepage → http://localhost:8000  
- API docs → http://localhost:8000/docs  
- Health   → http://localhost:8000/health  

### Environment variables

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres URL in production. Defaults to local SQLite. |
| `ADMIN_API_KEY` | Required to create institutions (`X-Admin-Key` header). Empty disables the endpoint. |
| `SEED_DEMO_DATA` | `true` seeds demo institutions and prints their keys. Development only. |
| `CORS_ORIGINS` | Comma-separated allowed browser origins. |

API keys are stored hashed and shown once, when an institution is created.

---

## Deploy on Render

1. Push this folder to a GitHub repository.
2. On Render → **New** → **Web Service**
3. Connect the repo and use:

| Setting          | Value                                              |
|------------------|----------------------------------------------------|
| Name             | `fidra`                                            |
| Runtime          | `Python 3`                                         |
| Build Command    | `pip install -r requirements.txt`                  |
| Start Command    | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |

4. Deploy.

---

## Core API Flow

Create an institution (admin only, the key is returned once):

```bash
curl -X POST https://YOUR-URL/v1/institutions \\
  -H "X-Admin-Key: <admin_key>" -H "Content-Type: application/json" \\
  -d '{"code": "mybank", "name": "My Bank", "bank_code": "123"}'
```

Then create a reference request:

```bash
curl -X POST https://YOUR-URL/v1/reference-requests \
  -H "X-API-Key: <api_key_from_logs>" \
  -H "Content-Type: application/json" \
  -d '{
    "customer_full_name": "Adebayo Okonkwo",
    "referee_account_number": "0123456789",
    "referee_bank_code": "011",
    "purpose": "account_opening",
    "consent_given": true
  }'
```

The response contains:
- `referee_authorize_url` → open in browser to authorize
- `customer_status_url` → live status page

---

## Brand

- **Name**: Fidra (from Latin *fides* = trust / faithfulness)
- **Tagline**: The rail that moves trust.
- **Colours**: Ink Navy · Trust Green · Rail Blue · Mint Signal · Harmattan Amber
- **Type**: Sora (headings) · Inter (body) · JetBrains Mono (data)
