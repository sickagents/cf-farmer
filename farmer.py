#!/usr/bin/env python3
"""Cloudflare Workers-AI account farmer.
 
Flow (reverse-engineered from a real signup HAR — cf.json):
  bootstrap ? captcha/challenge (sitekey) ? Solverify Turnstile ?
  user/create ? persistence/user(emailVerificationRequest) ? poll email ?
  user/email-verification ? accounts(account_id) ? user/tokens (AFTER verify).
 
The whole flow runs in ONE curl_cffi session (cookie jar persists the auth
session, impersonates Chrome TLS fingerprint to evade Cloudflare challenges).
Email verification polling uses direct IMAP.
"""
import asyncio
import json
import time
import base64
import random
import string
import argparse
import re
import os
import imaplib
import email
import uuid
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
 
from curl_cffi.requests import AsyncSession, RequestsError
from dotenv import load_dotenv
 
# --- Config ---
HERE = Path(__file__).parent
ACCOUNTS_FILE = HERE / "accounts.json"
ENV_FILE = HERE / ".env"
load_dotenv(ENV_FILE, override=True)
 
DOMAIN = os.environ.get("DOMAIN", "")
FALLBACK_SITEKEY = "0x4AAAAAAAJel0iaAR3mgkjp"
STRATUS_COMMIT = "43768e5f0b36b3c6c3c5ed00afa10affa55b38db"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
 
PROXY_FILE = HERE / "proxy.txt"
 
API = "https://dash.cloudflare.com/api/v4"
SIGNUP_URL = "https://dash.cloudflare.com/sign-up"
 
API_TOKEN_PERMISSIONS = [
    {"id": "644535f4ed854494a59cb289d634b257"},
    {"id": "a92d2450e05d4e7bb7d0a64968f83d11"},
    {"id": "bacc64e0f6c34fc0883a1223f938a104"},
]
 
HEADERS = {"User-Agent": UA, "x-cross-site-security": "dash"}
DEFAULT_TIMEOUT = 45
 
 
# --- Credentials ---
def get_captcha_key():
    return os.environ.get("SOLVERIFY_API_KEY")
 
 
# --- Proxy rotation from local file (proxy.txt) ---
_PROXY_CACHE = None
 
