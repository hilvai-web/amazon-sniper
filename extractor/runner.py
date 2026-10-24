"""Extracción completa de una categoría: la usan la línea de comandos y la app."""
from __future__ import annotations

import asyncio

from playwright.async_api import async_playwright

from . import config
from .browser import BrowserSession
from .crawler import Blocked, crawl, log
from .storage import AsinStore, load_state, save_state


def start_url_for(key: str) -> str:
    """URL de Best Sellers con la que arranca la cola de Playwright."""
    return config.CATEGORIAS[key]


def is_complete(key: str) -> bool:
    """CSV existente sin progreso pendiente = extracción terminada."""
    return config.output_csv(key).exists() and not config.state_file(key).exists()


async def run_extraction(key: str, reset: bool = False) -> int:
    """Devuelve 0 si se reunieron los 1000 ASINs, 1 si se guardó una lista parcial."""
    start_url = start_url_for(key)
    csv_path, state_path = config.output_csv(key), config.state_file(key)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    log(f"Categoría: {config.NOMBRES.get(key, key)} ({key})")
    log(f"URL: {start_url}")

    store = AsinStore(csv_path, config.TARGET_ASINS)
    queue, visited = ([], set()) if reset else load_state(state_path)
    if not reset:
        store.load()
        if len(store):
            log(f"Reanudando: {len(store)} ASINs ya guardados, {len(queue)} categorías pendientes")

    async with async_playwright() as pw:
        session = BrowserSession(pw)
        await session.start()
        try:
            await crawl(session, store, queue, visited, start_url, state_path)
        except Blocked as exc:
            log(f"Parado: {exc}. Vuelve a intentarlo más tarde y continuará donde lo dejó.")
        except (KeyboardInterrupt, asyncio.CancelledError):
            log("Extracción interrumpida.")
        finally:
            store.save()
            save_state(state_path, queue, visited)
            await session.close()

    log(f"{len(store)} ASINs únicos guardados en {csv_path.resolve()}")
    if store.full:
        state_path.unlink(missing_ok=True)
        return 0
    if not queue:
        log("Aviso: no quedan subcategorías por recorrer antes de llegar al objetivo.")
    return 1
