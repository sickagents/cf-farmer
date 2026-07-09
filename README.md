# cf-farmer

Cloudflare Workers AI account farmer. Auto-creates CF accounts, solves Turnstile, verifies email, generates API tokens, injects into 9router.

## Cara Pakai

### Prasyarat

1. **Boterdrop Solver** sudah running di `http://localhost:8000`
   - Ikuti tutorial di [boterdrop-solver](https://github.com/sickagents/boterdrop-solver)
   - Pastikan server sudah jalan: `curl http://localhost:8000/` return 200

2. **Catch-all email domain** (e.g. Cloudflare Email Routing -> Gmail)

3. **Gmail app password** untuk IMAP akses

---

### Step 1: Install Dependencies

```python
import subprocess, sys

subprocess.run([sys.executable, "-m", "pip", "install", "-q",
    "curl_cffi>=0.5", "python-dotenv>=1.0", "requests"], check=True)
print("Done")
```

### Step 2: Config

```python
import os

# === EDIT THIS ===
os.environ["IMAP_HOST"] = "imap.gmail.com"
os.environ["IMAP_PORT"] = "993"
os.environ["IMAP_USER"] = "wllmstevan@gmail.com"     # Your Gmail
os.environ["IMAP_PASS"] = "your-app-password"         # Gmail app password
os.environ["DOMAIN"] = "airwallex.fun"                # Email domain

# Boterdrop solver URL (harus sudah running)
BOTERDROP_URL = "http://localhost:8000"

print(f"Domain: {os.environ['DOMAIN']}")
print(f"Solver: {BOTERDROP_URL}")
```

### Step 3: Cek Boterdrop Solver

```python
import requests

try:
    r = requests.get(f"{BOTERDROP_URL}/", timeout=5)
    print(f"Boterdrop OK: {r.status_code}")
except Exception as e:
    print(f"Boterdrop TIDAK JALAN: {e}")
    print("Jalankan dulu Boterdrop Solver via tutorial di repo boterdrop-solver")
```

### Step 4: Add Proxies

```python
# Format: host:port:user:pass (satu per baris)
proxies = """
# gw.dataimpulse.com:823:user:pass
""".strip()

with open("proxy.txt", "w") as f:
    f.write(proxies)

print("Proxies saved")
```

### Step 5: Patch farmer.py (Solver Lokal)

```python
import re

with open("farmer.py", "r") as f:
    code = f.read()

# Ganti fungsi Solverify dengan solver lokal Boterdrop
local_solver = '''
async def solve_turnstile(captcha_key, sitekey, log=print):
    """Solve Turnstile via local Boterdrop solver."""
    import requests as req
    solver_url = os.environ.get("BOTERDROP_URL", "http://localhost:8000")
    url = f"{solver_url}/turnstile"
    log(f"    [captcha] solving via Boterdrop at {solver_url}...")
    for attempt in range(3):
        try:
            r = req.get(url, params={"sitekey": sitekey, "url": SIGNUP_URL}, timeout=120)
            data = r.json()
            if data.get("token"):
                log(f"    [captcha] solved!")
                return data["token"]
            log(f"    [captcha] attempt {attempt+1} failed: {data}")
        except Exception as e:
            log(f"    [captcha] attempt {attempt+1} error: {e}")
    return None
'''

# Replace Solverify function
pattern = r'# --- Solverify Turnstile ---.*?return None\n'
code = re.sub(pattern, local_solver + '\n', code, flags=re.DOTALL)

with open("farmer.py", "w") as f:
    f.write(code)

print("farmer.py patched -> pakai Boterdrop lokal")
```

### Step 6: Set Env & Test Solve

```python
import os, requests

os.environ["BOTERDROP_URL"] = BOTERDROP_URL

# Test Turnstile solve
r = requests.get(f"{BOTERDROP_URL}/turnstile", params={
    "sitekey": "0x4AAAAAAAJel0iaAR3mgkjp",
    "url": "https://dash.cloudflare.com/sign-up"
}, timeout=120)

data = r.json()
if data.get("token"):
    print(f"Turnstile token: {data['token'][:50]}...")
    print("Solver siap dipakai!")
else:
    print(f"Error: {data}")
```

### Step 7: Farm 1 Account

```python
import asyncio, os
from farmer import farm_one

os.environ["BOTERDROP_URL"] = BOTERDROP_URL

async def run():
    acc = await farm_one("local", log=print)
    if acc:
        print(f"\nEmail: {acc['email']}")
        print(f"Account ID: {acc['account_id']}")
        print(f"API Token: {acc['api_token'][:30]}...")
        print(f"Status: {acc['status']}")
    return acc

acc = await run()
```

### Step 8: Farm Batch

```python
from farmer import farm_batch

COUNT = 5  # Jumlah akun yang mau dibuat
await farm_batch("local", COUNT)
```

### Step 9: Cek Hasil

```python
import json, os

if os.path.exists("accounts.json"):
    with open("accounts.json") as f:
        accounts = json.load(f)
    print(f"Total accounts: {len(accounts)}")
    for acc in accounts:
        token = acc.get('api_token', '')
        print(f"  {acc['email']} | {acc['status']} | {token[:20]}...")
else:
    print("Belum ada akun")
```

### Step 10: Cek 9router

```python
import sqlite3, os

db_path = os.path.expanduser("~/.9router/db/data.sqlite")
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name, isActive FROM providerConnections WHERE provider='cloudflare-ai'")
    rows = cur.fetchall()
    conn.close()
    print(f"Cloudflare connections: {len(rows)}")
    for name, active in rows:
        print(f"  {name} - {'active' if active else 'inactive'}")
```

---

## Flow

```
Boterdrop Solver (port 8000)        cf-farmer
       |                               |
       |  <-- GET /turnstile --        |  1. bootstrap CF signup
       |  -- token -->                 |  2. solve Turnstile
       |                               |  3. create account
       |                               |  4. verify email (IMAP)
       |                               |  5. generate API token
       |                               |  6. inject to 9router
```

## Troubleshooting

| Masalah | Solusi |
|---|---|
| Boterdrop tidak jalan | Jalankan via tutorial di repo boterdrop-solver |
| Turnstile timeout | Cek proxy, coba kurangi thread di config Boterdrop |
| Email tidak masuk | Cek IMAP credentials, cek Cloudflare Email Routing |
| Token invalid | Tunggu12s setelah create token (propagation delay) |
| 9router duplicate | Normal, akun sudah ada di DB |
