import argparse
import json
import sys
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from rich import print
from rich.table import Table

from .curl_parser import parse_curl
from .vinted import VintedClient
from .browser_csrf import extract_csrf
from .browser_reposter import create_draft_via_browser
from .browser_login import login_and_get_cookies
import os
import tempfile
import uuid


def _extract_price_currency(item: Dict[str, Any]):
    # Try price_numeric first
    amount = item.get('price_numeric')
    currency = item.get('price_currency') or item.get('currency')
    p = item.get('price')
    if amount is None:
        if isinstance(p, dict):
            amount = p.get('amount')
            currency = currency or p.get('currency_code')
        elif isinstance(p, (int, float)):
            amount = p
        elif isinstance(p, str):
            amount = p
    return amount, currency


def _parse_created_at(item: Dict[str, Any]) -> Optional[datetime]:
    """Try to extract a timezone-aware datetime for item creation."""
    # Prefer created_at_ts if trustworthy
    ts = item.get('created_at_ts')
    if isinstance(ts, (int, float)):
        # Heuristic: ms vs s
        sec = ts / 1000.0 if ts > 10_000_000_000 else ts
        try:
            return datetime.fromtimestamp(sec, tz=timezone.utc)
        except Exception:
            pass
    # Try nested item.created_at from editor payload
    created_at = item.get('created_at') or (item.get('item') or {}).get('created_at')
    if isinstance(created_at, str) and created_at:
        s = created_at.strip()
        # Normalize timezone
        if s.endswith('Z'):
            s = s.replace('Z', '+00:00')
        # Some APIs return like '2025-09-15T14:33:00+02:00'
        try:
            dt = datetime.fromisoformat(s)
            # Ensure tz-aware
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
    # Fallback: derive from earliest photo high_resolution.timestamp
    photos = item.get('photos')
    if isinstance(photos, list) and photos:
        best_ts = None
        for p in photos:
            if not isinstance(p, dict):
                continue
            hr = p.get('high_resolution') or {}
            pts = hr.get('timestamp')
            if isinstance(pts, (int, float)):
                if best_ts is None or pts < best_ts:
                    best_ts = pts
        if isinstance(best_ts, (int, float)):
            try:
                return datetime.fromtimestamp(best_ts, tz=timezone.utc)
            except Exception:
                pass
    return None


