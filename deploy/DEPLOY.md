# Deploying Captn to captn-server024.org

Three ways to go live, cheapest first. **Option A (VPS) is strongly
recommended** — never expose your dev machine.

---

## Option A — Cheap VPS (recommended)

Works with any $4–6/mo box: Hetzner CX22, DigitalOcean droplet, OVH, etc.
Ubuntu 24.04 assumed.

### 1. DNS (at your registrar)
```
A     captn-server024.org    ->  <VPS IPv4>
A     www                    ->  <VPS IPv4>
```
Wait for propagation (`ping captn-server024.org` shows the VPS IP).

### 2. Server setup (paste as one script)
```bash
sudo apt update && sudo apt install -y caddy python3-venv git

sudo useradd -m -s /bin/bash captn
sudo -iu captn
git clone <your-repo> ~/captn && cd ~/captn
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Generate access token — save it, you'll need it to log in:
python3 -c "import secrets; print(secrets.token_urlsafe(32))" > .captn_token
mkdir -p .captn && cp .captn_token .captn/auth_token && rm .captn_token
cat .captn/auth_token   # <- this is your login token
```

### 3. Services (auto-start + auto-restart)
`/etc/systemd/system/captn.service`:
```ini
[Unit]
Description=Captn dashboard
After=network.target

[Service]
User=captn
WorkingDirectory=/home/captn/captn
ExecStart=/home/captn/captn/.venv/bin/streamlit run captn/dashboard/app.py --server.headless true --server.address 127.0.0.1 --server.port 8501
Restart=always
Environment=PYTHONUTF8=1

[Install]
WantedBy=multi-user.target
```

Then:
```bash
# Caddyfile from deploy/Caddyfile -> edit the email line first!
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile        # set your real email
sudo systemctl enable --now caddy captn
sudo systemctl status captn caddy     # both should be "active"
```

HTTPS certificates are issued automatically by Caddy on first request.

### 4. Firewall
```bash
sudo ufw allow OpenSSH
sudo ufw allow 80,443/tcp
sudo ufw enable
```
Port 8501 stays closed to the world — only Caddy talks to it.

### 5. Test
Open `https://captn-server024.org` → you should see the **Captn – Access
Restricted** gate → log in with the token.

---

## Option B — Home server with port-forward

Possible but riskier: your home IP is exposed, and your PC hosts a code-
execution panel. Only do this if you accept that.

1. Same DNS as above but pointing at your home public IP
2. Router: forward external 443 -> your PC's 443 (Caddy on Windows)
3. Run `deploy/run_dashboard_public.bat`
4. Caddy for Windows: `caddy.exe run --config C:\caddy\Caddyfile`

Extra precautions mandatory here:
- Windows Firewall: allow only 80/443 in
- Keep `CAPTN_AUTH_ENFORCE_LOCAL=1` so even LAN clients need the token

---

## Option C — Tunnel without opening ports (fastest, zero infra)

If you just want a shareable URL *today*, Cloudflare Tunnel gives you the
domain without touching router/VPS:

```bash
# On your machine, with cloudflared installed:
cloudflared tunnel --url http://localhost:8501
```
For the custom domain (requires the domain's nameservers on Cloudflare):
```bash
cloudflared tunnel login
cloudflared tunnel create captn
cloudflared tunnel route dns captn captn-server024.org
cloudflared tunnel run captn
```
Still keep auth ON (`set CAPTN_AUTH_TOKEN=...` before starting Streamlit).

---

## Security checklist before going live

- [ ] Token generated and stored in `.captn/auth_token` (never committed!) — or in `.env` as `CAPTN_AUTH_TOKEN`
- [ ] `deploy/Caddyfile` email changed to yours
- [ ] `.gitignore` contains `.captn/`, `.env`, and `openai_config.json`
- [ ] `python scripts/scan_secrets.py` passes (no secrets in tracked files)
- [ ] Test: logged-out visitor sees ONLY the login gate
- [ ] Optional demo hardening: don't enter a real OpenAI key on the public instance
- [ ] If the token was ever exposed, rotate it (see SECURITY.md) and restart the dashboard
