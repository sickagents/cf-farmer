# cf-farmer

Cloudflare Workers AI account farmer. Auto-creates CF accounts, solves Turnstile, verifies email, generates API tokens, injects into 9router.

## Jupyter Notebook Setup

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
os.environ["IMAP_USER"] = "you@gmail.com"        # Your Gmail
os.environ["IMAP_PASS"] = "your-app-password"     # Gmail app password
os.environ["DOMAIN"] = "yourdomain.com"           # Email domain

# Solver config
USE_LOCAL_SOLVER = True                           # True = Boterdrop, False = Solverify
LOCAL_SOLVER_URL = "http://localhost:8000"        # Boterdrop server URL
SOLVERIFY_API_KEY=***  # Only if USE_LOCAL_SOLVER = False

print(f"Domain: {os.environ['DOMAIN']}")
print(f"Solver: {'Local (Boterdrop)' if USE_LOCAL_SOLVER else 'Solverify'}")
```

### Step 3: Add Proxies

```python
# Add proxies (one per line)
proxies = """
# host:port:user:pass
""".strip()

with open("proxy.txt", "w") as f:
    f.write(proxies)

print(f"Proxies saved")
```

### Step 4: Patch farmer.py for Local Solver

```python
# Only needed if USE_LOCAL_SOLVER = True
import re

if USE_LOCAL_SOLVER:
    with open("farmer.py", "r") as f:
        code = f.read()

    local_solver_func = '''
async def solve_turnstile(captcha_key, sitekey, log=print):
    """Solve Turnstile via local Boterdrop solver."""
    import requests
    url = f"{os.environ.get('LOCAL_SOLVER_URL', 'http://localhost:8000')}/turnstile"
    log(f"    [captcha] solving via local solver...")
    for attempt in range(3):
        try:
            r = requests.get(url, params={"sitekey": sitekey, "url": SIGNUP_URL}, timeout=120)
            data = r.json()
            if data.get("token"):
                log(f"    [captcha] solved!")
                return data["token"]
            log(f"    [captcha] attempt {attempt+1} failed: {data}")
        except Exception as e:
            log(f"    [captcha] attempt {attempt+1} error: {e}")
    return None
'''

    pattern = r'# --- Solverify Turnstile ---.*?return None\n'
    code = re.sub(pattern, local_solver_func + '\n', code, flags=re.DOTALL)

    with open("farmer.py", "w") as f:
        f.write(code)

    print("farmer.py patched for local solver")
```

### Step 5: Start Boterdrop Solver (if using local)

```python
import subprocess, sys, time, requests

solver_proc = None
if USE_LOCAL_SOLVER:
    try:
        r = requests.get(f"{LOCAL_SOLVER_URL}/", timeout=2)
        print(f"Boterdrop already running at {LOCAL_SOLVER_URL}")
    except:
        print("Starting Boterdrop solver...")
        solver_proc = subprocess.Popen(
            [sys.executable, "api_server.py"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        for i in range(30):
            time.sleep(2)
            try:
                r = requests.get(f"{LOCAL_SOLVER_URL}/", timeout=2)
                if r.status_code == 200:
                    print(f"Solver ready after {(i+1)*2}s")
                    break
            except:
                pass
        else:
            print("Solver may not be ready")
```

### Step 6: Run Farmer (1 Account)

```python
import asyncio, os
from farmer import farm_one

if USE_LOCAL_SOLVER:
    os.environ["LOCAL_SOLVER_URL"] = LOCAL_SOLVER_URL

captcha_key = SOLVERIFY_API_KEY if not USE_LOCAL_SOLVER else "local"

async def run():
    acc = await farm_one(captcha_key, log=print)
    if acc:
        print(f"\nEmail: {acc['email']}")
        print(f"Account ID: {acc['account_id']}")
        print(f"API Token: {acc['api_token'][:30]}...")
        print(f"Status: {acc['status']}")
    return acc

acc = await run()
```

### Step 7: Run Batch (N Accounts)

```python
from farmer import farm_batch

COUNT = 5
captcha_key = SOLVERIFY_API_KEY if not USE_LOCAL_SOLVER else "local"
await farm_batch(captcha_key, COUNT)
```

### Step 8: Check Results

```python
import json

if os.path.exists("accounts.json"):
    with open("accounts.json") as f:
        accounts = json.load(f)
    print(f"Total accounts: {len(accounts)}")
    for acc in accounts:
        print(f"  {acc['email']} | {acc['status']} | {acc.get('account_id', 'N/A')}")
```

### Step 9: Verify 9router Integration

```python
import sqlite3

db_path = os.path.expanduser("~/.9router/db/data.sqlite")
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name, isActive FROM providerConnections WHERE provider='cloudflare-ai'")
    rows = cur.fetchall()
    conn.close()
    print(f"Cloudflare connections in 9router: {len(rows)}")
    for name, active in rows:
        print(f"  {name} - {'active' if active else 'inactive'}")
```

### Step 10: Cleanup

```python
if solver_proc:
    solver_proc.terminate()
    solver_proc.wait()
    print("Solver stopped")
```

## Flow

```
bootstrap -> Turnstile solve -> user/create -> email verify (IMAP) ->
API token -> verify_token -> inject to 9router
```

## Requirements

- Catch-all email domain (e.g. Cloudflare Email Routing -> Gmail)
- Gmail app password for IMAP access
- Residential proxies (recommended)
- Boterdrop Solver running locally (or Solverify API key)
