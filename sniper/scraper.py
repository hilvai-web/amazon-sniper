"""Descarga y análisis de fichas de producto de Amazon.

El parser es tolerante: Amazon cambia el HTML a menudo, así que se prueban
varios selectores conocidos en orden y se cae al siguiente si uno falla.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup

try:  # lxml es ~5x más rápido; html.parser es el respaldo sin dependencias
    import lxml  # noqa: F401
    _PARSER = "lxml"
except ImportError:  # pragma: no cover
    _PARSER = "html.parser"

ASIN_RE = re.compile(r"\b(B0[A-Z0-9]{8}|\d{9}[\dX])\b")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
]

PRICE_SELECTORS = [
    "#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen",
    "#corePrice_feature_div .a-price:not(.a-text-price) .a-offscreen",
    "#corePriceDisplay_desktop_feature_div .a-price:not(.a-text-price) .a-offscreen",
    "#tp_price_block_total_price_ww .a-offscreen",
    "#apex_offerDisplay_desktop .a-price:not(.a-text-price) .a-offscreen",
    "#apex_desktop .a-price:not(.a-text-price) .a-offscreen",
    ".priceToPay .a-offscreen",
    ".apexPriceToPay .a-offscreen",
    "#priceblock_dealprice",
    "#priceblock_ourprice",
    "#price_inside_buybox",
    "#newBuyBoxPrice",
    "#kindle-price",
    ".a-price.aok-align-center .a-offscreen",
]
# Sin vendedor destacado Amazon solo enseña "Ver todas las opciones de compra"
# con el precio más bajo de otros vendedores
OTHER_SELLERS_SELECTORS = [
    "#buybox-see-all-buying-choices .a-price .a-offscreen",
    "#buybox-see-all-buying-choices",
    "#olp_feature_div .a-price .a-offscreen",
    "#olp_feature_div",
    "#all-offers-display .a-price .a-offscreen",
]
PRICE_AMOUNT_RE = re.compile(r'"priceAmount"\s*:\s*(\d+(?:\.\d+)?)')
EURO_RE = re.compile(r"(\d{1,3}(?:[.\s\xa0]\d{3})*,\d{2}|\d+,\d{2})\s*€")

LIST_PRICE_SELECTORS = [
    "#corePriceDisplay_desktop_feature_div .basisPrice .a-offscreen",
    "#corePrice_feature_div .basisPrice .a-offscreen",
    "#corePriceDisplay_desktop_feature_div .a-price.a-text-price .a-offscreen",
    "#corePrice_desktop .a-text-price .a-offscreen",
    "#apex_desktop .a-text-price .a-offscreen",
    "#listPrice",
    "#priceblock_listprice",
]


@dataclass
class ProductInfo:
    asin: str
    market: str
    url: str
    title: str | None = None
    price: float | None = None
    list_price: float | None = None
    available: bool = True
    status: str = "ok"          # ok | captcha | not_found | no_price | error


class Blocked(Exception):
    """Amazon devolvió captcha o 503: hay que frenar (o cambiar de proxy)."""

    def __init__(self, msg: str, proxy: str | None = None):
        super().__init__(msg)
        self.proxy = proxy


def load_asins(path: str | Path) -> list[str]:
    """Extrae ASINs únicos de un .txt (uno por línea, o mezclados con texto/URLs)."""
    text = Path(path).read_text(encoding="utf-8", errors="ignore").upper()
    seen: dict[str, None] = {}
    for match in ASIN_RE.findall(text):
        seen.setdefault(match, None)
    return list(seen)


def load_proxies(path: str | Path) -> list[str]:
    """Proxies del usuario, uno por línea: host:puerto o http://user:pass@host:puerto."""
    raw = Path(path).read_bytes()
    # el Bloc de notas puede guardar en UTF-16 ("Unicode") o con BOM
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig", "ignore")
    proxies: dict[str, None] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit() and "://" not in parts[0]:
            line = f"{parts[0]}:{parts[1]}"          # "1.2.3.4 8080" o con tabulador
        else:
            line = parts[0]
        # las listas marcan "https" a los proxies que admiten webs https, pero
        # la conexión con el proxy en sí es HTTP normal
        if line.lower().startswith("https://"):
            line = "http://" + line[8:]
        if "://" not in line:
            line = "http://" + line
        if line.lower().startswith("socks"):
            continue                                  # SOCKS no está soportado
        proxies.setdefault(line, None)
    return list(proxies)


