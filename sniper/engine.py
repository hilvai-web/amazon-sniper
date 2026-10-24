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
from typing import Awaitable, Callable

import aiohttp
import certifi

from . import telegram
from .config import MARKETPLACES, Settings
from .db import Database
from .scraper import Blocked, IdentityPool, ProductInfo, build_headers, parse_product

Fetcher = Callable[[aiohttp.ClientSession, str, dict, str | None, float], Awaitable[tuple[int, str]]]

MAX_RETRIES = 2


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


class ScanEngine:
    def __init__(self, settings: Settings, asins: list[str], db: Database,
                 events: "queue.Queue[tuple]", proxies: list[str] | None = None,
                 fetcher: Fetcher = http_fetch):
        self.s = settings
        self.asins = asins
        self.db = db
        self.events = events
        self.identities = IdentityPool(proxies or [], settings.batch_size)
        self.fetcher = fetcher
        self.is_excluded = keyword_matcher(settings.exclude_keywords)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._resume_at = 0.0       # pausa global tras bloqueo
        self._block_streak = 0
        self.done = 0
        self.total = 0
        self.alerts = 0

    # ------------------------------------------------------------ control
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="scan-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

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
        connector = aiohttp.TCPConnector(ssl=ssl_ctx, limit=self.s.concurrency * 2)
        async with aiohttp.ClientSession(connector=connector,
                                         cookie_jar=aiohttp.CookieJar()) as session:
            workers = [asyncio.create_task(self._worker(i, session, jobs))
                       for i in range(max(1, self.s.concurrency))]
            all_done = asyncio.create_task(jobs.join())
            while not self._stop.is_set() and not all_done.done():
                await asyncio.sleep(0.3)
            all_done.cancel()
            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

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
                wait = self._resume_at - time.time()
                if wait > 0:
                    await self._sleep(wait)
                if self._stop.is_set():
                    return
                try:
                    await self._scan_one(session, asin, market)
                    self._block_streak = 0
                except Blocked as exc:
                    self._on_block(str(exc))
                    if tries < MAX_RETRIES:
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
        try:
            status, html = await self.fetcher(session, url, build_headers(ua, lang),
                                              proxy, self.s.timeout)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            self.identities.report_failure(proxy)
            raise
        if status in (503, 429):
            self.identities.report_failure(proxy)
            raise Blocked(f"HTTP {status}")
        if status == 404:
            self.identities.report_success(proxy)
            self.db.record_scan(asin, market, None, None, None)
            self._finish(asin, market, None, "no existe")
            return
        if status >= 400:
            self.identities.report_failure(proxy)
            raise aiohttp.ClientResponseError(None, (), status=status)  # type: ignore[arg-type]

        info = parse_product(html, asin, market, url)
        if info.status == "captcha":
            self.identities.report_failure(proxy)
            raise Blocked("captcha")
        self.identities.report_success(proxy)

        reference = self.db.reference_price(asin, market) if self.s.use_history else None
        self.db.record_scan(asin, market, info.title, info.price, info.list_price)
        reason = low_value_reason(info, reference, self.s.min_value, self.is_excluded)
        if reason:
            self.db.mark_excluded(asin, market, reason)
            self._finish(asin, market, info, reason)
            return
        await self._maybe_alert(session, info, reference)

    async def _maybe_alert(self, session: aiohttp.ClientSession, info: ProductInfo,
                           reference: float | None) -> None:
        discount, ref, source = evaluate(info, reference)
        status = {"ok": "ok", "no_price": "sin precio", "not_found": "no encontrado"}.get(info.status, info.status)
        if not info.available:
            status = "agotado"
        is_deal = (
            info.price is not None and info.available
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
