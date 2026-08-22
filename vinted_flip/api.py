"""Minimal Vinted catalogue client.

Vinted has no official public API. This client uses the same JSON endpoints the
website itself calls, bootstrapping an anonymous session cookie from the
homepage first. It is intended for light, personal use — keep request volume
low, cache results, and respect the site's terms of service.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

log = logging.getLogger(__name__)

DEFAULT_DOMAIN = "www.vinted.co.uk"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


@dataclass
class Listing:
    """One catalogue search result, normalised."""

    id: int
    title: str
    brand: str
    price: float
    currency: str
    size: str
    status: str  # condition label, e.g. "Very good"
    url: str
    photo_url: Optional[str]
    favourite_count: int
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, item: dict[str, Any], domain: str) -> "Listing":
        price_block = item.get("price") or {}
        if isinstance(price_block, dict):
            amount = float(price_block.get("amount") or 0)
            currency = price_block.get("currency_code") or "GBP"
        else:  # older payloads return a bare string
            amount = float(price_block or 0)
            currency = item.get("currency") or "GBP"
        photo = item.get("photo") or {}
        return cls(
            id=int(item.get("id", 0)),
            title=item.get("title") or "",
            brand=item.get("brand_title") or "",
            price=amount,
            currency=currency,
            size=item.get("size_title") or "",
            status=item.get("status") or "",
            url=item.get("url") or f"https://{domain}/items/{item.get('id')}",
            photo_url=photo.get("url"),
            favourite_count=int(item.get("favourite_count") or 0),
            raw=item,
        )


class VintedClient:
    """Anonymous read-only client for Vinted catalogue search."""

    def __init__(self, domain: str = DEFAULT_DOMAIN, delay_seconds: float = 2.0):
        self.domain = domain
        self.base = f"https://{domain}"
        self.delay_seconds = delay_seconds
        self._last_request = 0.0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-GB,en;q=0.9",
            }
        )
        self._bootstrapped = False
        self._csrf_token: Optional[str] = None

    def attach_cookies(self, cookie_header: str) -> None:
        """Attach a logged-in browser session.

        `cookie_header` is the raw Cookie header copied from the browser's
        dev tools while logged in on vinted.co.uk ("name=value; name2=value2").
        This ties requests to that account — keep volume low and human-paced.
        """
        for part in cookie_header.split(";"):
            if "=" in part:
                name, _, value = part.strip().partition("=")
                self.session.cookies.set(name, value, domain=f".{self.domain.removeprefix('www.')}")
        self._bootstrapped = True  # don't overwrite the real session

    def _bootstrap(self) -> None:
        """Hit the homepage once to receive the anonymous session cookies."""
        if self._bootstrapped:
            return
        resp = self.session.get(self.base, timeout=30)
        resp.raise_for_status()
        self._bootstrapped = True

    def _csrf(self) -> Optional[str]:
        """The web app sends an X-CSRF-Token header on writes; it is embedded
        in any page's <meta name="csrf-token"> tag."""
        if self._csrf_token:
            return self._csrf_token
        import re

        resp = self.session.get(self.base, timeout=30)
        m = re.search(
            r'<meta[^>]+name="csrf-token"[^>]+content="([^"]+)"', resp.text
        ) or re.search(r'"CSRF_TOKEN"\s*:\s*"([^"]+)"', resp.text)
        if m:
            self._csrf_token = m.group(1)
        return self._csrf_token

    def whoami(self) -> Optional[str]:
        """Login name of the attached account, or None when anonymous."""
        self._throttle()
        try:
            resp = self.session.get(
                f"{self.base}/api/v2/users/current", timeout=30
            )
            if resp.ok:
                user = resp.json().get("user") or {}
                return user.get("login")
        except requests.RequestException:
            pass
        return None

    def favourite(self, item_id: int) -> bool:
        """Heart a listing so it shows in the account's Favourites tab.
        Requires attach_cookies(). Returns True on success."""
        self._throttle()
        headers = {}
        token = self._csrf()
        if token:
            headers["X-CSRF-Token"] = token
        try:
            resp = self.session.post(
                f"{self.base}/api/v2/user_favourites/toggle",
                json={"type": "item", "entity_id": item_id},
                headers=headers,
                timeout=30,
            )
            if not resp.ok:
                log.warning(
                    "Favourite failed for item %s: HTTP %s", item_id, resp.status_code
                )
            return resp.ok
        except requests.RequestException as exc:
            log.warning("Favourite failed for item %s: %s", item_id, exc)
            return False

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)
        self._last_request = time.monotonic()

    def search(
        self,
        text: str = "",
        brand_ids: Optional[list[int]] = None,
        catalog_ids: Optional[list[int]] = None,
        order: str = "relevance",
        per_page: int = 96,
        page: int = 1,
        price_to: Optional[float] = None,
    ) -> list[Listing]:
        """Search the catalogue. `order` accepts relevance | newest_first |
        price_low_to_high | price_high_to_low."""
        self._bootstrap()
        self._throttle()
        params: dict[str, Any] = {
            "search_text": text,
            "order": order,
            "per_page": per_page,
            "page": page,
        }
        if brand_ids:
            params["brand_ids[]"] = brand_ids
        if catalog_ids:
            params["catalog_ids[]"] = catalog_ids
        if price_to is not None:
            params["price_to"] = price_to

        resp = self.session.get(
            f"{self.base}/api/v2/catalog/items", params=params, timeout=30
        )
        if resp.status_code in (401, 403):
            # Session cookie expired or was rejected — re-bootstrap once.
            log.info("Session rejected (%s); refreshing cookies", resp.status_code)
            self._bootstrapped = False
            self._bootstrap()
            resp = self.session.get(
                f"{self.base}/api/v2/catalog/items", params=params, timeout=30
            )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        return [Listing.from_api(i, self.domain) for i in items]

    def fetch_photo(self, url: str) -> Optional[bytes]:
        """Download a listing photo (thumbnails are fine for quality scoring)."""
        if not url:
            return None
        self._throttle()
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            return resp.content
        except requests.RequestException as exc:
            log.warning("Photo download failed for %s: %s", url, exc)
            return None