def parse_price(text: str | None) -> float | None:
    """Convierte '1.234,56 €', '€1,234.56' o '19,99' a float."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d,.]", "", text)
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        # el último separador es el decimal
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        whole, _, dec = cleaned.rpartition(",")
        cleaned = (whole.replace(",", "") + "." + dec) if len(dec) <= 2 else cleaned.replace(",", "")
    elif cleaned.count(".") > 1 or (cleaned.count(".") == 1 and len(cleaned.split(".")[1]) == 3):
        cleaned = cleaned.replace(".", "")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if value > 0 else None


def _node_price(node) -> float | None:
    """Precio de un nodo. Amazon a veces deja vacío el .a-offscreen y pone el
    importe solo en .a-price-whole + .a-price-fraction."""
    text = node.get_text(strip=True)
    value = parse_price(text) if len(text) < 40 else None
    if value:
        return value
    box = node if "a-price" in (node.get("class") or []) else node.find_parent(class_="a-price")
    if box is not None:
        whole, frac = box.select_one(".a-price-whole"), box.select_one(".a-price-fraction")
        if whole:
            digits = re.sub(r"\D", "", whole.get_text())
            cents = re.sub(r"\D", "", frac.get_text()) if frac else "00"
            if digits:
                return float(f"{digits}.{cents or '00'}") or None
    m = EURO_RE.search(text.replace("\xa0", " "))
    return parse_price(m.group(1)) if m else None


def _first_price(soup: BeautifulSoup, selectors: list[str]) -> float | None:
    for sel in selectors:
        for node in soup.select(sel):
            value = _node_price(node)
            if value:
                return value
    return None


def _hidden_price(soup: BeautifulSoup, html: str) -> float | None:
    """Precio de la oferta destacada en los datos ocultos del formulario de compra."""
    for node in soup.select('input[name*="customerVisiblePrice"][name$="[amount]"]'):
        try:
            value = float(node.get("value", ""))
        except ValueError:
            continue
        if value > 0:
            return value
    m = PRICE_AMOUNT_RE.search(html)
    return float(m.group(1)) or None if m else None


def is_captcha(html: str) -> bool:
    low = html[:20000].lower()
    return "validatecaptcha" in low or "captchacharacters" in low or "/errors/validatecaptcha" in low


def parse_product(html: str, asin: str, market: str, url: str) -> ProductInfo:
    info = ProductInfo(asin=asin, market=market, url=url)
    if is_captcha(html):
        info.status = "captcha"
        return info

    soup = BeautifulSoup(html, _PARSER)
    title = soup.select_one("#productTitle") or soup.select_one("#title")
    if title is None:
        info.status = "not_found"
        return info
    info.title = " ".join(title.get_text(" ", strip=True).split())[:200]

    availability = soup.select_one("#availability")
    if availability:
        av = availability.get_text(" ", strip=True).lower()
        if any(k in av for k in ("no disponible", "nicht verfügbar", "indisponible",
                                 "non disponibile", "currently unavailable", "niet beschikbaar")):
            info.available = False

    info.price = (_first_price(soup, PRICE_SELECTORS) or _hidden_price(soup, html)
                  or _first_price(soup, OTHER_SELLERS_SELECTORS))
    info.list_price = _first_price(soup, LIST_PRICE_SELECTORS)
    if info.list_price and info.price and info.list_price <= info.price:
        info.list_price = None  # tachado inválido o igual al precio actual
    if info.price is None:
        info.status = "no_price"
    return info


def build_headers(user_agent: str, accept_language: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": accept_language,
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "no-cache",
    }


class IdentityPool:
    """Rota la identidad de las peticiones (User-Agent + proxy opcional) cada
    `batch_size` productos y descarta proxies que fallan repetidamente."""

    def __init__(self, proxies: list[str], batch_size: int = 10, max_failures: int = 2):
        self._proxies = list(proxies)
        random.shuffle(self._proxies)
        self._failures: dict[str, int] = {}
        self._batch_size = max(1, batch_size)
        self._max_failures = max_failures
        self._count = 0
        self._idx = 0
        self._current = self._new_identity()

    @property
    def proxies_alive(self) -> int:
        return len(self._proxies)

    def _new_identity(self) -> tuple[str, str | None]:
        proxy = None
        if self._proxies:
            self._idx = (self._idx + 1) % len(self._proxies)
            proxy = self._proxies[self._idx]
        return random.choice(USER_AGENTS), proxy

    def next(self) -> tuple[str, str | None]:
        self._count += 1
        if self._count % self._batch_size == 0:
            self._current = self._new_identity()
        return self._current

    def rotate(self) -> None:
        self._current = self._new_identity()

    def report_failure(self, proxy: str | None) -> bool:
        """Cuenta un fallo; devuelve True si el proxy queda descartado."""
        if not proxy:
            return False
        self._failures[proxy] = self._failures.get(proxy, 0) + 1
        if self._failures[proxy] >= self._max_failures and proxy in self._proxies:
            self._proxies.remove(proxy)
            self.rotate()
            return True
        if self._current[1] == proxy:
            self.rotate()  # no insistir con un proxy que acaba de fallar
        return False

    def remove(self, proxy: str | None) -> bool:
        """Descarta el proxy de inmediato (p. ej. Amazon lo ha bloqueado)."""
        if proxy and proxy in self._proxies:
            self._proxies.remove(proxy)
            self.rotate()
            return True
        return False

    def report_success(self, proxy: str | None) -> None:
        if proxy:
            self._failures[proxy] = 0
