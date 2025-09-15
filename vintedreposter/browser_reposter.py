from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import os
import tempfile
import time


# Try to import botasaurus and selenium; gracefully degrade if unavailable
try:
    from botasaurus import Driver, driver  # type: ignore
    _BOTASAURUS_AVAILABLE = True
except Exception:
    Driver = None  # type: ignore
    driver = None  # type: ignore
    _BOTASAURUS_AVAILABLE = False

try:
    from selenium.webdriver.common.by import By  # type: ignore
    from selenium.webdriver.common.keys import Keys  # type: ignore
    from selenium.webdriver.support.ui import WebDriverWait  # type: ignore
    from selenium.webdriver.support import expected_conditions as EC  # type: ignore
    from selenium import webdriver  # type: ignore
    from selenium.webdriver.chrome.options import Options as ChromeOptions  # type: ignore
    _SELENIUM_AVAILABLE = True
except Exception:
    By = None  # type: ignore
    Keys = None  # type: ignore
    WebDriverWait = None  # type: ignore
    EC = None  # type: ignore
    webdriver = None  # type: ignore
    ChromeOptions = None  # type: ignore
    _SELENIUM_AVAILABLE = False


@dataclass
class RepostItemData:
    item_id: int
    title: str
    description: str
    price: Optional[float]
    currency: Optional[str]
    brand: Optional[str]
    brand_id: Optional[int]
    size_id: Optional[int]
    catalog_id: Optional[int]
    status_id: Optional[int]
    color_ids: List[int]
    photo_urls: List[str]


def _inject_cookies(d, cookies: Dict[str, str], headers: Optional[Dict[str, str]] = None):
    # Add cookies before navigation
    for name, value in cookies.items():
        try:
            d.add_cookie({
                'name': name,
                'value': value,
                'domain': '.vinted.fr',
                'path': '/',
            })
        except Exception:
            # Some cookies cannot be set (httpOnly, sameSite); ignore failures
            pass
    # Also parse Cookie header if provided
    if headers:
        cookie_str = None
        for k, v in headers.items():
            if k.lower() == 'cookie':
                cookie_str = v
                break
        if cookie_str:
            for part in cookie_str.split(';'):
                if '=' in part:
                    name, val = part.split('=', 1)
                    name = name.strip()
                    val = val.strip()
                    if name and val:
                        try:
                            d.add_cookie({
                                'name': name,
                                'value': val,
                                'domain': '.vinted.fr',
                                'path': '/',
                            })
                        except Exception:
                            pass


def _first(elist):
    return elist[0] if elist else None


def _download_photos(photo_urls: List[str], headers: Optional[Dict[str, str]] = None, cookies: Optional[Dict[str, str]] = None) -> List[str]:
    """Download photo URLs to a temp folder; return absolute file paths."""
    if not photo_urls:
        return []
    try:
        import requests
    except Exception:
        return []
    out_files: List[str] = []
    temp_dir = tempfile.mkdtemp(prefix="vinted_photos_")
    s = requests.Session()
    if cookies:
        for k, v in cookies.items():
            try:
                s.cookies.set(k, v, domain='.vinted.fr')
            except Exception:
                pass
    if headers:
        s.headers.update({k: v for k, v in headers.items() if k.lower() not in {'cookie'}})
    for i, url in enumerate(photo_urls):
        try:
            r = s.get(url, timeout=20)
            r.raise_for_status()
            ext = '.jpg'
            ct = r.headers.get('content-type', '')
            if 'png' in ct:
                ext = '.png'
            elif 'jpeg' in ct:
                ext = '.jpg'
            elif 'webp' in ct:
                ext = '.webp'
            path = os.path.join(temp_dir, f"photo_{i+1}{ext}")
            with open(path, 'wb') as f:
                f.write(r.content)
            out_files.append(path)
        except Exception:
            continue
    return out_files


def _extract_photo_urls(item: Dict[str, Any]) -> List[str]:
    urls: List[str] = []
    photos = item.get('photos') or item.get('item_photos') or []
    if isinstance(photos, list):
        for p in photos:
            # Try a range of common keys
            u = (
                p.get('full_size_url') or p.get('url') or p.get('image_url') or p.get('original_url') or p.get('original')
            )
            if not u and isinstance(p.get('formats'), dict):
                # Pick a large format if present
                fmts = p.get('formats')
                for key in ['xxl', 'xl', 'l', 'm', 'original']:
                    if key in fmts and isinstance(fmts[key], dict):
                        u = fmts[key].get('url')
                        if u:
                            break
            if isinstance(u, str):
                urls.append(u)
    return urls


