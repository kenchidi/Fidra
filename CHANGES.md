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
- app/api/routes.py (also: GET /v1/inbox, POST /v1/reference-requests/{id}/bank-response)
- app/api/public.py, app/templates/referee.html, app/templates/referee_done.html   referee page reworked (see below)
- app/templates/dash_insights.html   NEW
- app/templates/dash_new.html, dash_detail.html, dash_inbox.html, dash_inbox_detail.html   (updated for signed forms)
- app/templates/dash_inbox.html, dash_inbox_detail.html, dash_account.html, dash_team.html   NEW
- app/services/mock_bank.py  DELETE this file from your repo (no longer used)
- app/templates/home.html   NEW homepage: Path A / Path B, in-browser example, assurance levels, Sign in link
- app/api/dashboard.py      NEW staff dashboard: login, requests list, new request, request detail/result, print view
- app/core/security.py      NEW password hashing
- app/templates/dash_*.html NEW dashboard screens (base, login, requests, new, detail)
- app/static/fidra-docs.html docs: Path A / Path B flow, admin header, key shown once
- requirements.txt, render.yaml, README.md

## Environment variables (set in Render)
- ADMIN_API_KEY  a long random string. Without it, institution creation is disabled.
- DATABASE_URL   Postgres connection string (render.yaml wires this to the fidra-db database).
- SEED_DEMO_DATA leave false in production.
- CORS_ORIGINS   your real front-end origins, comma-separated. "*" allows all.

## Staff dashboard (new)
Staff sign in at /app/login. Fidra creates their logins with the admin key:

    curl -X POST https://YOUR-URL/v1/institutions/CODE/staff \
      -H "X-Admin-Key: YOUR_ADMIN_KEY" -H "Content-Type: application/json" \
      -d '{"email":"ngozi@bank.ng","full_name":"Ngozi A.","role":"officer","password":"a-long-temporary-password"}'

New environment variables: SESSION_SECRET (long random string) and SESSION_HTTPS_ONLY=true. render.yaml sets both.
New table: staffuser (created automatically on start).
Included: login with throttling and CSRF protection, requests list with search, a guided new-request form,
a result screen in plain language, a link to share with the referee (WhatsApp or copy), a print view for the KYC file.
Not yet included: staff management screen, password change or reset, different permissions per role
(roles are stored, but every signed-in person sees their institution's requests), the referee bank's
"checks waiting for you" inbox for Path B, automatic SMS or email to the referee.

## Update: Path B inbox, password change, team (new)
- Path B is now real. The referee's bank answers from Inbox (or the API), and the referee agrees through the link. The request completes when BOTH have answered. The simulated bank is gone.
- New staff logins (created by Fidra or by a bank admin) must choose their own password on first sign-in. Everyone can change theirs under their name at the top right.
- Admins get a Team page: add people, switch them off or on, set a new temporary password.
- Changed table: referencerequest has three new columns and staffuser has one. Start from a fresh database (Render's free plan resets on redeploy anyway).
- Referee page reworked. Path B: the person is only asked to agree ("I agree" / decline) and is told what their bank will confirm; no good-standing or tenure questions, because the bank answers those. Path A: wording says the answers are the referee's own statement, not checked by a bank. Both: the account number is masked, and the asking bank is named.
- Still not built: automatic SMS/email to the referee, a real-time bank integration beyond the API endpoints, different permissions for officers and supervisors, per-staff request history.

## Update: signed bank forms (new)
When the referee's bank is on Fidra, the requesting bank's officer chooses "Signed bank form" on the New request screen and uploads a photo or scan of the form the referee signed on paper. The other bank sees it in its Inbox, compares the signature with its specimen, and answers: matches / doesn't match / photo unclear (asks for a clearer copy). The referee needs no phone or device. "Send a link" still works for referees who have one.
- Rules: JPG, PNG or PDF up to 5 MB (checked by file content, not the name). Only the two banks can open a form. Every view is logged (table formviewlog). Forms are deleted after SIGNED_FORM_RETENTION_DAYS (default 90, set it in Render if you want another period); the record that a form existed is kept.
- The result says how the referee was verified ("Signature compared by <bank>"). A mismatch ends the request with a clear message.
- API: POST/GET /v1/reference-requests/{public_id}/signed-form, and a new signature_match field on the bank-response call (required when the request came with a signed form).
- Storage: images are kept in the database for the pilot. Use Postgres, because Render's free app disk is wiped on restart. Render's free Postgres expires after a limited period, so check that before real use.
- New tables: signedform, formviewlog. New columns on referencerequest: verification_method, form_status. Start from a fresh database.
- Not covered: a second check on who signed (for example a call or code to the number the bank holds), and printing a bank's own form template inside Fidra.

## Update: reducing the workload on banks (new)
- Checks before a request is accepted (screens and API): the referee's account number must be exactly 10 digits and the bank code 3 to 6 digits; an identical open request (same applicant, referee account and bank) is refused and the existing request id is returned. API clients that sent other formats will now get a 422, so tell any developers using the API.
- Daily limit per institution (DAILY_REQUEST_LIMIT, default 200; 0 turns it off).
- Response deadline for Path B (BANK_RESPONSE_HOURS, default 48). The requester sees the deadline; the responding bank's inbox shows "Due in..." or "Overdue by..." and lists overdue and soonest-due items first.
- In-app reminders: the asking bank can press "Remind the bank" once per REMINDER_COOLDOWN_HOURS (default 12). The bank sees "Reminded" in its inbox. API: POST /v1/reference-requests/{public_id}/remind. This does not send email or SMS.
- Insights page (supervisors and admins): requests sent, completed, typical time to complete and requests past their deadline when asking; received, answered, typical time to answer, waiting now and overdue when answering. Numbers are for the bank's own team only.
- New columns on referencerequest (bank_due_at, last_reminder_at, reminder_count). Start from a fresh database.
- Deliberately not built: answering many requests in one click. It invites rubber-stamping, which is the weakness Fidra is meant to fix.

## Update: easier-to-follow docs page (new)
The side menu and page are now grouped by task with plain labels (Set up a bank, Ask another bank for a reference, Answer another bank, Pages for referees and customers, System). Each action has a plain-language title and a one-line description, and the technical path sits beside it. Search finds actions by name or by path, the current section is highlighted as you scroll, and small screens get a "Jump to a section" menu. Only app/static/fidra-docs.html changed.

## Important
- The institution table changed (api_key -> api_key_hash). Existing databases are not migrated. Start from a fresh database, then recreate institutions through the admin endpoint.
- Any API keys that were ever returned by the old open endpoint or printed in logs should be treated as compromised.
- render.yaml now names the service "fidra" and adds a database. Your live service was created in the dashboard, so only use the blueprint if you want Render to manage it from this file.
