"""Sesión de navegador Playwright con rotación de User-Agent y proxy."""
from __future__ import annotations

import itertools
import random

from playwright.async_api import Browser, BrowserContext, Page, Playwright

from . import config


class BrowserSession:
    def __init__(self, pw: Playwright):
        self._pw = pw
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.page: Page | None = None
        usable = [p for p in config.PROXIES if p.get("server")]
        self._proxies = itertools.cycle(usable) if usable else None

    async def start(self) -> None:
        self._browser = await self._pw.chromium.launch(headless=config.HEADLESS)
        await self.new_identity()

    async def new_identity(self) -> None:
        """Contexto limpio: cookies nuevas, otro User-Agent y el siguiente proxy."""
        if self._context:
            await self._context.close()
        proxy = next(self._proxies) if self._proxies else None
        self._context = await self._browser.new_context(
            user_agent=random.choice(config.USER_AGENTS),
            locale="es-ES",
            timezone_id="Europe/Madrid",
            viewport={"width": random.choice([1366, 1440, 1536, 1920]),
                      "height": random.choice([768, 864, 900, 1080])},
            extra_http_headers={"Accept-Language": "es-ES,es;q=0.9"},
            proxy={k: v for k, v in proxy.items() if v} if proxy else None,
        )
        self._context.set_default_timeout(config.PAGE_TIMEOUT_MS)
        self.page = await self._context.new_page()

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