def collect_item_data(base_item: Dict[str, Any], detailed_item: Optional[Dict[str, Any]] = None) -> RepostItemData:
    src: Dict[str, Any] = {**(base_item or {}), **(detailed_item or {})}
    # Price + currency
    amount = src.get('price_numeric')
    currency = src.get('price_currency') or src.get('currency')
    p = src.get('price')
    if amount is None:
        if isinstance(p, dict):
            amount = p.get('amount')
            currency = currency or p.get('currency_code')
        elif isinstance(p, (int, float)):
            amount = p
    if isinstance(amount, str):
        try:
            amount = float(amount.replace(',', '.'))
        except Exception:
            amount = None

    return RepostItemData(
        item_id=int(src.get('id')),
        title=src.get('title') or "",
        description=src.get('description') or "",
        price=amount if isinstance(amount, (int, float)) else None,
        currency=currency,
        brand=src.get('brand_title') or src.get('brand'),
        brand_id=src.get('brand_id'),
        size_id=src.get('size_id'),
        catalog_id=src.get('catalog_id'),
        status_id=src.get('status_id'),
        color_ids=src.get('color_ids') or [],
        photo_urls=_extract_photo_urls(src),
    )


def _find_first(driver, selectors: List[str]):
    if not By:
        return None
    for sel in selectors:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el:
                return el
        except Exception:
            continue
    return None


def _type_value(driver, selectors: List[str], value: str) -> bool:
    el = _find_first(driver, selectors)
    if not el:
        return False
    try:
        el.clear()
    except Exception:
        pass
    try:
        el.send_keys(value)
        return True
    except Exception:
        return False


def _upload_files(driver, selectors: List[str], files: List[str]) -> bool:
    if not files:
        return False
    el = _find_first(driver, selectors)
    if not el:
        return False
    try:
        # Many file inputs support multiple files separated by newline
        el.send_keys("\n".join(files))
        return True
    except Exception:
        return False


def _click_save_draft(driver) -> bool:
    if not By:
        return False
    candidates = [
        (By.XPATH, "//button[contains(., 'Sauvegarder le brouillon')]") ,
        (By.XPATH, "//button[contains(., 'brouillon')]") ,
        (By.XPATH, "//button[contains(., 'Save draft')]") ,
        (By.CSS_SELECTOR, "button[type='submit']"),
    ]
    for by, sel in candidates:
        try:
            btn = driver.find_element(by, sel)
            if btn:
                btn.click()
                return True
        except Exception:
            continue
    return False


def _run_repost_flow(driver_obj, cookies: Dict[str, str], headers: Dict[str, str], data: RepostItemData) -> Dict[str, Any]:
    # Navigate and attach cookies
    driver_obj.get('https://www.vinted.fr/')
    _inject_cookies(driver_obj, cookies, headers)
    driver_obj.get('https://www.vinted.fr/items/new')

    # Optionally wait for the page to load a key element
    try:
        if WebDriverWait and By and EC:
            WebDriverWait(driver_obj, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "body"))
            )
    except Exception:
        pass

    # Upload photos first so they start processing
    files = _download_photos(data.photo_urls, headers=headers, cookies=cookies)
    _upload_files(driver_obj, [
        "input[type='file'][multiple]",
        "input[type='file'][accept*='image']",
        "input[type='file']",
    ], files)

    # Fill title, description, and price
    _type_value(driver_obj, [
        "input[name='title']",
        "input[id*='title']",
        "textarea[name='title']",
        "input[placeholder*='Titre']",
        "input[placeholder*='title' i]",
    ], data.title)

    _type_value(driver_obj, [
        "textarea[name='description']",
        "textarea[id*='description']",
        "textarea[placeholder*='Description' i]",
    ], data.description)

    if data.price is not None:
        _type_value(driver_obj, [
            "input[name='price']",
            "input[id*='price']",
            "input[placeholder*='Prix' i]",
            "input[aria-label*='Prix' i]",
        ], str(data.price))

    # Try to click Save draft
    saved = _click_save_draft(driver_obj)
    time.sleep(3)

    return {
        "ok": bool(saved),
        "saved": bool(saved),
        "current_url": getattr(driver_obj, 'current_url', ''),
        "photos": len(files),
    }


if _BOTASAURUS_AVAILABLE:
    @driver(headless=False)
    def _repost_with_browser(task) -> Dict[str, Any]:
        ctx = task.ctx
        cookies: Dict[str, str] = ctx.get('cookies', {})
        headers: Dict[str, str] = ctx.get('headers', {})
        data: RepostItemData = ctx.get('data')
        d = task.driver
        return _run_repost_flow(d, cookies, headers, data)
else:
    _repost_with_browser = None  # type: ignore


def create_draft_via_browser(cookies: Dict[str, str], headers: Dict[str, str], base_item: Dict[str, Any], detailed_item: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """High-level API to run the browser repost flow. Returns a dict with result info."""
    data = collect_item_data(base_item, detailed_item)

    # Prefer Botasaurus if available
    if _BOTASAURUS_AVAILABLE and _repost_with_browser is not None:
        return _repost_with_browser(ctx={
            "cookies": cookies,
            "headers": headers,
            "data": data,
        })

    # Fallback to Selenium WebDriver if available
    if not _SELENIUM_AVAILABLE or webdriver is None:
        raise RuntimeError("No browser automation backend available. Install 'botasaurus' or 'selenium'.")

    options = ChromeOptions()
    # Keep visible for now to help diagnose; switch to headless if desired
    # options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    driver_obj = webdriver.Chrome(options=options)
    try:
        return _run_repost_flow(driver_obj, cookies, headers, data)
    finally:
        try:
            driver_obj.quit()
        except Exception:
            pass
