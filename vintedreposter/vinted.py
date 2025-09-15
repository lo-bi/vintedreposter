import base64
import json
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    import jwt  # pyjwt
except Exception:
    jwt = None  # type: ignore
import requests

Json = Dict[str, Any]


class VintedClient:
    def __init__(self, base_url: str = "https://www.vinted.fr", headers: Optional[Dict[str, str]] = None, cookies: Optional[Dict[str, str]] = None):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({
            'accept': 'application/json, text/plain, */*',
        })
        if headers:
            # Don't blindly forward hop-by-hop headers or raw cookie headers
            safe = {k: v for k, v in headers.items() if k.lower() not in {
                'content-length', 'host', 'authority', 'cookie', 'cookies'
            }}
            self.session.headers.update(safe)
        if cookies:
            for k, v in cookies.items():
                self.session.cookies.set(k, v, domain=".vinted.fr")

    def get_user_id(self) -> Optional[int]:
        # Prefer v_uid cookie
        v_uid = self.session.cookies.get('v_uid')
        if v_uid and v_uid.isdigit():
            return int(v_uid)
        # Fallback: parse access_token_web JWT 'sub'
        token = self.session.cookies.get('access_token_web')
        if token and jwt:
            try:
                payload = jwt.decode(token, options={"verify_signature": False, "verify_aud": False})
                sub = payload.get('sub')
                if sub and str(sub).isdigit():
                    return int(sub)
            except Exception:
                pass
        return None

    def wardrobe_items(self, user_id: int, page: int = 1, per_page: int = 20) -> List[Json]:
        url = f"{self.base_url}/api/v2/wardrobe/{user_id}/items"
        params = {"page": page, "per_page": per_page, "order": "relevance"}
        r = self.session.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        # API usually returns {items: [...]} or {catalog_items: [...]}
        return data.get('items') or data.get('catalog_items') or []

    def wardrobe_items_page(self, user_id: int, page: int = 1, per_page: int = 20, order: str = "relevance") -> Tuple[List[Json], Dict[str, Any]]:
        """Fetch a single wardrobe page and return (items, pagination)."""
        url = f"{self.base_url}/api/v2/wardrobe/{user_id}/items"
        params = {"page": page, "per_page": per_page, "order": order}
        r = self.session.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        items = data.get('items') or data.get('catalog_items') or []
        pagination = data.get('pagination') or {}
        return items, pagination

    def wardrobe_items_all(self, user_id: int, per_page: int = 20, order: str = "relevance", max_pages: Optional[int] = None) -> List[Json]:
        """Iterate all wardrobe pages and return the full item list."""
        items_all: List[Json] = []
        page = 1
        total_pages = None
        while True:
            items, pagination = self.wardrobe_items_page(user_id, page=page, per_page=per_page, order=order)
            items_all.extend(items)
            total_pages = pagination.get('total_pages') if isinstance(pagination, dict) else None
            current_page = pagination.get('current_page') if isinstance(pagination, dict) else page
            if max_pages is not None and page >= max_pages:
                break
            if total_pages is None:
                # If pagination missing, stop when fewer than per_page items returned
                if not items or len(items) < per_page:
                    break
            else:
                if current_page >= total_pages:
                    break
            page += 1
        return items_all

    def get_item(self, item_id: int) -> Json:
        url = f"{self.base_url}/api/v2/items/{item_id}"
        r = self.session.get(url)
        r.raise_for_status()
        return r.json().get('item') or {}

    def get_item_upload_details(self, item_id: int, csrf_token: Optional[str] = None) -> Json:
        """Fetch rich item details from the item_upload namespace used by the editor.

        Mirrors the browser request to:
        GET /api/v2/item_upload/items/{id}
        """
        url = f"{self.base_url}/api/v2/item_upload/items/{item_id}"
        headers = {
            'x-enable-multiple-size-groups': 'true',
            'referer': f"{self.base_url}/items/{item_id}/edit",
        }
        if csrf_token:
            headers['x-csrf-token'] = csrf_token
        r = self.session.get(url, headers=headers)
        r.raise_for_status()
        return r.json()

    def create_draft(self, csrf_token: str, payload: Json) -> Json:
        url = f"{self.base_url}/api/v2/item_upload/drafts"
        headers = {
            'content-type': 'application/json',
            'x-csrf-token': csrf_token,
            'x-enable-multiple-size-groups': 'true',
            'origin': self.base_url,
            'referer': f"{self.base_url}/items/new",
        }
        r = self.session.post(url, json=payload, headers=headers)
        r.raise_for_status()
        return r.json()

    def upload_photo(self, csrf_token: str, file_path: str, temp_uuid: str, photo_type: str = 'item') -> Json:
        """Upload a single photo file to Vinted and return the JSON response with photo id.

        This mirrors the browser request to POST /api/v2/photos using multipart/form-data.
        """
        import mimetypes
        url = f"{self.base_url}/api/v2/photos"
        # Build headers; DO NOT set Content-Type manually, requests will set proper boundary
        headers = {
            'accept': 'application/json, text/plain, */*',
            'origin': self.base_url,
            'referer': f"{self.base_url}/items/new",
            'x-csrf-token': csrf_token,
        }
        # Optionally include x-anon-id from a single matching cookie; otherwise skip
        try:
            # Iterate to avoid CookieConflictError when duplicates exist
            anon_vals = [c.value for c in self.session.cookies if c.name == 'anon_id']
            anon = anon_vals[-1] if anon_vals else None
            if anon:
                headers['x-anon-id'] = anon
        except Exception:
            pass

        mime, _ = mimetypes.guess_type(file_path)
        mime = mime or 'application/octet-stream'
        with open(file_path, 'rb') as f:
            files = {
                'photo[file]': (file_path.split('/')[-1], f, mime),
            }
            data = {
                'photo[type]': photo_type,
                'photo[temp_uuid]': temp_uuid,
            }
            r = self.session.post(url, headers=headers, files=files, data=data)
            r.raise_for_status()
            return r.json()

    def publish_draft(self, csrf_token: str, draft_id: int, draft_payload: Json) -> Json:
        """Publish a draft by completing it.

        Mirrors browser: POST /api/v2/item_upload/drafts/{id}/completion with body like
        { "draft": { ... with id set ... }, "feedback_id": null, "push_up": false, "parcel": null, "upload_session_id": "..." }
        """
        url = f"{self.base_url}/api/v2/item_upload/drafts/{draft_id}/completion"
        headers = {
            'content-type': 'application/json',
            'x-csrf-token': csrf_token,
            'x-enable-multiple-size-groups': 'true',
            'origin': self.base_url,
            'referer': f"{self.base_url}/items/{draft_id}/edit",
        }
        r = self.session.post(url, json=draft_payload, headers=headers)
        r.raise_for_status()
        return r.json()

    def delete_item(self, csrf_token: str, item_id: int) -> Json:
        """Delete an existing item before publishing the reposted one.

        Endpoint: POST /api/v2/items/{id}/delete. Empty body, requires CSRF.
        Returns JSON with result or empty.
        """
        url = f"{self.base_url}/api/v2/items/{item_id}/delete"
        headers = {
            'x-csrf-token': csrf_token,
            'origin': self.base_url,
            'referer': f"{self.base_url}/items/{item_id}",
        }
        r = self.session.post(url, headers=headers)
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return {"ok": True}

    def create_item(self, csrf_token: str, payload: Json) -> Json:
        """Directly create an item (skip draft) via item_upload endpoint.

        POST /api/v2/item_upload/items with JSON body of shape:
        { "item": { ... }, "feedback_id": null, "push_up": false, "parcel": null, "upload_session_id": "..." }
        """
        url = f"{self.base_url}/api/v2/item_upload/items"
        headers = {
            'content-type': 'application/json',
            'x-csrf-token': csrf_token,
            'x-enable-multiple-size-groups': 'true',
            'x-upload-form': 'true',
            'origin': self.base_url,
            'referer': f"{self.base_url}/items/new",
        }
        # Optionally include anon id
        try:
            anon_vals = [c.value for c in self.session.cookies if c.name == 'anon_id']
            anon = anon_vals[-1] if anon_vals else None
            if anon:
                headers['x-anon-id'] = anon
        except Exception:
            pass

        r = self.session.post(url, json=payload, headers=headers)
        r.raise_for_status()
        return r.json()