def _days_since_created(item: Dict[str, Any]) -> str:
    dt = _parse_created_at(item)
    if not dt:
        return "?"
    now = datetime.now(tz=timezone.utc)
    delta = now - dt
    # Round down to whole days, minimum 0
    days = max(int(delta.total_seconds() // 86400), 0)
    return str(days)


def render_items_table(items: List[Dict[str, Any]]):
    table = Table(title="Vinted items")
    table.add_column("#", style="cyan", justify="right")
    table.add_column("ID", style="green")
    table.add_column("Title")
    table.add_column("Price", justify="right")
    table.add_column("Days", justify="right")
    table.add_column("Favs", justify="right")
    table.add_column("Views", justify="right")

    for idx, it in enumerate(items, 1):
        amount, curr = _extract_price_currency(it)
        price = f"{amount} {curr}".strip() if amount is not None else ""
        title = it.get('title') or it.get('brand_title') or ''
        days = _days_since_created(it)
        favs = it.get('favorite_count') or it.get('favourite_count') or it.get('favorites_count') or it.get('favourites_count') or 0
        views = it.get('view_count') or it.get('views') or 0
        table.add_row(str(idx), str(it.get('id')), str(title), str(price), str(days), str(favs), str(views))
    print(table)


def prompt_yes_no(msg: str) -> bool:
    ans = input(f"{msg} [y/N]: ").strip().lower()
    return ans in {"y", "yes"}


def main():
    parser = argparse.ArgumentParser(description="Repost old Vinted ads from a pasted cURL")
    parser.add_argument("curl_file", nargs="?", help="Path to a text file containing the copied cURL request.")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--per-page", type=int, default=20)
    parser.add_argument("--browser", action="store_true", help="Use a real browser (Botasaurus) to fill the form and save a draft.")
    parser.add_argument("--login-browser", action="store_true", help="Open a browser for Vinted login and reuse extracted cookies.")
    parser.add_argument("--login-timeout", type=int, default=180, help="Seconds to wait for login to complete in browser mode (0 for no timeout).")
    parser.add_argument("--keep-login-browser", action="store_true", help="Keep the login browser window open after cookies are captured.")
    args = parser.parse_args()

    if args.curl_file:
        with open(args.curl_file, 'r', encoding='utf-8') as f:
            curl_text = f.read()
    else:
        print("Paste your cURL command, then Ctrl-D (Linux/macOS) or Ctrl-Z Enter (Windows):")
        curl_text = sys.stdin.read()

    url, headers, cookies, user_agent = parse_curl(curl_text)

    # Optional interactive login flow to grab fresh cookies
    if args.login_browser:
        # Allow user-specific redirect by inferring target member path if v_uid present in parsed cookies
        member_id = cookies.get('v_uid') if isinstance(cookies.get('v_uid'), str) else None
        start_url = f"https://www.vinted.fr/member/signup/select_type?ref_url=https://www.vinted.fr/member/{member_id or ''}"
        try:
            b_cookies = login_and_get_cookies(
                start_url=start_url,
                wait_url_prefix="https://www.vinted.fr/member/",
                timeout=args.login_timeout,
                keep_open=args.keep_login_browser,
            )
            # Merge into parsed cookies (browser cookies take precedence)
            cookies.update(b_cookies or {})
        except Exception as e:
            print(f"[yellow]Browser login failed or unavailable: {e}[/yellow] Continuing with provided cookies.")

    client = VintedClient(headers=headers, cookies=cookies)
    # If member_id was unknown, we can now attempt to infer again from client cookie jar
    user_id = client.get_user_id()
    if not user_id:
        print("[red]Could not determine user_id from cookies/JWT. Ensure v_uid or access_token_web present.[/red]")
        sys.exit(1)

    # Fetch all pages and then sort by oldest
    items = client.wardrobe_items_all(user_id, per_page=args.per_page, order="relevance")
    if not items:
        print("[yellow]No items found or request blocked.[/yellow]")
        sys.exit(0)

    # Enrich each item with favorites, views, and created_at using the item/details endpoints if missing
    csrf_list = None
    try:
        csrf_list = extract_csrf(cookies)
    except Exception:
        csrf_list = None
    enriched = []
    for it in items:
        need_stats = not any(k in it for k in ("favorite_count", "favourite_count", "view_count", "views"))
        need_created = not any(k in it for k in ("created_at", "created_at_ts"))
        if need_stats or need_created:
            try:
                det = client.get_item(int(it.get('id')))
                if isinstance(det, dict):
                    # Merge non-empty stats into the item
                    for k in ("favorite_count", "favourite_count", "view_count", "views", "created_at", "created_at_ts"):
                        if det.get(k) is not None:
                            it[k] = det.get(k)
            except Exception:
                pass
        # If still missing created date, try editor details (requires CSRF)
        if not any(k in it for k in ("created_at", "created_at_ts")) and csrf_list:
            try:
                ed = client.get_item_upload_details(int(it.get('id')), csrf_list)
                if isinstance(ed, dict):
                    # Prefer nested item.created_at
                    created_nested = (ed.get('item') or {}).get('created_at')
                    if created_nested and 'created_at' not in it:
                        it['created_at'] = created_nested
                    if 'created_at_ts' in ed and ed.get('created_at_ts') is not None:
                        it['created_at_ts'] = ed.get('created_at_ts')
            except Exception:
                pass
        enriched.append(it)

    # Sort by oldest first using parsed creation date; unknown dates go last
    def _sort_key(x: Dict[str, Any]):
        dt = _parse_created_at(x)
        return (0, dt) if dt else (1, datetime.max.replace(tzinfo=timezone.utc))

    enriched.sort(key=_sort_key)

    render_items_table(enriched)

    idx_raw = input("Select an item number to repost (or blank to exit): ").strip()
    if not idx_raw:
        return
    try:
        idx = int(idx_raw)
    except ValueError:
        print("[red]Invalid selection[/red]")
        return
    if not (1 <= idx <= len(enriched)):
        print("[red]Out of range[/red]")
        return

    item = enriched[idx - 1]
    amount, curr = _extract_price_currency(item)
    print(f"Selected: {item.get('title')} (ID {item.get('id')}) price={amount} {curr or ''}")

    if not prompt_yes_no("Repost this item as a new draft?"):
        return

    # If user opted for browser mode, attempt that path first
    if args.browser:
        try:
            # Try to fetch richer details if possible without CSRF first
            details = {}
            try:
                csrf_tmp = extract_csrf(cookies)
                if csrf_tmp:
                    details = client.get_item_upload_details(int(item.get('id')), csrf_tmp)
            except Exception:
                details = {}
            res = create_draft_via_browser(cookies=cookies, headers=headers, base_item=item, detailed_item=details)
            if res.get("ok"):
                print("[green]Draft saved via browser automation.[/green]")
                print(json.dumps(res, indent=2, ensure_ascii=False))
                return
            else:
                print("[yellow]Browser automation did not confirm save. Falling back to API path...[/yellow]")
        except Exception as e:
            print(f"[yellow]Browser mode failed: {e}. Falling back to API path...[/yellow]")

    # Extract CSRF using browser if available, otherwise requests fallback
    csrf = extract_csrf(cookies)
    if not csrf:
        print("[red]Failed to extract CSRF token from /items/new[/red]")
        sys.exit(1)
    # Fetch richer item details from the item_upload endpoint (editor payload)
    try:
        details = client.get_item_upload_details(int(item.get('id')), csrf)
    except Exception:
        details = {}

    # Merge base list item with details, details take precedence
    src = {**(item or {}), **(details or {}), **(details.get('item') or {})}

    # Normalize price and currency
    price_val = src.get('price_numeric')
    if price_val is None:
        p = src.get('price')
        if isinstance(p, dict):
            amount = p.get('amount')
            try:
                price_val = float(amount) if amount is not None else None
            except Exception:
                price_val = None
        elif isinstance(p, (int, float)):
            price_val = p
    currency = src.get('price_currency') or src.get('currency')
    if currency is None and isinstance(src.get('price'), dict):
        currency = src['price'].get('currency_code')

    # Build photo assignments by re-uploading photos so they are bound to temp_uuid
    assigned_photos = []
    # Generate a fresh UUID for this new draft/upload session
    upload_temp_uuid = str(uuid.uuid4())
    # Prefer photos list from details or base item
    photos = []
    if isinstance(details.get('item', {}).get('photos'), list):
        photos = details['item']['photos']
    elif isinstance(details.get('photos'), list):
        photos = details['photos']
    elif isinstance(src.get('photos'), list):
        photos = src['photos']

    def _extract_url(p):
        if not isinstance(p, dict):
            return None
        return p.get('full_size_url') or p.get('url') or p.get('image_url') or (
            isinstance(p.get('formats'), dict) and next((v.get('url') for k, v in p['formats'].items() if isinstance(v, dict) and v.get('url')), None)
        )

    # Download to tmp and upload via API
    if photos:
        import requests
        tmpdir = tempfile.mkdtemp(prefix="vinted_upload_")
        for i, p in enumerate(photos):
            u = _extract_url(p)
            if not u:
                continue
            try:
                # Reuse the client's session to preserve cookies as-is
                s = client.session
                r = s.get(u, timeout=20)
                r.raise_for_status()
                fname = os.path.join(tmpdir, f"img_{i+1}.jpg")
                with open(fname, 'wb') as f:
                    f.write(r.content)
                try:
                    up = client.upload_photo(csrf, fname, upload_temp_uuid, photo_type='item')
                except requests.exceptions.HTTPError as e2:
                    body = getattr(e2.response, 'text', '') if hasattr(e2, 'response') else ''
                    print(f"[yellow]Upload attempt failed for {fname}: {e2} {body}[/yellow]")
                    continue
                except Exception as e2:
                    print(f"[yellow]Upload attempt failed for {fname}: {e2}[/yellow]")
                    continue
                pid = up.get('id')
                if pid:
                    assigned_photos.append({"id": pid, "orientation": up.get('orientation') or 0})
            except Exception as e:
                print(f"[yellow]Photo upload failed for {u}: {e}[/yellow]")

    # Build payload for direct item creation
    item_payload = {
        "item": {
            "id": None,
            "currency": currency or "EUR",
            "temp_uuid": upload_temp_uuid,
            "title": src.get('title') or "",
            "description": src.get('description') or "",
            "brand_id": src.get('brand_id'),
            "brand": src.get('brand_title') or src.get('brand'),
            "size_id": src.get('size_id'),
            "catalog_id": src.get('catalog_id'),
            "isbn": None,
            "is_unisex": bool(src.get('is_unisex')),
            "status_id": src.get('status_id') or 1,
            "video_game_rating_id": None,
            "price": price_val or 0,
            "package_size_id": src.get('package_size_id') or 1,
            "shipment_prices": {"domestic": None, "international": None},
            "color_ids": src.get('color_ids') or [],
            "assigned_photos": assigned_photos,
            "measurement_length": src.get('measurement_length'),
            "measurement_width": src.get('measurement_width'),
            "item_attributes": src.get('item_attributes') or [],
            "manufacturer": src.get('manufacturer'),
            "manufacturer_labelling": src.get('manufacturer_labelling'),
        },
        "feedback_id": None,
        "push_up": False,
        "parcel": None,
        "upload_session_id": upload_temp_uuid,
    }

    # Prompt for critical missing fields that the API often requires
    missing_prompts = [
        ("brand_id", "Enter brand_id (numeric) or leave blank:", int),
        ("size_id", "Enter size_id (numeric) or leave blank:", int),
        ("catalog_id", "Enter catalog_id (numeric) or leave blank:", int),
        ("status_id", "Enter status_id (1=new with tag, 2=new without tag, 3=very good, 4=good, 5=satisfactory) or leave blank:", int),
    ]
    for key, prompt, caster in missing_prompts:
        if item_payload["item"].get(key) in (None, ""):
            val = input(prompt + " ").strip()
            if val:
                try:
                    item_payload["item"][key] = caster(val)
                except Exception:
                    print(f"[yellow]Ignored invalid {key} value[/yellow]")

    # Offer to delete the original item before creating the new one
    if item_payload["item"].get("assigned_photos"):
        if prompt_yes_no("Delete the original item before creating the repost? This cannot be undone."):
            try:
                del_res = client.delete_item(csrf, int(item.get('id')))
                print("[green]Original item deleted[/green]")
                if del_res:
                    print(json.dumps(del_res, indent=2, ensure_ascii=False))
            except Exception as e:
                print(f"[yellow]Failed to delete original item: {e}[/yellow]")

    try:
        created = client.create_item(csrf, item_payload)
        print("[green]Item created[/green]")
        print(json.dumps(created, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"[red]Item creation failed:[/red] {e}")
        sys.exit(2)