def _parse_proxy_line(line: str):
    """Parse a proxy.txt line into a curl_cffi proxy URL.
 
    Accepts:
      host:port:user:pass        -> http://user:pass@host:port
      host:port                  -> http://host:port
      http://user:pass@host:port -> passed through unchanged
      socks5://...               -> passed through unchanged
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "://" in line:
        return line  # already a full proxy URL
    parts = line.split(":")
    if len(parts) == 4:
        host, port, user, pwd = parts
        return f"http://{user}:{pwd}@{host}:{port}"
    if len(parts) == 2:
        host, port = parts
        return f"http://{host}:{port}"
    return None
 
def load_proxies():
    """Load and cache proxies from PROXY_FILE."""
    global _PROXY_CACHE
    if _PROXY_CACHE is not None:
        return _PROXY_CACHE
    proxies = []
    if PROXY_FILE.exists():
        for line in PROXY_FILE.read_text().splitlines():
            p = _parse_proxy_line(line)
            if p:
                proxies.append(p)
    _PROXY_CACHE = proxies
    return proxies
 
def random_proxy():
    """Pick one proxy at random from the local file."""
    proxies = load_proxies()
    if not proxies:
        return None
    return random.choice(proxies)
 
 
# --- HTTP helpers (curl_cffi with Chrome TLS fingerprint impersonation) ---
async def _req(session, method, url, payload=None, extra=None, proxy=None):
    kwargs = {"timeout": DEFAULT_TIMEOUT}
    if payload is not None:
        kwargs["json"] = payload
        kwargs["headers"] = {"content-type": "application/json", **(extra or {})}
    elif extra:
        kwargs["headers"] = extra
    if proxy:
        kwargs["proxy"] = proxy
 
    for _ in range(3):
        try:
            r = await session.request(method, url, **kwargs)
            try:
                return r.json()
            except (json.JSONDecodeError, ValueError):
                return {"_status": r.status_code, "_text": r.text}
        except RequestsError:
            await asyncio.sleep(2)
    return None
 
async def get_json(s, url, proxy=None):
    return await _req(s, "GET", url, proxy=proxy)
 
async def post_json(s, url, payload, extra=None, proxy=None):
    return await _req(s, "POST", url, payload, extra, proxy=proxy)
 
async def put_json(s, url, payload, proxy=None):
    return await _req(s, "PUT", url, payload, proxy=proxy)
 
 
# --- IMAP email polling ---
async def get_verification_token(email_addr, timeout=240, log=print):
    """Poll Gmail via IMAP for the Cloudflare verification link and return its token."""
    log(f"    [verify] polling inbox for {email_addr} (={timeout}s)...")
 
    imap_host = os.environ["IMAP_HOST"]
    imap_port = int(os.environ["IMAP_PORT"])
    imap_user = os.environ["IMAP_USER"]
    imap_pass = os.environ["IMAP_PASS"]
 
    seen = set()
    start = time.time()
 
    while time.time() - start < timeout:
        try:
            mail = imaplib.IMAP4_SSL(imap_host, imap_port)
            mail.login(imap_user, imap_pass)
            mail.select("INBOX")
 
            status, messages = mail.search(None, f'(TO "{email_addr}")')
            if status == "OK":
                for num in messages[0].split():
                    if num in seen:
                        continue
                    seen.add(num)
 
                    status, data = mail.fetch(num, "(RFC822)")
                    if status != "OK":
                        continue
 
                    msg = email.message_from_bytes(data[0][1])
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                                break
                    else:
                        body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
 
                    link = re.search(r"/email-verification\?token=([A-Za-z0-9_\-]+)", body)
                    if link:
                        mail.logout()
                        return link.group(1)
                    tok = re.search(r"[?&]token=([A-Za-z0-9_\-]{40,})", body)
                    if tok:
                        mail.logout()
                        return tok.group(1)
 
            mail.logout()
        except Exception as e:
            log(f"    [verify] imap error: {e}")
 
        await asyncio.sleep(8)
 
    log("    [verify] timeout, no email")
    return None
 
 
# --- Captcha throttle: serialized so Solverify doesn't rate-limit ---
_captcha_sem = asyncio.Semaphore(1)
 
# --- Solverify Turnstile ---
SOLVERIFY = "https://solver.solverify.net"
 
async def solve_turnstile(captcha_key, sitekey, log=print):
    """Solve Turnstile via Solverify (captcha is serialized globally)."""
    async with _captcha_sem:
        async with AsyncSession(impersonate="chrome131") as s:
            created = await post_json(s, f"{SOLVERIFY}/createTask",
                                      {"clientKey": captcha_key,
                                       "task": {"type": "turnstile", "websiteURL": SIGNUP_URL,
                                                "websiteKey": sitekey, "cdata": "signup", "action": "signup"}})
            if not created or not created.get("taskId"):
                log(f"    [captcha] Solverify createTask failed: {created}")
                return None
            tid = created["taskId"]
            for _ in range(45):
                await asyncio.sleep(3)
                res = await post_json(s, f"{SOLVERIFY}/getTaskResult",
                                      {"clientKey": captcha_key, "taskId": tid})
                if not res:
                    continue
                if res.get("status") == "completed":
                    return res["solution"]["value"]
                if res.get("errorId"):
                    log(f"    [captcha] Solverify error: {res.get('errorCode')} {res.get('errorDescription')}")
                    return None
            log("    [captcha] Solverify timeout")
            return None
 
 
# --- helpers ---
def gen_email():
    return "".join(random.choices(string.ascii_lowercase, k=8)) + str(random.randint(100, 999)) + "@" + DOMAIN
 
def gen_password():
    return "".join(random.choices(string.ascii_letters, k=10)) + str(random.randint(10, 99)) + "-Aa1!"
 
def make_legal_stamp(country="id"):
    raw = f"ts:{int(time.time()*1000)}/stratus_commit:{STRATUS_COMMIT}/country:{country}"
    return base64.b64encode(raw.encode()).decode()
 
def load_accounts():
    return json.loads(ACCOUNTS_FILE.read_text()) if ACCOUNTS_FILE.exists() else []
 
def save_account(acc):
    accs = load_accounts(); accs.append(acc); ACCOUNTS_FILE.write_text(json.dumps(accs, indent=2))
    inject_to_9router(acc)
 
 
# --- 9router DB auto-inject ---
ROUTER_DB = Path(os.path.expanduser("~/.9router/db/data.sqlite"))
ROUTER_CONNECTION_NAME = "WaguriAgent"
 
def _is_transient_sqlite_error(e: Exception) -> bool:
    """True if the error is a transient WAL/checkpoint contention issue.
 
    'database disk image is malformed' here is NOT real corruption (9router's
    integrity_check returns ok) — it's a transient read of a half-written WAL
    page while 9router checkpoints. 'database is locked'/'busy' are the same
    class of concurrent-access errors. All are safe to retry on a fresh conn.
    """
    msg = str(e).lower()
    return any(s in msg for s in (
        "malformed", "database is locked", "database is busy", "disk i/o",
    ))
 
 
def _inject_once(acc, api_token, account_id, now):
    """Single attempt: open conn, pick priority, insert, commit. Raises on failure."""
    conn_id = str(uuid.uuid4())
    db = sqlite3.connect(str(ROUTER_DB), timeout=30.0)
    try:
        # Wait up to 30s for any 9router write lock instead of failing instantly.
        db.execute("PRAGMA busy_timeout=30000")
        # SELECT + INSERT in one IMMEDIATE transaction so priority can't race.
        db.execute("BEGIN IMMEDIATE")
        cur = db.execute(
            "SELECT COALESCE(MAX(priority), 0) FROM providerConnections WHERE provider=?",
            ("cloudflare-ai",),
        )
        next_priority = (cur.fetchone()[0] or 0) + 1
 
        data = json.dumps({
            "apiKey": api_token,
            "testStatus": "active",
            "providerSpecificData": {
                "accountId": account_id,
                "connectionProxyEnabled": False,
                "connectionProxyUrl": "",
                "connectionNoProxy": "",
            },
            "backoffLevel": 0,
        })
 
        db.execute(
            """INSERT INTO providerConnections
               (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
            (conn_id, "cloudflare-ai", "apikey",
             f"{ROUTER_CONNECTION_NAME} #{next_priority}", None,
             next_priority, data, now, now),
        )
        db.commit()
        return next_priority
    finally:
        db.close()
 
 
