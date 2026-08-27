# wd-40 — Command Cheatsheet

All paths are absolute and work from any directory. The sandbox + Security Center
protect `.captn` secrets and contain untrusted scripts. No commit/push is done
(your choice: keep local).

---

## 1. Run untrusted scripts through the sandbox

```powershell
# basic (max containment, no network)
& "C:\Users\macel\AppData\Local\Python\bin\python.exe" "C:\Users\macel\Desktop\workspace\CaptN-BRAIN-main\CaptN-BRAIN-main\wd-40\run.py" <script.py>

# allow internet (for crawlers)
& "C:\Users\macel\AppData\Local\Python\bin\python.exe" "C:\Users\macel\Desktop\workspace\CaptN-BRAIN-main\CaptN-BRAIN-main\wd-40\run.py" --net <script.py>

# strict mode + allow specific read path
& "C:\Users\macel\AppData\Local\Python\bin\python.exe" "C:\Users\macel\Desktop\workspace\CaptN-BRAIN-main\CaptN-BRAIN-main\wd-40\run.py" --strict --allow C:\path\to\read <script.py>
```

---

## 2. Test the sandbox (verify protection works)

```powershell
& "C:\Users\macel\AppData\Local\hermes\hermes-agent\venv\Scripts\python" "C:\Users\macel\Desktop\workspace\CaptN-BRAIN-main\CaptN-BRAIN-main\wd-40\_selftest.py"
# expect: 11/11 passed
```

---

## 3. Security Center (the always-on monitor)

```powershell
# START (silent + startup toast) — run this to keep it alive
& "C:\Users\macel\AppData\Local\Python\bin\pythonw.exe" "C:\Users\macel\Desktop\workspace\CaptN-BRAIN-main\CaptN-BRAIN-main\wd-40\daemon.py" --interval 300

# Or just double-click: wd-40\start_shield.bat

# STOP
New-Item -Path "C:\Users\macel\Desktop\workspace\CaptN-BRAIN-main\CaptN-BRAIN-main\wd-40\stop.flag" -ItemType File -Force

# CHECK if running
Get-Process pythonw | Where-Object { $_.CommandLine -like '*daemon.py*' }

# VIEW activity log (its "window" now)
Get-Content "C:\Users\macel\Desktop\workspace\CaptN-BRAIN-main\CaptN-BRAIN-main\wd-40\logs\daemon.log" -Tail 10
```

---

## 4. Auto-start at login (one-time, needs admin PowerShell)

```powershell
# run PowerShell AS ADMINISTRATOR, then:
schtasks /Create /TN "wd-40-shield" /TR "C:\Users\macel\Desktop\workspace\CaptN-BRAIN-main\CaptN-BRAIN-main\wd-40\start_shield.bat" /SC ONLOGON /RL HIGHEST /F

# start it now (without waiting for next login):
schtasks /Run /TN wd-40-shield

# check status:
schtasks /Query /TN wd-40-shield

# remove it later:
schtasks /Delete /TN wd-40-shield /F
```

---

## 5. Notifications (toast)

```powershell
# test toast manually
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\Users\macel\Desktop\workspace\CaptN-BRAIN-main\CaptN-BRAIN-main\wd-40\_notify.ps1" "wd-40 Shield TEST" "notifications work" "info"

# if you don't see toasts: turn off Focus Assist / Do Not Disturb (Windows 11 suppresses them)
```

---

## 6. Secrets / .captn

```powershell
# check who can access .captn
icacls "C:\Users\macel\Desktop\workspace\CaptN-BRAIN-main\CaptN-BRAIN-main\.captn"

# confirm secrets are git-ignored (should print a match)
git -C "C:\Users\macel\Desktop\workspace\CaptN-BRAIN-main\CaptN-BRAIN-main" check-ignore -v .captn/auth_token .captn/openai_config.json
```

---

## 7. Git (local only — no commit, no push)

```powershell
Set-Location "C:\Users\macel\Desktop\workspace\CaptN-BRAIN-main\CaptN-BRAIN-main"

git status                       # see what's staged
git status --short | Measure-Object -Line   # count changed files
git diff --stat                  # summary of changes
# NOTE: nothing is committed. When you're ready (your call): git add -A && git commit -m "wd-40 hardening"
# NEVER git push — you chose to keep local.
```

---

## 8. Docker (optional, parked — not installed)

```powershell
# only after you install OFFICIAL Docker Desktop from docker.com (NOT the Store):
docker --version        # confirm
docker compose config   # validate compose
docker compose up --build   # build + run dashboard on :8501

# run untrusted script inside the container:
docker compose run --rm captn python wd-40/run.py <script.py>
docker compose run --rm captn python wd-40/run.py --net captn/workers/crawler.py
```

---

## Quick daily workflow

```powershell
# 1) Start the shield (once per session, or it auto-starts at login if task registered)
& "C:\Users\macel\AppData\Local\Python\bin\pythonw.exe" "C:\Users\macel\Desktop\workspace\CaptN-BRAIN-main\CaptN-BRAIN-main\wd-40\daemon.py" --interval 300

# 2) Run anything untrusted
& "C:\Users\macel\AppData\Local\Python\bin\python.exe" "C:\Users\macel\Desktop\workspace\CaptN-BRAIN-main\CaptN-BRAIN-main\wd-40\run.py" --net captn/workers/crawler.py

# 3) You'll get a toast if the shield finds anything; otherwise it stays silent.
```

---

## What's protected (verified)

| Protection | Status |
|---|---|
| Scripts can't write outside project | ✅ sandbox blocks it (11/11 tested) |
| Scripts can't read `.captn` / secrets | ✅ sandbox secret-read block |
| Scripts can't exfil via network/DNS | ✅ egress + DNS blocked |
| Scripts can't spawn external programs | ✅ spawn blocked |
| `.captn` locked at OS level | ✅ correct ACL (you + Admins + SYSTEM) |
| Secrets can't be committed | ✅ git-ignored |
| Background monitoring | ✅ daemon, silent + toast alerts |

**Note:** the sandbox is a tripwire against naive/LLM-generated scripts, not a
vault against deliberate malware (a script using `ctypes`/`windll` can bypass
audit hooks). For truly hostile code, use the Docker container.
