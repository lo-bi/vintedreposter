# Vinted Item Cloner

Small CLI app to clone/duplicate your Vinted listings. It reuses your authenticated browser cURL to act as you and creates a brand‑new item with the same content and photos.

What it does:

- Parses a pasted cURL to extract headers and cookies (no storage, local only)
- Fetches ALL your wardrobe items across pages
- Shows title, price, age in days, favorites, and views (oldest first)
- Re‑uploads photos and directly creates a new item via Vinted API (skips drafts)
- Optionally deletes the original item before creating the clone
- Optional browser login helper to grab fresh cookies

## Prerequisites

- Python 3.10+
- A copied cURL from your browser's Network tab after you're logged in on vinted.fr (any same-origin API call works). The cURL must include cookies like `v_uid`, `access_token_web`, etc.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

Paste your cURL into a file, e.g. `auth.curl`, or pipe it via stdin.

```bash
# Basic: use your captured cURL
python main.py auth.curl

# Or pipe via stdin
cat auth.curl | python main.py

# Optional: open a browser window to log in and reuse cookies
python main.py auth.curl --login-browser --login-timeout 0

# Keep the login window open after capture (Selenium recommended)
python main.py auth.curl --login-browser --keep-login-browser --login-timeout 0
```

- The CLI lists your items (title, price, days, favorites, views) sorted by oldest.
- Pick one to clone; photos are uploaded first, then a fresh item is created.
- You can choose to delete the original item before cloning.

## Notes

- This tool uses your cookies only locally. It does not store anything beyond the current run.
- CSRF token is extracted from `https://www.vinted.fr/items/new` and attached to API calls.
- Some listing fields may still need manual inputs (e.g., mandatory brand/size/catalog/status if missing).
- Vinted may employ anti-bot protections (Cloudflare, DataDome). Using your own cookies and running soon after capturing the cURL increases success.