def inject_to_9router(acc):
    """Inject a Cloudflare AI account into the 9router providerConnections table.
 
    Hardened against transient 'malformed'/locked errors caused by WAL
    checkpoint contention with the live 9router process: each attempt opens a
    FRESH connection (a stale conn can keep seeing a malformed WAL snapshot),
    sets busy_timeout, and retries with backoff. Real failures (duplicate row,
    missing data) are not retried.
    """
    if not ROUTER_DB.exists():
        return  # 9router not installed, skip silently
 
    api_token = acc.get("api_token")
    account_id = acc.get("account_id")
    if not api_token or not account_id:
        return  # need both for a working connection
 
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
 
    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        try:
            pr = _inject_once(acc, api_token, account_id, now)
            print(f"    [9router] injected connection #{pr} -> {ROUTER_DB}")
            return
        except sqlite3.IntegrityError as e:
            print(f"    [9router] skip (duplicate?): {e}")
            return  # not transient — retrying won't help
        except sqlite3.Error as e:
            if _is_transient_sqlite_error(e) and attempt < max_attempts:
                backoff = 0.5 * (2 ** (attempt - 1))  # 0.5,1,2,4s
                print(f"    [9router] transient error (attempt {attempt}/{max_attempts}): {e} — retry in {backoff:.1f}s")
                time.sleep(backoff)
                continue
            print(f"    [9router] error (gave up after {attempt}): {e}")
            return
 
 
# --- API-token verification (standalone bearer call) ---
async def verify_token(account_id, api_token, log=print):
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/meta/llama-3.2-1b-instruct"
    async with AsyncSession(impersonate="chrome131") as s:
        resp = await post_json(s, url, {"prompt": "Say hi in one word"},
                               extra={"Authorization": f"Bearer {api_token}"})
    if resp and resp.get("success"):
        log(f"    [check] WORKS: {resp['result'].get('response', '')[:40]}")
        return True
    log(f"    [check] failed: {resp.get('errors') if resp else 'no response'}")
    return False
 
 
