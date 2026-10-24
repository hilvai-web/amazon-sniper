"""Recorrido de Best Sellers: categoría raíz y luego subcategorías (BFS).

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

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from . import config
from .browser import BrowserSession
from .storage import AsinStore, save_state

ASIN_RE = re.compile(r"/dp/([A-Z0-9]{10})")
PRICE_RE = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})\s*€")


class Blocked(Exception):
    """Captcha o página de error de Amazon."""


def log(msg: str) -> None:
    print(msg, flush=True)


def category_url(root: str, node: str | None = None) -> str:
    """root = ruta del departamento, p. ej. '/gp/bestsellers/electronics'."""
    return config.BASE_URL + root + (f"/{node}" if node else "")


def subcat_pattern(root: str) -> re.Pattern:
    return re.compile(re.escape(root) + r"/(\d+)")


def normalize(text: str) -> str:
    """Minúsculas y sin tildes, para comparar con EXCLUDE_KEYWORDS."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


_KEYWORDS_RE = re.compile(
    r"\b(" + "|".join(re.escape(normalize(k)) for k in config.EXCLUDE_KEYWORDS) + r")\b")


def is_excluded(name: str) -> bool:
    """True si el nombre (título hasta la primera coma o paréntesis) contiene
    alguna palabra de la lista negra."""
    head = " ".join(re.split(r"[,(|]", normalize(name))[0].split()[:config.TITLE_WORDS])
    return bool(config.EXCLUDE_KEYWORDS) and bool(_KEYWORDS_RE.search(head))


def url_slug(href: str) -> str:
    """'/Duracell-Pilas-AA-alcalinas/dp/B0...' -> 'Duracell Pilas AA alcalinas'."""
    path = href.split("/dp/")[0].rsplit("/", 1)[-1]
    return path.replace("-", " ")


def parse_price(text: str) -> float | None:
    """Primer precio en euros del texto de la ficha ('1.299,00 €' -> 1299.0)."""
    m = PRICE_RE.search(text.replace("\xa0", " "))
    return float(m.group(1).replace(".", "").replace(",", ".")) if m else None


def extract_asins(items: list[dict]) -> tuple[list[str], int]:
    """ASINs de valor alto en orden de ranking, sin repetir.
    `items` son fichas {href, text}. Devuelve (asins, descartados)."""
    seen: dict[str, None] = {}
    dropped = 0
    for item in items:
        m = ASIN_RE.search(item["href"])
        if not m or m.group(1) in seen:
            continue
        text = item.get("text", "")
        price = parse_price(text)
        title = next((ln for ln in text.splitlines() if len(ln.strip()) > 15), text)
        if (price is None or price < config.MIN_PRICE
                or is_excluded(title) or is_excluded(url_slug(item["href"]))):
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
LINKS_JS = "els => els.map(e => ({href: e.href, text: e.innerText || ''}))"


async def load_page(session: BrowserSession, url: str) -> tuple[list[dict], list[dict]]:
    """Abre la URL y devuelve (fichas del ranking, todos los enlaces), como {href, text}."""
    response = await session.page.goto(url, wait_until="domcontentloaded")
    if response and response.status in (403, 429, 503):
        raise Blocked(f"HTTP {response.status}")
    html = (await session.page.content())[:20000].lower()
    if "validatecaptcha" in html or "captchacharacters" in html:
        raise Blocked("captcha")
    await accept_cookies(session)
    await scroll_to_bottom(session)
    products = await session.page.evaluate(GRID_ITEMS_JS)
    links = await session.page.eval_on_selector_all("a[href]", LINKS_JS)
    return products, links


async def fetch_with_retries(session: BrowserSession, url: str) -> tuple[list[dict], list[dict]] | None:
    for attempt in range(config.MAX_RETRIES + 1):
        try:
            return await load_page(session, url)
        except (Blocked, PlaywrightTimeout, PlaywrightError) as exc:
            wait = 20 * (attempt + 1) + random.uniform(0, 10)
            log(f"  ! {type(exc).__name__}: {str(exc)[:80]} -> nueva identidad, espera {wait:.0f}s")
            await session.new_identity()
            await asyncio.sleep(wait)
    return None


async def find_start(session: BrowserSession, category: dict) -> str:
    """URL de arranque: el departamento, o la subcategoría cuyo nombre contenga
    category['sub'] (se busca en el menú lateral del departamento)."""
    root_url = category_url(category["ruta"])
    wanted = category.get("sub")
    if not wanted:
        return root_url
    result = await fetch_with_retries(session, root_url)
    if result:
        pattern = subcat_pattern(category["ruta"])
        for link in result[1]:
            m = pattern.search(link["href"])
            if m and normalize(wanted) in normalize(link.get("text", "")):
                log(f"Subcategoría encontrada: {link['text'].strip()}")
                return category_url(category["ruta"], m.group(1))
    log(f"Aviso: no encuentro la subcategoría '{wanted}'; uso el departamento completo.")
    return root_url


async def crawl(session: BrowserSession, store: AsinStore, queue: list[str],
                visited: set[str], category: dict, state_path) -> None:
    root = category["ruta"]
    if not queue and not visited:
        queue.append(await find_start(session, category))
    fails = 0
    while queue and not store.full:
        cat = queue.pop(0)
        if cat in visited:
            continue
        log(f"\n> Categoría {cat}")
        for pg in range(1, config.PAGES_PER_CATEGORY + 1):
            url = cat if pg == 1 else f"{cat}?pg={pg}"
            result = await fetch_with_retries(session, url)
            if result is None:
                fails += 1
                log(f"  x Página perdida ({fails}/{config.MAX_CONSECUTIVE_FAILS} fallos seguidos)")
                if fails >= config.MAX_CONSECUTIVE_FAILS:
                    queue.insert(0, cat)  # se reintentará al reanudar
                    raise Blocked("demasiados fallos seguidos; IP probablemente limitada")
                break
            fails = 0
            products, links = result
            if pg == 1:
                new_cats = [c for c in extract_subcategories(links, cat, root)
                            if c not in visited and c not in queue]
                queue.extend(new_cats)
            asins, dropped = extract_asins(products)
            added = store.add_many(asins)
            log(f"  pág {pg}: +{added} nuevos, {dropped} descartados (baratos/consumibles) | total {len(store)}/{store.limit} | cola {len(queue)} categorías")
            store.save()
            if store.full:
                break
            await human_delay()
        visited.add(cat)
        save_state(state_path, queue, visited)
