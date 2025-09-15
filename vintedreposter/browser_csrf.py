from typing import Dict, Optional
import re

CSRF_REGEX = re.compile(r'\\\"CSRF_TOKEN\\\":\\\"([0-9a-f\\-]{36})\\\"', re.IGNORECASE)

# Try to import botasaurus; fall back if unavailable
try:
    from botasaurus import Driver, driver  # type: ignore
    _BOTASAURUS_AVAILABLE = True
except Exception:
    Driver = None  # type: ignore
    driver = None  # type: ignore
    _BOTASAURUS_AVAILABLE = False


def _inject_cookies(d, cookies: Dict[str, str]):
    # Add cookies before navigation
    for name, value in cookies.items():
        d.add_cookie({
            'name': name,
            'value': value,
            'domain': '.vinted.fr',
            'path': '/',
        })


def _fetch_csrf_via_requests(cookies: Dict[str, str]) -> Optional[str]:
    try:
        import requests
        s = requests.Session()
        for k, v in cookies.items():
            s.cookies.set(k, v, domain='.vinted.fr')
        # Use a realistic UA if present in cookies context; otherwise default
        headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36',
            'referer': 'https://www.vinted.fr/',
        }
        r = s.get('https://www.vinted.fr/items/new', headers=headers)
        r.raise_for_status()
        html = r.text
        m = CSRF_REGEX.search(html)
        if m:
            return m.group(1)
    except Exception:
        return None
    return None


if _BOTASAURUS_AVAILABLE:
    @driver(headless=True)
    def fetch_csrf_with_browser(task) -> Optional[str]:
        ctx = task.ctx
        cookies = ctx.get('cookies', {})
        d = task.driver

        # Navigate to a same-domain page to attach cookies
        d.get('https://www.vinted.fr/')
        _inject_cookies(d, cookies)
        d.get('https://www.vinted.fr/items/new')

        html = d.page_source
        m = CSRF_REGEX.search(html)
        if m:
            return m.group(1)
        return None
else:
    fetch_csrf_with_browser = None  # type: ignore


def extract_csrf(cookies: Dict[str, str]) -> Optional[str]:
    """Get CSRF token either via Botasaurus (if installed) or plain requests fallback."""
    if fetch_csrf_with_browser:
        try:
            token = fetch_csrf_with_browser(ctx={"cookies": cookies})
            if token:
                return token
        except Exception:
            pass
    # Fallback via requests
    return _fetch_csrf_via_requests(cookies)
