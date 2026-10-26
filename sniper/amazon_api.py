"""Cliente de la Creators API de Amazon Afiliados (sucesora de PA-API 5).

Necesita las credenciales de Associates Central (Herramientas > Creators API):
ID de credencial ("amzn1.application-oa2-client..."), secret y el ID de
afiliado (partner tag, "xxxx-21") de la tienda que se consulta.

La API da precio, PVP tachado, vendedor y disponibilidad de hasta 10 productos
por petición, pero no dice si la oferta es Prime: eso lo decide el motor.
"""
from __future__ import annotations

import time

import aiohttp

from .config import MARKETPLACES
from .scraper import ProductInfo

# Europa (ES, DE, FR, IT, NL, BE...) = credenciales versión 3.2
TOKEN_URL = "https://api.amazon.co.uk/auth/o2/token"
GET_ITEMS_URL = "https://creatorsapi.amazon/catalog/v1/getItems"
MAX_ITEMS = 10              # ASINs por petición
RESOURCES = [
    "itemInfo.title",
    "offersV2.listings.price",
    "offersV2.listings.availability",
    "offersV2.listings.condition",
    "offersV2.listings.merchantInfo",
    "offersV2.listings.isBuyBoxWinner",
]
UNAVAILABLE = {"OUT_OF_STOCK", "UNAVAILABLE"}


class ApiError(Exception):
    """Fallo de la API; `auth` = credenciales rechazadas, `throttled` = demasiadas peticiones."""

    def __init__(self, msg: str, auth: bool = False, throttled: bool = False):
        super().__init__(msg)
        self.auth, self.throttled = auth, throttled


def _get(data: dict, *keys: str):
    """Lee data[k1][k2]... aceptando camelCase o PascalCase."""
    for key in keys:
        if not isinstance(data, dict):
            return None
        for variant in (key, key[:1].upper() + key[1:], key.upper()):
            if variant in data:
                data = data[variant]
                break
        else:
            return None
    return data


def _amount(money) -> float | None:
    value = _get(money or {}, "amount")
    try:
        return float(value) if value is not None and float(value) > 0 else None
    except (TypeError, ValueError):
        return None


def parse_item(item: dict, market: str) -> ProductInfo:
    asin = _get(item, "asin") or ""
    domain = MARKETPLACES[market][0]
    info = ProductInfo(asin=asin, market=market, url=f"https://{domain}/dp/{asin}", source="api")
    info.title = (_get(item, "itemInfo", "title", "displayValue") or "")[:200] or None
    listings = _get(item, "offersV2", "listings") or []
    new = [l for l in listings if (_get(l, "condition", "value") or "New").lower() == "new"]
    listing = next((l for l in new if _get(l, "isBuyBoxWinner")), new[0] if new else None)
    if listing is None:
        info.status = "no_price"
        return info
    info.price = _amount(_get(listing, "price", "money"))
    info.list_price = _amount(_get(listing, "price", "savingBasis", "money"))
    if info.list_price and info.price and info.list_price <= info.price:
        info.list_price = None
    info.available = (_get(listing, "availability", "type") or "").upper() not in UNAVAILABLE
    info.seller = _get(listing, "merchantInfo", "name") or ""
    # sin dato de Prime en la API: vendido por Amazon = Prime seguro
    info.prime = info.seller.lower().startswith("amazon")
    if info.price is None:
        info.status = "no_price"
    return info


class CreatorsApi:
    def __init__(self, client_id: str, client_secret: str, partner_tag: str, timeout: float = 20.0):
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.partner_tag = partner_tag.strip()
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._token = ""
        self._token_expires = 0.0

    async def _access_token(self, session: aiohttp.ClientSession) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        body = {"grant_type": "client_credentials", "client_id": self.client_id,
                "client_secret": self.client_secret, "scope": "creatorsapi::default"}
        async with session.post(TOKEN_URL, json=body, timeout=self.timeout) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200 or "access_token" not in data:
                raise ApiError(f"credenciales rechazadas ({data.get('error_description') or data.get('error') or resp.status})",
                               auth=True)
        self._token = data["access_token"]
        self._token_expires = time.time() + float(data.get("expires_in", 3600))
        return self._token

    async def get_items(self, session: aiohttp.ClientSession, asins: list[str],
                        market: str) -> dict[str, ProductInfo]:
        """{asin: ProductInfo} de los que la API devuelve; los demás no aparecen."""
        domain = MARKETPLACES[market][0]
        token = await self._access_token(session)
        body = {"itemIds": asins[:MAX_ITEMS], "itemIdType": "ASIN", "marketplace": domain,
                "partnerTag": self.partner_tag, "resources": RESOURCES}
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                   "x-marketplace": domain}
        async with session.post(GET_ITEMS_URL, json=body, headers=headers, timeout=self.timeout) as resp:
            try:
                data = await resp.json(content_type=None)
            except ValueError:
                data = {}
            if resp.status == 429:
                raise ApiError("demasiadas peticiones", throttled=True)
            if resp.status in (401, 403):
                self._token = ""
                raise ApiError(_error_text(data) or f"acceso denegado (HTTP {resp.status})", auth=True)
            if resp.status != 200:
                raise ApiError(_error_text(data) or f"HTTP {resp.status}")
        items = _get(data, "itemsResult", "items") or []
        return {info.asin: info for info in (parse_item(i, market) for i in items) if info.asin}


def _error_text(data: dict) -> str:
    errors = _get(data, "errors") or []
    if errors and isinstance(errors, list):
        first = errors[0]
        return f"{_get(first, 'code') or ''}: {_get(first, 'message') or ''}".strip(": ")
    return str(_get(data, "message") or "")
