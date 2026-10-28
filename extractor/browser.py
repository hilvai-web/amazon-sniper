"""Sesión de navegador Playwright con rotación de User-Agent y proxy."""
from __future__ import annotations

import asyncio
import itertools
import random
import time

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
        self._proxy: dict | None = None
        self._ua = random.choice(config.USER_AGENTS)
        self._viewport = {"width": 1536, "height": 864}

    async def _launch(self, headless: bool) -> Browser:
        errors = []
        for channel in config.BROWSER_CHANNELS:
            try:
                return await self._pw.chromium.launch(
                    headless=headless, **({"channel": channel} if channel else {}))
            except Exception as exc:  # noqa: BLE001 - probamos el siguiente navegador
                errors.append(f"{channel or 'chromium'}: {str(exc).splitlines()[0]}")
        raise RuntimeError("No encuentro ningún navegador compatible (Microsoft Edge o Chrome). "
                           "Instala Edge o Chrome y vuelve a intentarlo.\n" + "\n".join(errors))

    async def start(self) -> None:
        self._browser = await self._launch(config.HEADLESS)
        await self.new_identity()

    def _context_options(self) -> dict:
        return dict(
            user_agent=self._ua,
            locale="es-ES",
            timezone_id="Europe/Madrid",
            viewport=self._viewport,
            extra_http_headers={"Accept-Language": "es-ES,es;q=0.9"},
            proxy={k: v for k, v in self._proxy.items() if v} if self._proxy else None,
        )

    async def _open_context(self, storage_state: dict | None = None) -> None:
        if self._context:
            await self._context.close()
        self._context = await self._browser.new_context(storage_state=storage_state,
                                                        **self._context_options())
        self._context.set_default_timeout(config.PAGE_TIMEOUT_MS)
        self.page = await self._context.new_page()

    async def new_identity(self) -> None:
        """Contexto limpio: cookies nuevas, otro User-Agent y el siguiente proxy."""
        self._proxy = next(self._proxies) if self._proxies else None
        self._ua = random.choice(config.USER_AGENTS)
        self._viewport = {"width": random.choice([1366, 1440, 1536, 1920]),
                          "height": random.choice([768, 864, 900, 1080])}
        await self._open_context()

    async def solve_captcha_by_hand(self, url: str, is_captcha) -> bool:
        """Abre una ventana visible con la misma sesión para que el usuario
        resuelva el captcha. Cuando desaparece, se cierra la ventana y se sigue
        con las cookies ya validadas. False si se agota el tiempo o la cierra."""
        state = await self._context.storage_state()
        visible = await self._launch(headless=False)
        solved = False
        try:
            ctx = await visible.new_context(storage_state=state, **self._context_options())
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded")
            deadline = time.monotonic() + config.MANUAL_CAPTCHA_MINUTES * 60
            while time.monotonic() < deadline:
                await asyncio.sleep(2)
                try:
                    html = await page.content()
                except Exception:  # noqa: BLE001 - ventana cerrada
                    break
                if not is_captcha(html):
                    solved = True
                    state = await ctx.storage_state()
                    break
        finally:
            await visible.close()
        if solved:
            await self._open_context(storage_state=state)
        return solved

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
