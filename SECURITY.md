# Captn Security Runbook

This repo ships a code-execution dashboard behind an auth gate. Treat the
following secrets as the highest-value assets:

| Secret | Where it lives | Leaks if… |
|---|---|---|
| Dashboard access token | `.captn/auth_token` (gitignored) | pasted into a tracked file (e.g. `deploy/*.md`) |
| OpenAI-compatible key | `.captn/openai_config.json` (gitignored) or `.env` | committed to git |
| GitHub PAT | `.captn/github_token` (gitignored) or `.env` | committed to git |
| Cloudflared creds | `~/.cloudflared/` (per machine) | copied into the repo |

## What is already hardened
- Auth gate uses `hmac.compare_digest` (constant-time) token comparison.
- Login brute-force: sliding 10-attempt / 5-min lockout, escalating delay.
- Fail-closed: if no token is set, **remote** requests are denied (only local dev is open).
- CORS disabled, XSRF protection on.
- Dashboard binds `127.0.0.1` by default; the tunnel/proxy is the only exposure.

## How to ROTATE the dashboard token (do this if you ever suspect a leak)
1. Generate a new one:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
2. Write it to `.captn/auth_token` (or set `CAPTN_AUTH_TOKEN` in `.env`):
   ```bash
   echo <NEW_TOKEN> > .captn/auth_token
   ```
3. Restart the dashboard (the running process still holds the OLD token):
   ```bash
   # kill the old streamlit, then:
   python -m streamlit run captn/dashboard/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
   ```
4. The old token stops working immediately. Distribute the new token out-of-band.

## Pre-commit secret scan
Run before every commit:
```bash
python scripts/scan_secrets.py
```
It fails (exit 1) if it finds a high-entropy secret or the dashboard token
pattern in any tracked/tracked-candidate file. Wire it into a pre-commit hook:
```bash
echo 'python scripts/scan_secrets.py' > .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

## Don't
- Don't paste tokens into `*.md` / `*.py` / `*.bat` that aren't gitignored.
- Don't enter a real OpenAI key while the dashboard is publicly tunnelled.
- Don't bind the dashboard to `0.0.0.0` without a token enforced.
