from __future__ import annotations

from typing import Dict, Optional
import time
import re

START_URL_DEFAULT = "https://www.vinted.fr/member/signup/select_type?ref_url=https://www.vinted.fr/member/"
WAIT_URL_PREFIX_DEFAULT = "https://www.vinted.fr/member/"

# Optional backends
try:
    from botasaurus import Driver, driver  # type: ignore
    _BOTASAURUS_AVAILABLE = True
except Exception:
    Driver = None  # type: ignore
    driver = None  # type: ignore
    _BOTASAURUS_AVAILABLE = False

try:
    from selenium import webdriver  # type: ignore
    from selenium.webdriver.chrome.options import Options as ChromeOptions  # type: ignore
    _SELENIUM_AVAILABLE = True
except Exception:
    webdriver = None  # type: ignore
    ChromeOptions = None  # type: ignore
    _SELENIUM_AVAILABLE = False


def _collect_cookie_dict(driver_obj) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        cookies = driver_obj.get_cookies()
    except Exception:
        cookies = []
    for c in cookies or []:
        name = c.get('name')
        val = c.get('value')
        if name and val is not None:
            out[name] = val
    return out


def _wait_for_login_and_cookies(driver_obj, wait_url_prefix: str, timeout: int = 180) -> Dict[str, str]:
    infinite = timeout is not None and timeout <= 0
    end = None if infinite else (time.time() + timeout)
    last_url = ""
    while True:
        if end is not None and time.time() >= end:
            break
        try:
            cur = getattr(driver_obj, 'current_url', '')
            last_url = cur or last_url
            cookies = _collect_cookie_dict(driver_obj)
            # Consider logged-in only when a persistent login cookie exists
            logged_in = ('v_uid' in cookies) or ('access_token_web' in cookies)
            # Consider profile URL when it matches /member/<digits> (optionally followed by slug or query)
            on_profile = bool(re.match(r'^https://www\.vinted\.fr/member/\d+', cur))
            if logged_in and on_profile:
                return cookies
        except Exception:
            pass
        time.sleep(1.0)
    # Return whatever cookies we have, even if URL check failed
    return _collect_cookie_dict(driver_obj)


def login_and_get_cookies(start_url: Optional[str] = None, wait_url_prefix: str = WAIT_URL_PREFIX_DEFAULT, timeout: int = 180, keep_open: bool = False) -> Dict[str, str]:
    """Open a browser to Vinted, let user log in, then extract cookies.

    Returns a dict of cookie_name -> value. Prefers Botasaurus if available, falls back to Selenium.
    """
    start_url = start_url or START_URL_DEFAULT

    # If we want to keep the window open, prefer Selenium when available for lifecycle control
    if _BOTASAURUS_AVAILABLE and driver is not None and not (keep_open and _SELENIUM_AVAILABLE and webdriver is not None):
        @driver(headless=False)
        def _run(task):  # type: ignore
            d = task.driver
            d.get(start_url)
            cookies = _wait_for_login_and_cookies(d, wait_url_prefix=wait_url_prefix, timeout=timeout)
            if keep_open:
                # Wait a bit so user can see state
                time.sleep(2)
            return cookies

        res = _run(ctx={})  # type: ignore
        return res or {}

    if not _SELENIUM_AVAILABLE or webdriver is None:
        raise RuntimeError("No browser automation backend available. Install 'botasaurus' or 'selenium'.")

    options = ChromeOptions()
    # Keep visible so user can complete login
    # options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    d = webdriver.Chrome(options=options)
    d.get(start_url)
    cookies = _wait_for_login_and_cookies(d, wait_url_prefix=wait_url_prefix, timeout=timeout)
    # Optionally keep window open to avoid abrupt close before user finishes
    if not keep_open:
        try:
            d.quit()
        except Exception:
            pass
    return cookies
