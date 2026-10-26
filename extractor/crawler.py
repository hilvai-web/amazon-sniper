"""Recorrido de búsquedas de Amazon y de Best Sellers.

Búsquedas (/s?k=...): se leen sus páginas de resultados una a una (sin los
anuncios patrocinados), alternando entre las búsquedas de la categoría.

Best Sellers (solo la categoría general):

Cada página de Best Sellers da como mucho 100 productos (2 páginas de 50).
Para llegar a 1000 se recorre el árbol de subcategorías de Electrónica en
anchura: primero el top de la raíz, luego el de cada subcategoría principal
(Audio, Fotografía, Accesorios...), luego sus hijas, hasta completar el cupo.

Solo cuentan productos de valor alto: precio >= MIN_PRICE y sin palabras de
la lista negra (pilas, fundas, cables...) en el título ni en la URL. Las
subcategorías de consumibles ni siquiera se visitan.
"""
from __future__ import annotations

import asyncio
import random
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from . import config
from .browser import BrowserSession
from .storage import AsinStore, save_state

ASIN_RE = re.compile(r"/dp/([A-Z0-9]{10})")
PRICE_RE = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})\s*€")


class Blocked(Exception):
    """Captcha o página de error de Amazon."""


_log_sink = print


def set_log_sink(fn) -> None:
    """La app redirige aquí los mensajes del extractor a su registro."""
    global _log_sink
    _log_sink = fn


def log(msg: str) -> None:
    for line in str(msg).splitlines() or [""]:
        if line.strip():
            _log_sink(line)


def category_url(root: str, node: str | None = None) -> str:
    """root = ruta del departamento, p. ej. '/gp/bestsellers/electronics'."""
    return config.BASE_URL + root + (f"/{node}" if node else "")


def subcat_pattern(root: str) -> re.Pattern:
    return re.compile(re.escape(root) + r"/(\d+)")


