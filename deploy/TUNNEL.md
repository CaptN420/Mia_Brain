# Captn Tunnel — current setup & how to restart it

> ⚠️ SECURITY: This file previously contained a live access token and Cloudflared
> credential paths in cleartext. Those have been removed. Treat the token as a
> SECRET — it lives only in `.captn/auth_token` (gitignored). Never paste it
> back into a tracked file. The live dashboard URL and tunnel identity are
> operational config, but the token itself must stay out of version control.

## Restart everything after a reboot

```bash
# Terminal 1 — dashboard (localhost-only; the auth gate enforces the token)
cd C:\Users\macel\Desktop\workspace\CaptN-BRAIN-main\CaptN-BRAIN-main
python -m streamlit run captn/dashboard/app.py --server.port 8501 --server.headless true --server.address 127.0.0.1

# Terminal 2 — tunnel
cloudflared tunnel run captn
```

## Run automatically at startup (optional)

Install cloudflared as a Windows service:

```
cloudflared service install
```

(Then it starts with Windows; the dashboard still needs starting, or make
a Task Scheduler entry for the streamlit command.)

## Take the site down / up

- Down instantly: kill the `cloudflared tunnel run captn` process.
- Up again: re-run the same command. Domain/DNS never changes.

## Security notes

- Token required for all remote visitors; localhost bypass only for
  genuine 127.0.0.1/::1 clients.
- The token is read from `.captn/auth_token` (or `CAPTN_AUTH_TOKEN` env).
  If you suspect it leaked, **rotate it** (see SECURITY.md) and restart the
  dashboard — the old token immediately stops working.
- Don't enter a real OpenAI API key while public — use none or throwaway.
- Secrets live in `.captn/` (gitignored) and `~/.cloudflared/`.
- Cloudflared credential files are per-machine; they are NOT part of this repo.
