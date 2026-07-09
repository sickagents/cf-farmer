# cf-farmer

Cloudflare Workers AI account farmer. Auto-creates CF accounts, solves Turnstile CAPTCHA, verifies email, generates API tokens, and injects into 9router.

## Flow

```
bootstrap -> captcha/challenge -> Solverify Turnstile -> user/create ->
email verification (IMAP poll) -> user/email-verification ->
accounts(account_id) -> user/tokens -> verify_token -> inject to 9router
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your credentials
# Add proxies to proxy.txt (one per line)
```

## Usage

```bash
# Farm 1 account
python3 farmer.py --count 1

# Farm 10 accounts
python3 farmer.py --count 10
```

## Requirements

- `.env` — IMAP credentials (Gmail app password), email DOMAIN, SOLVERIFY_API_KEY
- `proxy.txt` — residential proxies (host:port:user:pass format)
- Solverify API key for Turnstile CAPTCHA solving
- Catch-all email domain or disposable email

## Output

- `accounts.json` — all created accounts with email, password, account_id, api_token
- Auto-injects into `~/.9router/db/data.sqlite` as cloudflare-ai provider connections

## Config

| Variable | Description |
|---|---|
| `IMAP_HOST` | IMAP server (default: imap.gmail.com) |
| `IMAP_PORT` | IMAP port (default: 993) |
| `IMAP_USER` | IMAP username |
| `IMAP_PASS` | IMAP password (app password for Gmail) |
| `DOMAIN` | Email domain for generated accounts |
| `SOLVERIFY_API_KEY` | Solverify Turnstile solver API key |
