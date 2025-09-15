import re
from typing import Dict, Tuple
from urllib.parse import urlparse

CookieMap = Dict[str, str]
HeaderMap = Dict[str, str]


def parse_curl(curl_text: str) -> Tuple[str, HeaderMap, CookieMap, str]:
    """
    Parse a curl command copied from the browser network tab.

    Returns: (url, headers, cookies, user_agent)
    """
    # Collapse line continuations and normalize whitespace
    text = re.sub(r"\\\s*\\\n\s*", " ", curl_text.strip())
    text = re.sub(r"\s+\\\n", " ", text)

    # URL
    m_url = re.search(r"curl\s+'([^']+)'|curl\s+\"([^\"]+)\"|curl\s+([^\s]+)", text)
    if not m_url:
        raise ValueError("Could not find URL in curl text")
    url = next(g for g in m_url.groups() if g)
    urlparse(url)  # validates

    # Headers (-H 'k: v')
    headers: HeaderMap = {}
    for k, v in re.findall(r"-H\s+'([^:]+):\s*([^']*)'", text):
        headers[k.strip().lower()] = v.strip()

    # Cookies (-b 'a=b; c=d') and/or -H 'cookie: ...'
    cookies: CookieMap = {}
    b_match = re.search(r"-b\s+'([^']+)'|-b\s+\"([^\"]+)\"", text)
    if b_match:
        raw = next(g for g in b_match.groups() if g)
        for part in raw.split(';'):
            if '=' in part:
                name, val = part.split('=', 1)
                cookies[name.strip()] = val.strip()

    # Also merge cookies from a Cookie header if present
    cookie_hdr = None
    for hk, hv in headers.items():
        if hk.lower() == 'cookie':
            cookie_hdr = hv
            break
    if cookie_hdr:
        for part in cookie_hdr.split(';'):
            if '=' in part:
                name, val = part.split('=', 1)
                cookies.setdefault(name.strip(), val.strip())

    user_agent = headers.get('user-agent', '')
    return url, headers, cookies, user_agent
