"""Motor de escaneo asíncrono.

Corre en un hilo propio con su event loop de asyncio, para no congelar la GUI.
Se comunica con la interfaz solo a través de una `queue.Queue` de eventos:

    ("log", str)                        mensaje para el registro
    ("row", dict)                       resultado de un producto
    ("progress", hechos, total, alertas)
    ("finished", str)                   el escaneo ha terminado
"""
from __future__ import annotations

import asyncio
import queue
import random
import re
import ssl
import threading
import time
import unicodedata
from urllib.parse import urlencode
from typing import Awaitable, Callable

import aiohttp
import certifi

from . import telegram
from .amazon_api import MAX_ITEMS as API_MAX_ITEMS
from .amazon_api import ApiError, CreatorsApi, consultar_via_api_afiliados
from .config import MARKETPLACES, Settings, app_dir
from .db import Database
from .scraper import (Blocked, IdentityPool, ProductInfo, build_headers, is_captcha,
                      offers_url, parse_captcha_form, parse_offers, parse_product, USER_AGENTS)

Fetcher = Callable[[aiohttp.ClientSession, str, dict, str | None, float], Awaitable[tuple[int, str]]]

MAX_RETRIES = 2
CAPTCHA_WAIT_SECONDS = 15 * 60      # tiempo máximo esperando tu respuesta en Telegram
CAPTCHA_MAX_TRIES = 5
API_MARKETS = {"ES"}        # el ID de afiliado es de amazon.es
PROXY_TEST_URL = "https://www.amazon.es/"


async def http_fetch(session: aiohttp.ClientSession, url: str, headers: dict,
                     proxy: str | None, timeout: float) -> tuple[int, str]:
    async with session.get(url, headers=headers, proxy=proxy,
                           timeout=aiohttp.ClientTimeout(total=timeout),
                           allow_redirects=True) as resp:
        return resp.status, await resp.text(errors="ignore")


def evaluate(info: ProductInfo, reference: float | None) -> tuple[float, float | None, str]:
    """Devuelve (descuento %, precio de referencia, fuente) usando el mejor dato
    disponible: precio tachado de Amazon o mediana del histórico local."""
    best = (0.0, None, "-")
    if not info.price:
        return best
    if info.list_price:
        d = (info.list_price - info.price) / info.list_price * 100
        best = (d, info.list_price, "PVP tachado")
    if reference and reference > info.price:
        d = (reference - info.price) / reference * 100
        if d > best[0]:
            best = (d, reference, "histórico")
    return best


TITLE_WORDS = 10


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def keyword_matcher(keywords: str) -> Callable[[str], bool]:
    """Devuelve una función que dice si un título contiene alguna palabra excluida."""
    words = [_normalize(k.strip()) for k in keywords.split(",") if k.strip()]
    if not words:
        return lambda _title: False
    pattern = re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b")
    # solo el nombre (hasta la primera coma o paréntesis): ahí Amazon dice qué es el
    # producto; después vienen extras ("40 h de batería", "cable incluido")
    def matches(title: str | None) -> bool:
        name = re.split(r"[,(|]", _normalize(title or ""))[0]
        return bool(pattern.search(" ".join(name.split()[:TITLE_WORDS])))
    return matches


def low_value_reason(info: ProductInfo, reference: float | None, min_value: float,
                     is_excluded: Callable[[str], bool]) -> str | None:
    """Motivo para descartar un producto, o None si merece la pena vigilarlo.
    El "precio normal" es el mayor entre precio actual, PVP tachado e histórico,
    así un error de precio en un producto caro no se descarta por salir barato."""
    if info.title and is_excluded(info.title):
        return "excluido: consumible"
    known = [p for p in (info.price, info.list_price, reference) if p]
    if min_value > 0 and known and max(known) < min_value:
        return "excluido: barato"
    return None


DEBUG_DIR = app_dir() / "depuracion"
DEBUG_MAX_FILES = 10


def save_debug_page(html: str, asin: str, market: str) -> None:
    """Guarda unas pocas fichas "sin precio" para poder ver qué devolvió Amazon."""
    try:
        DEBUG_DIR.mkdir(exist_ok=True)
        if len(list(DEBUG_DIR.glob("*.html"))) < DEBUG_MAX_FILES:
            (DEBUG_DIR / f"sin_precio_{asin}_{market}.html").write_text(html, encoding="utf-8")
    except OSError:
        pass