def normalize(text: str) -> str:
    """Minúsculas y sin tildes, para comparar con EXCLUDE_KEYWORDS."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def keyword_re(words: list[str]) -> re.Pattern | None:
    words = [normalize(w) for w in words if w.strip()]
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b") if words else None


_KEYWORDS_RE = keyword_re(config.EXCLUDE_KEYWORDS)


def name_head(name: str) -> str:
    """Nombre del producto: el título hasta la primera coma o paréntesis."""
    return " ".join(re.split(r"[,(|]", normalize(name))[0].split()[:config.TITLE_WORDS])


def is_excluded(name: str) -> bool:
    """True si el nombre (título hasta la primera coma o paréntesis) contiene
    alguna palabra de la lista negra."""
    return bool(_KEYWORDS_RE and _KEYWORDS_RE.search(name_head(name)))


class CategoryFilter:
    """Palabras que debe tener (en el título) y que no puede tener (en el
    nombre) un producto para contar como de la categoría."""

    def __init__(self, key: str | None = None):
        self.include = keyword_re(config.PALABRAS_CATEGORIA.get(key, []))
        self.exclude = keyword_re(config.EXCLUIR_CATEGORIA.get(key, []))

    def accepts(self, title: str) -> bool:
        if self.exclude and self.exclude.search(name_head(title)):
            return False
        return not self.include or bool(self.include.search(normalize(title)))


def url_slug(href: str) -> str:
    """'/Duracell-Pilas-AA-alcalinas/dp/B0...' -> 'Duracell Pilas AA alcalinas'."""
    path = href.split("/dp/")[0].rsplit("/", 1)[-1]
    return path.replace("-", " ")


def parse_price(text: str) -> float | None:
    """Primer precio en euros del texto de la ficha ('1.299,00 €' -> 1299.0)."""
    m = PRICE_RE.search(text.replace("\xa0", " "))
    return float(m.group(1).replace(".", "").replace(",", ".")) if m else None


def extract_asins(items: list[dict], category: CategoryFilter | None = None) -> tuple[list[str], int]:
    """ASINs de valor alto en orden de ranking, sin repetir.
    `items` son fichas {href, text[, title]}. Devuelve (asins, descartados)."""
    seen: dict[str, None] = {}
    dropped = 0
    for item in items:
        m = ASIN_RE.search(item["href"])
        if not m or m.group(1) in seen:
            continue
        text = item.get("text", "")
        price = parse_price(text)
        title = item.get("title") or next((ln for ln in text.splitlines() if len(ln.strip()) > 15), text)
        if (price is None or price < config.MIN_PRICE
                or is_excluded(title) or is_excluded(url_slug(item["href"]))
                or (category and not category.accepts(title))):
            dropped += 1
            continue
        seen[m.group(1)] = None
    return list(seen), dropped


def extract_subcategories(links: list[dict], current: str, root: str) -> list[str]:
    nodes: dict[str, None] = {}
    pattern = subcat_pattern(root)
    for link in links:
        m = pattern.search(link["href"])
        if m and not is_excluded(link.get("text", "")):
            url = category_url(root, m.group(1))
            if url != current:
                nodes.setdefault(url, None)
    return list(nodes)


async def human_delay() -> None:
    await asyncio.sleep(random.uniform(*config.DELAY_RANGE))


async def accept_cookies(session: BrowserSession) -> None:
    try:
        button = session.page.locator("#sp-cc-accept")
        if await button.is_visible(timeout=1500):
            await button.click()
    except PlaywrightError:
        pass


async def scroll_to_bottom(session: BrowserSession) -> None:
    """Best Sellers carga los productos 31-50 al hacer scroll."""
    page = session.page
    for _ in range(12):
        await page.mouse.wheel(0, random.randint(700, 1100))
        await asyncio.sleep(random.uniform(0.25, 0.6))
        at_end = await page.evaluate(
            "window.innerHeight + window.scrollY >= document.body.scrollHeight - 50")
        if at_end:
            break


# Rejilla del ranking; evita contar carruseles de "vistos recientemente" o patrocinados
# Cada ficha devuelve su enlace /dp/ y su texto visible (nombre + precio)
GRID_ITEMS_JS = """() => {
  let cards = document.querySelectorAll('#gridItemRoot, .p13n-grid-content > div, .zg-grid-general-faceout');
  if (!cards.length) cards = Array.from(document.querySelectorAll('a[href*="/dp/"]'), a => a.parentElement);
  return Array.from(cards, c => {
    const a = c.querySelector('a[href*="/dp/"]');
    return a ? {href: a.href, text: c.innerText || ''} : null;
  }).filter(Boolean);
}"""
# Resultados de búsqueda, sin anuncios patrocinados. El precio se toma del
# texto accesible (.a-offscreen), que viene entero ("1.299,00 €")
SEARCH_ITEMS_JS = """() => Array.from(
  document.querySelectorAll('div[data-component-type="s-search-result"][data-asin]'), c => {
    const asin = c.getAttribute('data-asin');
    if (!asin || /^Patrocinado|^Sponsored/i.test((c.innerText || '').trim())) return null;
    if (c.querySelector('.puis-sponsored-label-text, .s-sponsored-label-text')) return null;
    const title = Array.from(c.querySelectorAll('h2'), h => h.innerText.trim()).join(' ');
    const price = c.querySelector('.a-price:not(.a-text-price) .a-offscreen');
    return {href: location.origin + '/dp/' + asin, title,
            text: (price ? price.textContent + '\\n' : '') + (c.innerText || '')};
  }).filter(Boolean)"""
HAS_NEXT_JS = "() => !!document.querySelector('a.s-pagination-next')"
LINKS_JS = "els => els.map(e => ({href: e.href, text: e.innerText || ''}))"


def is_search(url: str) -> bool:
    return urlsplit(url).path.rstrip("/") == "/s"


def search_page_url(url: str, page: int) -> str:
    """La misma búsqueda en la página `page` (sin el resto de parámetros de seguimiento)."""
    parts = urlsplit(url)
    keep = [(k, v) for k, v in parse_qsl(parts.query) if k in ("k", "i", "rh", "s", "bbn", "node")]
    if page > 1:
        keep.append(("page", str(page)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(keep), ""))


def search_page_number(url: str) -> int:
    return int(dict(parse_qsl(urlsplit(url).query)).get("page", 1))


async def load_page(session: BrowserSession, url: str) -> tuple[list[dict], list[dict], bool]:
    """Abre la URL y devuelve (fichas de productos, todos los enlaces, hay página siguiente)."""
    response = await session.page.goto(url, wait_until="domcontentloaded")
    if response and response.status in (403, 429, 503):
        raise Blocked(f"HTTP {response.status}")
    html = (await session.page.content())[:20000].lower()
    if "validatecaptcha" in html or "captchacharacters" in html:
        raise Blocked("captcha")
    await accept_cookies(session)
    await scroll_to_bottom(session)
    if is_search(url):
        products = await session.page.evaluate(SEARCH_ITEMS_JS)
        return products, [], await session.page.evaluate(HAS_NEXT_JS)
    products = await session.page.evaluate(GRID_ITEMS_JS)
    links = await session.page.eval_on_selector_all("a[href]", LINKS_JS)
    return products, links, False


async def fetch_with_retries(session: BrowserSession, url: str) -> tuple[list[dict], list[dict], bool] | None:
    for attempt in range(config.MAX_RETRIES + 1):
        try:
            return await load_page(session, url)
        except (Blocked, PlaywrightTimeout, PlaywrightError) as exc:
            wait = 20 * (attempt + 1) + random.uniform(0, 10)
            log(f"  ! {type(exc).__name__}: {str(exc)[:80]} -> nueva identidad, espera {wait:.0f}s")
            await session.new_identity()
            await asyncio.sleep(wait)
    return None


def department_root(url: str) -> str:
    """'https://www.amazon.es/gp/bestsellers/electronics/664660031' -> '/gp/bestsellers/electronics'.
    Las subcategorías que se siguen son las de ese mismo departamento."""
    m = re.search(r"(/gp/bestsellers/[^/?#]+)", url)
    if not m:
        raise ValueError(f"URL de Best Sellers no válida: {url}")
    return m.group(1)


async def crawl(session: BrowserSession, store: AsinStore, queue: list[str],
                visited: set[str], start_urls: list[str], state_path,
                category: CategoryFilter | None = None) -> None:
    """Recorre las URLs de la categoría: páginas de búsqueda o Best Sellers."""
    if not queue and not visited:
        queue.extend(search_page_url(u, 1) if is_search(u) else u.split("?")[0].rstrip("/")
                     for u in start_urls)
    fails = 0

    async def fetch(url: str):
        nonlocal fails
        result = await fetch_with_retries(session, url)
        if result is None:
            fails += 1
            log(f"  x Página perdida ({fails}/{config.MAX_CONSECUTIVE_FAILS} fallos seguidos)")
            if fails >= config.MAX_CONSECUTIVE_FAILS:
                raise Blocked("demasiados fallos seguidos; IP probablemente limitada")
        else:
            fails = 0
        return result

    def add(products: list[dict], label: str) -> None:
        asins, dropped = extract_asins(products, category)
        added = store.add_many(asins)
        log(f"  {label}: +{added} nuevos, {dropped} descartados (baratos/consumibles/otra categoría)"
            f" | total {len(store)}/{store.limit} | pendientes {len(queue)}")
        store.save()

    while queue and not store.full:
        cat = queue.pop(0)
        if cat in visited:
            continue
        if is_search(cat):
            page = search_page_number(cat)
            if page == 1:
                log(f"\n> Búsqueda {dict(parse_qsl(urlsplit(cat).query)).get('k', cat)}")
            try:
                result = await fetch(cat)
            except Blocked:
                queue.insert(0, cat)  # se reintentará al reanudar
                raise
            if result:
                products, _, has_next = result
                add(products, f"pág {page}")
                # la página siguiente va al final: se alternan las búsquedas
                if products and has_next and page < config.MAX_SEARCH_PAGES:
                    queue.append(search_page_url(cat, page + 1))
            visited.add(cat)
            save_state(state_path, queue, visited)
            if not store.full:
                await human_delay()
            continue

        root = department_root(cat)
        log(f"\n> Categoría {cat}")
        for pg in range(1, config.PAGES_PER_CATEGORY + 1):
            url = cat if pg == 1 else f"{cat}?pg={pg}"
            try:
                result = await fetch(url)
            except Blocked:
                queue.insert(0, cat)
                raise
            if result is None:
                break
            products, links, _ = result
            if pg == 1:
                new_cats = [c for c in extract_subcategories(links, cat, root)
                            if c not in visited and c not in queue]
                queue.extend(new_cats)
            add(products, f"pág {pg}")
            if store.full:
                break
            await human_delay()
        visited.add(cat)
        save_state(state_path, queue, visited)
