# cf-farmer

Cloudflare Workers AI account farmer. Auto-creates CF accounts, solves Turnstile, verifies email, generates API tokens, injects into 9router.

## Prasyarat

1. **Boterdrop Solver** sudah running di `http://localhost:8000`
   - Ikuti tutorial di [boterdrop-solver](https://github.com/sickagents/boterdrop-solver)

2. **Catch-all email domain** (e.g. Cloudflare Email Routing -> Gmail)

3. **Gmail app password** untuk IMAP akses

---

## Terminal Setup (One-Shot)

```bash
# 1. Clone repo
git clone https://github.com/sickagents/cf-farmer ~/cf-farmer
cd ~/cf-farmer

# 2. Install dependencies
pip install curl_cffi python-dotenv requests

# 3. Config (.env)
cat > .env << 'EOF'
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USER=wllmstevan@gmail.com
IMAP_PASS=your-app-password
DOMAIN=airwallex.fun
EOF

# 4. Patch farmer.py untuk pakai solver lokal
python3 << 'PYEOF'
import re

with open("farmer.py", "r") as f:
    code = f.read()

local_solver = '''
async def solve_turnstile(captcha_key, sitekey, log=print):
    """Solve Turnstile via local Boterdrop solver."""
    import requests as req
    import os
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

pattern = r'# --- Solverify Turnstile ---.*?return None\n'
code = re.sub(pattern, local_solver + '\n', code, flags=re.DOTALL)

with open("farmer.py", "w") as f:
    f.write(code)

print("farmer.py patched for local Boterdrop solver")
PYEOF

# 5. Test solver connection
python3 -c "
import requests
r = requests.get('http://localhost:8000/turnstile', params={'sitekey': '0x4AAAAAAAJel0iaAR3mgkjp', 'url': 'https://dash.cloudflare.com/sign-up'}, timeout=120)
data = r.json()
print(f'Token: {data[\"token\"][:50]}...' if data.get('token') else f'Error: {data}')
"

# 6. Farm 1 account
BOTERDROP_URL=http://localhost:8000 python3 farmer.py --count 1
```

## Batch Farm

```bash
# Farm 10 accounts
BOTERDROP_URL=http://localhost:8000 python3 farmer.py --count 10
```

## Cek Hasil

```bash
# Lihat semua akun yang sudah dibuat
cat accounts.json | python3 -m json.tool

# Hitung total
python3 -c "import json; accs=json.load(open('accounts.json')); print(f'Total: {len(accs)}'); [print(f'  {a[\"email\"]} | {a[\"status\"]}') for a in accs]"
```

## Cek 9router

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('/root/.9router/db/data.sqlite')
cur = conn.cursor()
cur.execute('SELECT name, isActive FROM providerConnections WHERE provider=\"cloudflare-ai\"')
rows = cur.fetchall()
conn.close()
print(f'Cloudflare connections: {len(rows)}')
for name, active in rows:
    print(f'  {name} - {\"active\" if active else \"inactive\"}')
"
```

## Proxy

Edit `proxy.txt`, tambahkan proxy (satu per baris):

```
host:port:user:pass
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `IMAP_HOST` | imap.gmail.com | IMAP server |
| `IMAP_PORT` | 993 | IMAP port |
| `IMAP_USER` | - | Gmail address |
| `IMAP_PASS` | - | Gmail app password |
| `DOMAIN` | - | Email domain |
| `BOTERDROP_URL` | http://localhost:8000 | Solver URL |

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
| Token invalid | Tunggu 12s setelah create token (propagation delay) |
| 9router duplicate | Normal, akun sudah ada di DB |