class ScanEngine:
    def __init__(self, settings: Settings, asins: list[str], db: Database,
                 events: "queue.Queue[tuple]", proxies: list[str] | None = None,
                 fetcher: Fetcher = http_fetch):
        self.s = settings
        self.asins = asins
        self.db = db
        self.events = events
        self.proxies = list(proxies or [])
        self.identities = IdentityPool(self.proxies, settings.batch_size)
        self.fetcher = fetcher
        self.is_excluded = keyword_matcher(settings.exclude_keywords)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._resume_at = 0.0       # pausa global tras bloqueo
        self._block_streak = 0
        self.done = 0
        self.total = 0
        self.alerts = 0
        self.api: CreatorsApi | None = None
        if settings.api_client_id and settings.api_client_secret and settings.api_partner_tag:
            self.api = CreatorsApi(settings.api_client_id, settings.api_client_secret,
                                   settings.api_partner_tag, settings.timeout)
        self._web_only: set[tuple[str, str]] = set()   # lo que la API no pudo dar
        self.waiting_captcha = False    # parado hasta que el usuario resuelva el captcha
        self.api_done = 0

    # ------------------------------------------------------------ control
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="scan-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def resume(self, rotate: bool = True) -> None:
        """El usuario ha resuelto el captcha: se sigue donde se quedó."""
        if self.waiting_captcha:
            self.waiting_captcha = False
            self._block_streak = 0
            self._resume_at = 0.0
            if rotate:
                self.identities.rotate()
            self._log("Continuando el escaneo.")

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _emit(self, *event) -> None:
        self.events.put(event)

    def _log(self, msg: str) -> None:
        self._emit("log", time.strftime("%H:%M:%S ") + msg)

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
            reason = "Detenido por el usuario" if self._stop.is_set() else "Escaneo completado"
        except Exception as exc:  # noqa: BLE001
            reason = f"Error fatal: {exc}"
        self._emit("finished", reason)

    # ------------------------------------------------------------ núcleo
    async def _main(self) -> None:
        jobs: asyncio.Queue[tuple[str, str, int]] = asyncio.Queue()
        skipped = 0
        for asin in self.asins:
            for market in self.s.markets:
                if (self.db.recently_scanned(asin, market, self.s.rescan_hours)
                        or self.db.recently_excluded(asin, market)):
                    skipped += 1
                    continue
                jobs.put_nowait((asin, market, 0))
        self.total = jobs.qsize()
        self._log(f"{self.total} consultas en cola ({skipped} omitidas por escaneo reciente o por ser baratas/consumibles). "
                  f"Proxies: {self.identities.proxies_alive or 'ninguno (IP directa)'}")
        self._emit("progress", 0, self.total, 0)
        if not self.total:
            return

        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_ctx, limit=max(self.s.concurrency * 2, 30))
        async with aiohttp.ClientSession(connector=connector,
                                         cookie_jar=aiohttp.CookieJar()) as session:
            if self.proxies:
                await self._check_proxies(session)
            workers = [asyncio.create_task(self._worker(i, session, jobs))
                       for i in range(max(1, self.s.concurrency))]
            if self.api and API_MARKETS & set(self.s.markets):
                self._log("API de Amazon activada: se reparte la cola con la web.")
                workers.append(asyncio.create_task(self._api_lane(session, jobs)))
            all_done = asyncio.create_task(jobs.join())
            while not self._stop.is_set() and not all_done.done():
                await asyncio.sleep(0.3)
            all_done.cancel()
            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
        if self.api_done:
            self._log(f"API de Amazon: {self.api_done} productos consultados por la API.")

    async def _check_proxies(self, session: aiohttp.ClientSession) -> None:
        """Prueba cada proxy contra Amazon y se queda solo con los que responden
        bien y rápido. Los demás (caídos, lentos, captcha o error) se descartan."""
        self._log(f"Comprobando {len(self.proxies)} proxies contra Amazon "
                  f"(máx. {self.s.proxy_max_seconds:g} s cada uno)…")
        limit = asyncio.Semaphore(25)

        async def probe(proxy: str) -> tuple[str, float] | None:
            async with limit:
                if self._stop.is_set():
                    return None
                start = time.monotonic()
                try:
                    status, html = await self.fetcher(
                        session, PROXY_TEST_URL, build_headers(random.choice(USER_AGENTS), "es-ES,es;q=0.9"),
                        proxy, self.s.proxy_max_seconds)
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
                    return None
                elapsed = time.monotonic() - start
                if status != 200 or is_captcha(html) or elapsed > self.s.proxy_max_seconds:
                    return None
                return proxy, elapsed

        results = [r for r in await asyncio.gather(*(probe(p) for p in self.proxies)) if r]
        results.sort(key=lambda r: r[1])
        good = [p for p, _ in results]
        self.identities = IdentityPool(good, self.s.batch_size)
        if good:
            self._log(f"Proxies útiles: {len(good)} de {len(self.proxies)} "
                      f"(el más rápido responde en {results[0][1]:.1f} s).")
        else:
            self._log(f"Ningún proxy de los {len(self.proxies)} funciona con Amazon: se usa la conexión directa.")

    def _drop_proxy(self, proxy: str | None, reason: str, now: bool = False) -> None:
        """Cuenta un fallo del proxy (2 fallos = fuera) o lo quita ya si now=True."""
        dropped = self.identities.remove(proxy) if now else self.identities.report_failure(proxy)
        if dropped:
            left = self.identities.proxies_alive
            self._log(f"Proxy descartado ({reason}): {proxy}. Quedan {left}"
                      + ("" if left else "; sigo con la conexión directa") + ".")

    async def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while not self._stop.is_set() and time.monotonic() < end:
            await asyncio.sleep(min(0.25, end - time.monotonic()))

    async def _worker(self, wid: int, session: aiohttp.ClientSession,
                      jobs: "asyncio.Queue[tuple[str, str, int]]") -> None:
        # arranque escalonado para no disparar todas las peticiones a la vez
        await self._sleep(wid * random.uniform(0.5, 1.5))
        while not self._stop.is_set():
            # bloquea hasta que haya trabajo (los reintentos vuelven a la cola);
            # _main cancela a los workers cuando la cola queda vacía y procesada
            asin, market, tries = await jobs.get()
            try:
                while self.waiting_captcha and not self._stop.is_set():
                    await asyncio.sleep(0.5)
                wait = self._resume_at - time.time()
                if wait > 0:
                    await self._sleep(wait)
                if self._stop.is_set():
                    return
                try:
                    await self._scan_one(session, asin, market)
                    self._block_streak = 0
                except Blocked as exc:
                    if exc.proxy:
                        # con proxies, un bloqueo solo afecta a ese proxy: se cambia y se reintenta
                        self._drop_proxy(exc.proxy, f"Amazon lo bloquea: {exc}", now=True)
                    elif self.s.captcha_pause:
                        # se para todo hasta que lo resuelvas; el producto se reintenta sin gastar intento
                        self._ask_captcha(asin, market, str(exc), session, exc)
                        jobs.put_nowait((asin, market, tries))
                        continue
                    else:
                        self._on_block(str(exc))
                    if tries < MAX_RETRIES or (exc.proxy and tries < MAX_RETRIES + 3):
                        jobs.put_nowait((asin, market, tries + 1))
                        continue
                    self._finish(asin, market, None, "bloqueado")
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                    if tries < MAX_RETRIES:
                        jobs.put_nowait((asin, market, tries + 1))
                        continue
                    self._finish(asin, market, None, f"error red: {type(exc).__name__}")
                await self._sleep(random.uniform(self.s.delay_min, max(self.s.delay_min, self.s.delay_max)))
            finally:
                jobs.task_done()

    def _ask_captcha(self, asin: str, market: str, reason: str,
                     session: aiohttp.ClientSession | None = None, exc: Blocked | None = None) -> None:
        if self.waiting_captcha:
            return
        self.waiting_captcha = True
        url = f"https://{MARKETPLACES[market][0]}/dp/{asin}"
        form = parse_captcha_form(exc.html, exc.url) if exc and exc.html else None
        by_telegram = bool(form and session and self.s.telegram_token and self.s.telegram_chat_id)
        self._log(f"[!] Amazon ha solicitado verificación ({reason}). "
                  + ("Te la he mandado a Telegram: contesta allí, o " if by_telegram else "")
                  + "resuélvela en el navegador y pulsa «Continuar».")
        self._emit("captcha", url)
        if by_telegram:
            asyncio.get_running_loop().create_task(self._telegram_captcha(session, exc, form, market))

    async def _telegram_captcha(self, session: aiohttp.ClientSession, exc: Blocked,
                                form: dict, market: str) -> None:
        """Manda el captcha a Telegram, espera tu respuesta y se la envía a Amazon
        con la misma sesión (cookies y User-Agent) que estaba escaneando."""
        token, chat = self.s.telegram_token, self.s.telegram_chat_id
        headers = build_headers(exc.ua or random.choice(USER_AGENTS), MARKETPLACES[market][1])
        try:
            offset = await telegram.next_offset(session, token)
            for attempt in range(1, CAPTCHA_MAX_TRIES + 1):
                if not self.waiting_captcha or self._stop.is_set():
                    return
                note = "" if attempt == 1 else "❌ No ha valido, aquí va otro.\n"
                image = await self._fetch_bytes(session, form["image"], headers) if form["image"] else None
                if image and form["text_field"]:
                    sent = await telegram.send_photo(session, token, chat, image,
                                                     f"{note}🔐 Amazon pide verificación ({market}). "
                                                     "Responde con las letras de la imagen.")
                else:
                    sent, _ = await telegram.send_message(session, token, chat,
                                                          f"{note}🔐 Amazon pide confirmar que eres una persona "
                                                          f"({market}). Responde <b>ok</b> para continuar.")
                if not sent:
                    self._log("No pude mandar el captcha a Telegram; resuélvelo en el navegador.")
                    return
                answer, offset = await self._wait_reply(session, token, chat, offset)
                if answer is None:
                    return          # sin respuesta a tiempo, o ya resuelto desde la app
                params = dict(form["fields"])
                if form["text_field"]:
                    params[form["text_field"]] = answer.strip().replace(" ", "").upper()
                status, html = await self.fetcher(session, form["action"] + "?" + urlencode(params),
                                                  headers, None, self.s.timeout)
                if status == 200 and not is_captcha(html):
                    await telegram.send_message(session, token, chat, "✅ Captcha resuelto. Sigo escaneando.")
                    self._log("Captcha resuelto desde Telegram.")
                    self.resume(rotate=False)      # mismas cookies y User-Agent: los ya validados
                    self._emit("captcha_solved")
                    return
                form = parse_captcha_form(html, form["action"]) or form
            await telegram.send_message(session, token, chat,
                                        "Demasiados intentos. Resuélvelo en el navegador y pulsa «Continuar» en la app.")
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            self._log(f"Captcha por Telegram falló ({type(err).__name__}); resuélvelo en el navegador.")

    async def _fetch_bytes(self, session: aiohttp.ClientSession, url: str, headers: dict) -> bytes | None:
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                return await resp.read() if resp.status == 200 else None
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            return None

    async def _wait_reply(self, session: aiohttp.ClientSession, token: str, chat: str,
                          offset: int) -> tuple[str | None, int]:
        """Primer mensaje de texto de tu chat. (None, offset) si pasa el tiempo
        o si ya lo has resuelto con el botón de la app."""
        deadline = time.monotonic() + CAPTCHA_WAIT_SECONDS
        while self.waiting_captcha and not self._stop.is_set() and time.monotonic() < deadline:
            for update in await telegram.get_updates(session, token, offset):
                offset = update["update_id"] + 1
                msg = update.get("message") or {}
                if str((msg.get("chat") or {}).get("id")) == str(chat) and msg.get("text"):
                    return msg["text"], offset
        return None, offset

    def _on_block(self, reason: str) -> None:
        """Backoff exponencial global: 30 s, 60 s, 120 s ... máx. 10 min."""
        self._block_streak += 1
        pause = min(600, 30 * 2 ** (self._block_streak - 1))
        self._resume_at = max(self._resume_at, time.time() + pause)
        self.identities.rotate()
        self._log(f"⚠ Amazon limita las peticiones ({reason}). Pausa de {pause}s y cambio de identidad.")

    async def _scan_one(self, session: aiohttp.ClientSession, asin: str, market: str) -> None:
        domain, lang = MARKETPLACES[market]
        url = f"https://{domain}/dp/{asin}"
        ua, proxy = self.identities.next()
        # con proxy, el tiempo máximo es el de proxy lento; sin proxy, el normal
        timeout = min(self.s.timeout, self.s.proxy_max_seconds * 2) if proxy else self.s.timeout
        start = time.monotonic()
        try:
            status, html = await self.fetcher(session, url, build_headers(ua, lang), proxy, timeout)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            self._drop_proxy(proxy, "sin respuesta")
            raise
        if proxy and time.monotonic() - start > self.s.proxy_max_seconds:
            self._drop_proxy(proxy, "muy lento")
        if status in (503, 429, 403):
            raise Blocked(f"HTTP {status}", proxy, html=html, url=url, ua=ua)
        if status == 404:
            self.identities.report_success(proxy)
            self.db.record_scan(asin, market, None, None, None)
            self._finish(asin, market, None, "no existe")
            return
        if status >= 400:
            self._drop_proxy(proxy, f"HTTP {status}")
            raise aiohttp.ClientResponseError(None, (), status=status)  # type: ignore[arg-type]

        info = parse_product(html, asin, market, url)
        if info.status == "no_price":
            save_debug_page(html, asin, market)
        if info.status == "captcha":
            raise Blocked("captcha", proxy, html=html, url=url, ua=ua)
        self.identities.report_success(proxy)
        if info.other_offers and (info.price is None or self.s.only_prime):
            await self._fill_from_offers(session, info, ua, lang, proxy, timeout)
        await self._process(session, info)

    async def _fill_from_offers(self, session: aiohttp.ClientSession, info: ProductInfo, ua: str,
                                lang: str, proxy: str | None, timeout: float) -> None:
        """Sin oferta destacada: abre "Ver todas las opciones de compra" y toma la
        oferta nueva más barata (la más barata con Prime si se pide Solo Prime)."""
        domain = MARKETPLACES[info.market][0]
        await self._sleep(random.uniform(0.5, 1.5))
        try:
            status, html = await self.fetcher(session, offers_url(domain, info.asin),
                                              build_headers(ua, lang), proxy, timeout)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            return
        if status != 200 or is_captcha(html):
            return
        offers = parse_offers(html)
        if not offers:
            if info.price is None:
                info.other_offers = False     # nadie lo vende ahora mismo
            return
        prime = [o for o in offers if o.prime]
        best = prime[0] if prime and (self.s.only_prime or prime[0].price <= offers[0].price) else offers[0]
        info.price, info.prime, info.seller = best.price, best.prime, best.seller
        info.status = "ok"
        if info.list_price and info.list_price <= info.price:
            info.list_price = None

    async def _process(self, session: aiohttp.ClientSession, info: ProductInfo) -> bool:
        """Guarda el precio, filtra y avisa. Devuelve False si un dato de la API
        no basta (posible error de precio sin saber si es Prime): lo mira la web."""
        asin, market = info.asin, info.market
        reference = self.db.reference_price(asin, market) if self.s.use_history else None
        if info.source == "api" and self.s.only_prime and not info.prime and info.available:
            discount = evaluate(info, reference)[0]
            if info.price and discount >= self.s.min_discount:
                return False
        self.db.record_scan(asin, market, info.title, info.price, info.list_price)
        reason = low_value_reason(info, reference, self.s.min_value, self.is_excluded)
        if reason:
            self.db.mark_excluded(asin, market, reason)
            self._finish(asin, market, info, reason)
            return True
        await self._maybe_alert(session, info, reference)
        return True

    async def _api_lane(self, session: aiohttp.ClientSession,
                        jobs: "asyncio.Queue[tuple[str, str, int]]") -> None:
        """Consulta la API de 10 en 10 mientras los workers web siguen con la cola.
        Lo que la API no da (o si falla) vuelve a la cola para la web."""
        backoff = 0.0
        while not self._stop.is_set() and self.api:
            batch = [await jobs.get()]
            while len(batch) < API_MAX_ITEMS:
                try:
                    batch.append(jobs.get_nowait())
                except asyncio.QueueEmpty:
                    break
            mine = [j for j in batch if j[1] in API_MARKETS and (j[0], j[1]) not in self._web_only]
            for job in batch:
                if job not in mine:
                    jobs.put_nowait(job)
            try:
                if not mine:
                    await self._sleep(1)
                    continue
                market = mine[0][1]
                for job in [j for j in mine if j[1] != market]:
                    jobs.put_nowait(job)
                mine = [j for j in mine if j[1] == market]
                try:
                    results = await consultar_via_api_afiliados(self.api, session, [a for a, _, _ in mine], market)
                    backoff = 0.0
                except (ApiError, aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                    for job in mine:
                        self._web_only.add((job[0], job[1]))
                        jobs.put_nowait(job)
                    if isinstance(exc, ApiError) and exc.auth:
                        self._log(f"API de Amazon desactivada en esta vuelta: {exc}. Sigue solo la web.")
                        self.api = None
                        return
                    if isinstance(exc, ApiError) and exc.throttled:
                        backoff = min(60.0, max(5.0, backoff * 2))
                    else:
                        backoff = min(60.0, max(5.0, backoff))
                        self._log(f"API de Amazon: {str(exc)[:120] or type(exc).__name__}. Esos productos los mira la web.")
                    await self._sleep(backoff)
                    continue
                for asin, mkt, tries in mine:
                    info = results.get(asin)
                    handled = False
                    if info is not None:
                        try:
                            handled = await self._process(session, info)
                            if not handled:
                                # posible error sin Prime confirmado: se comprueba en la web
                                await self._scan_one(session, asin, mkt)
                                handled = True
                        except (Blocked, aiohttp.ClientError, asyncio.TimeoutError, OSError):
                            handled = False
                    if handled:
                        self.api_done += 1
                    else:
                        self._web_only.add((asin, mkt))
                        jobs.put_nowait((asin, mkt, tries))
                await self._sleep(self.s.api_interval)
            finally:
                for _ in batch:
                    jobs.task_done()

    async def _maybe_alert(self, session: aiohttp.ClientSession, info: ProductInfo,
                           reference: float | None) -> None:
        discount, ref, source = evaluate(info, reference)
        status = {"ok": "ok", "no_price": "sin precio" if info.other_offers else "sin ofertas",
                  "not_found": "no encontrado"}.get(info.status, info.status)
        if not info.available:
            status = "agotado"
        elif self.s.only_prime and info.price is not None and not info.prime:
            status = "no Prime"
        is_deal = (
            info.price is not None and info.available
            and (info.prime or not self.s.only_prime)
            and discount >= self.s.min_discount
            and (self.s.max_price <= 0 or info.price <= self.s.max_price)
        )
        if is_deal:
            status = "🚨 ERROR"
            if self.db.should_alert(info.asin, info.market, info.price, self.s.alert_cooldown_hours):
                text = telegram.format_alert(info.title, info.asin, info.market, info.price,
                                             ref, discount, info.url, source)
                sent, detail = await telegram.send_message(
                    session, self.s.telegram_token, self.s.telegram_chat_id, text)
                self.db.record_alert(info.asin, info.market, info.price, ref, discount, sent)
                self.alerts += 1
                self._log(f"🚨 {info.asin} ({info.market}) {info.price:.2f} € -{discount:.0f}% "
                          f"→ Telegram: {'enviado' if sent else detail}")
            else:
                status = "🚨 ya avisado"
        self._finish(info.asin, info.market, info, status, discount, ref)

    def _finish(self, asin: str, market: str, info: ProductInfo | None, status: str,
                discount: float = 0.0, reference: float | None = None) -> None:
        self.done += 1
        self._emit("row", {
            "asin": asin,
            "market": market,
            "title": (info.title if info else "") or "",
            "price": info.price if info else None,
            "reference": reference,
            "discount": discount,
            "status": status,
            "url": info.url if info else f"https://{MARKETPLACES[market][0]}/dp/{asin}",
        })
        self._emit("progress", self.done, self.total, self.alerts)
