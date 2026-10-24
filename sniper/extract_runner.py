"""Ejecuta el extractor de ASINs dentro de la propia app, en un hilo aparte.

Las listas se guardan en la carpeta "listas" junto al ejecutable. Los mensajes
del extractor y su final llegan a la GUI por la cola de eventos:

    ("log", str)                         línea del extractor
    ("extract_done", categoria, código)  ha terminado (0 = 1000 ASINs reunidos)
"""
from __future__ import annotations

import asyncio
import queue
import sys
import threading
import time
from pathlib import Path

from extractor import config as xconfig
from extractor import crawler, runner

from .config import app_dir

LISTS_DIR = app_dir() / "listas"
xconfig.DATA_DIR = LISTS_DIR


def categories() -> tuple[dict[str, str], str]:
    """{clave: nombre visible} y la clave por defecto."""
    return ({k: xconfig.NOMBRES.get(k, k) for k in xconfig.CATEGORIAS}, xconfig.DEFAULT_CATEGORY)


def csv_path(key: str) -> Path:
    return xconfig.output_csv(key)


def list_age_hours(key: str) -> float | None:
    """Horas desde que se guardó la lista de la categoría (None si no existe)."""
    path = csv_path(key)
    return (time.time() - path.stat().st_mtime) / 3600 if path.exists() else None


def is_complete(key: str) -> bool:
    return runner.is_complete(key)


class ExtractRun:
    def __init__(self, key: str, events: "queue.Queue[tuple]"):
        self.key, self.events = key, events
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        # una lista ya completa se rehace desde cero; una a medias se reanuda
        reset = is_complete(self.key)
        crawler.set_log_sink(lambda line: self.events.put(("log", "[lista] " + line)))
        self._thread = threading.Thread(target=self._run, args=(reset,), name="extractor", daemon=True)
        self._thread.start()

    def _run(self, reset: bool) -> None:
        # Playwright lanza el navegador como subproceso: en Windows necesita el
        # bucle Proactor (main.py fija el Selector por defecto para aiohttp)
        self._loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
        code = 1
        try:
            self._task = self._loop.create_task(runner.run_extraction(self.key, reset))
            code = self._loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            code = 1
        except Exception as exc:  # noqa: BLE001
            self.events.put(("log", f"[lista] Error: {exc}"))
            code = 2
        finally:
            self._loop.close()
        self.events.put(("extract_done", self.key, code))

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def cancel(self) -> None:
        """Para la extracción; lo ya guardado en la lista se conserva."""
        if self.running and self._loop and self._task:
            self._loop.call_soon_threadsafe(self._task.cancel)