# --- One account, one session ---
async def farm_one(captcha_key, log=print, create_retries=6):
    session = None
    email = password = None
    created = None
 
    # Retry the create-half (bootstrap -> challenge -> turnstile -> create) with a fresh
    # session + proxy each time; keep the session that succeeds.
    for attempt in range(1, create_retries + 1):
        s = AsyncSession(impersonate="chrome131", headers=HEADERS)
        proxy = random_proxy()
        if not proxy:
            log("    [proxy] WARNING: proxy.txt kosong, langsung tanpa proxy")
        else:
            log(f"    [create {attempt}/{create_retries}] proxy {proxy}...")
        try:
            boot = await get_json(s, f"{API}/system/bootstrap", proxy=proxy)
            if not (boot and boot.get("success")):
                raise RuntimeError(f"bootstrap {boot}")
            sec_token = boot["result"]["data"]["data"]["security_token"]
            country = boot["result"]["data"].get("ip_country", "id").lower()
 
            chal = await get_json(s, f"{API}/captcha/challenge?context=signup", proxy=proxy)
            sitekey = (chal["result"]["key"] if chal and chal.get("success") and chal.get("result")
                       else FALLBACK_SITEKEY)
 
            log(f"    [create {attempt}/{create_retries}] country={country} — solving Turnstile (Solverify)...")
            token = await solve_turnstile(captcha_key, sitekey, log=log)
            if not token:
                raise RuntimeError("no turnstile token")
 
            email, password = gen_email(), gen_password()
            resp = await post_json(s, f"{API}/user/create", {
                "email": email, "password": password, "mrk_optin": True,
                "security_token": sec_token, "method": "Onboarding: New_v2", "locale": "en-US",
                "legal_stamp": make_legal_stamp(country), "opt_ins": {},
                "mrktCheckboxDisplayed": False, "hCaptchaDisplayed": False,
                "cf_challenge_response": token,
            }, extra={"Referer": "https://dash.cloudflare.com/"}, proxy=proxy if proxy else None)
 
            if resp and resp.get("success"):
                created = resp["result"]
                session = s  # keep this authenticated session
                log(f"          created! user_id={created['id']} email={email}")
                break
            log(f"          create failed: {resp.get('errors') if resp else resp}")
        except Exception as e:
            log(f"          attempt error: {e}")
        await s.close()
        await asyncio.sleep(random.randint(4, 10))
 
    if not created:
        return None
 
    try:
        # trigger the verification email
        await post_json(session, f"{API}/persistence/user", {"emailVerificationRequest": "welcome"})
 
        # poll inbox via IMAP -> verify
        verified = False
        vtok = await get_verification_token(email, log=log)
        if vtok:
            vr = await put_json(session, f"{API}/user/email-verification", {"token": vtok})
            verified = bool(vr and vr.get("success"))
        log(f"    [verify] email_verified={verified}")
 
        # account id
        accts = await get_json(session, f"{API}/accounts?per_page=100")
        account_id = (accts["result"][0]["id"] if accts and accts.get("success") and accts.get("result")
                      else None)
        log(f"    [account] id={account_id}")
 
        # API token -- ONLY after verification (else 400 "Please verify your email")
        api_token = None
        if verified and account_id:
            tk = await post_json(session, f"{API}/user/tokens", {
                "name": "workers-ai", "condition": {},
                "policies": [{"effect": "allow",
                              "resources": {f"com.cloudflare.api.account.{account_id}": "*"},
                              "permission_groups": API_TOKEN_PERMISSIONS}],
            })
            if tk and tk.get("success"):
                api_token = tk["result"]["value"]
                log(f"    [token] {api_token[:20]}...")
                # Propagation delay: token sometimes returns 10000 auth error
                # immediately after creation. Wait a few seconds before verify.
                log("    [token] waiting 12s for propagation...")
                await asyncio.sleep(12)
            else:
                log(f"    [token] failed: {tk.get('errors') if tk else tk}")
        elif not verified:
            log("    [token] skipped (email not verified)")
    finally:
        await session.close()
 
    status = "created"
    if api_token and account_id:
        status = "active" if await verify_token(account_id, api_token, log=log) else "token_unverified"
    elif verified:
        status = "verified_no_token"
 
    acc = {
        "email": email, "password": password, "user_id": created["id"], "account_id": account_id,
        "exit_ip": "direct", "created_at": datetime.now(timezone.utc).isoformat(),
        "email_verified": verified, "api_token": api_token, "neurons_quota": 10000, "status": status,
    }
    save_account(acc)
    return acc
 
 
# --- Batch (sequential — one account at a time) ---
async def farm_batch(captcha_key, count):
    ok = 0
 
    for i in range(count):
        log = lambda msg, i=i: _slog(i + 1, msg)
        log(f" START")
        try:
            acc = await farm_one(captcha_key, log=log)
        except Exception as e:
            log(f"EXCEPTION: {e}")
            continue
        if acc and acc.get("status") == "active":
            ok += 1
            log(f"DONE — {acc['email']} | acct={acc['account_id']} | token={acc['api_token'][:18]}...")
        elif acc:
            log(f"PARTIAL — {acc['email']} | status={acc['status']}")
        else:
            log(f"FAIL")
 
        if i < count - 1:
            await asyncio.sleep(random.uniform(2, 5))
 
    print(f"\n{'='*56}\n[DONE] active: {ok}/{count} -> {ACCOUNTS_FILE}\n{'='*56}")
 
 
def _slog(i, msg):
    """Threadsafe synchronized log line."""
    import sys
    sys.stdout.write(f"[{i:4d}] {msg}\n")
    sys.stdout.flush()
 
 
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--verify-tokens", action="store_true", help="re-check saved accounts' tokens")
    args = ap.parse_args()
 
    captcha_key = get_captcha_key()
    if not captcha_key:
        print("[!] No SOLVERIFY_API_KEY in .env"); return
 
    if args.verify_tokens:
        accs = load_accounts()
        async def _all():
            for a in accs:
                if a.get("api_token") and a.get("account_id"):
                    print(f"[*] {a['email']}")
                    a["status"] = "active" if await verify_token(a["account_id"], a["api_token"]) else "invalid"
            ACCOUNTS_FILE.write_text(json.dumps(accs, indent=2))
        asyncio.run(_all())
        return
 
    asyncio.run(farm_batch(captcha_key, count=args.count))
 
 
if __name__ == "__main__":
    main()