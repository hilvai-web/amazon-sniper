"""Lanza el extractor de ASINs (proyecto AmazonExtractor) desde la app.

El extractor vive en su propia carpeta con su .venv y Playwright, así que se
ejecuta como proceso aparte y su salida se reenvía a la cola de eventos de la GUI:

    ("log", str)                         línea de salida del extractor
    ("extract_done", categoria, código)  ha terminado (0 = completo)
"""
from __future__ import annotations

import importlib.util
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

DEFAULT_DIR = Path.home() / "AmazonExtractor"

# Se usa solo si no se encuentra extractor/config.py
FALLBACK_CATEGORIES = {
    "electronica_hogar": "Electrónica",
    "telefonia_movil": "Móviles y telefonía",
    "tv_video": "TV, vídeo y home cinema",
    "audio_hifi": "Audio y Hi-Fi",
    "informatica": "Informática",
}


def extractor_dir(configured: str = "") -> Path:
    return Path(configured) if configured else DEFAULT_DIR


def is_installed(base: Path) -> bool:
    return (base / "extractor_amazon.py").exists() and python_exe(base).exists()


def python_exe(base: Path) -> Path:
    if sys.platform == "win32":
        return base / ".venv" / "Scripts" / "python.exe"
    return base / ".venv" / "bin" / "python"


def load_categories(base: Path) -> tuple[dict[str, str], str]:
    """{clave: nombre visible} leído de extractor/config.py, y la clave por defecto."""
    path = base / "extractor" / "config.py"
    try:
        spec = importlib.util.spec_from_file_location("_extractor_config", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        cats = {k: v["nombre"] for k, v in module.CATEGORIAS.items()}
        return cats, module.DEFAULT_CATEGORY
    except Exception:  # noqa: BLE001
        return dict(FALLBACK_CATEGORIES), "electronica_hogar"


def csv_path(base: Path, key: str) -> Path:
    return base / f"amazon_top1000_{key}.csv"


def is_complete(base: Path, key: str) -> bool:
    """CSV existente sin progreso pendiente = extracción terminada."""
    return csv_path(base, key).exists() and not (base / f"extractor_estado_{key}.json").exists()


class ExtractRun:
    def __init__(self, base: Path, key: str, events: "queue.Queue[tuple]"):
        self.base, self.key, self.events = base, key, events
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        # una lista ya completa se rehace desde cero; una a medias se reanuda
        args = [str(python_exe(self.base)), "-u", "extractor_amazon.py", self.key]
        if is_complete(self.base, self.key):
            args.append("--reset")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.proc = subprocess.Popen(
            args, cwd=self.base, env=env, creationflags=flags,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            line = line.rstrip()
            if line:
                self.events.put(("log", "[extractor] " + line))
        self.events.put(("extract_done", self.key, self.proc.wait()))

    @property
    def running(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def cancel(self) -> None:
        """Para el extractor y su navegador. Lo ya guardado en el CSV se conserva."""
        if not self.running:
            return
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                           creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True)
        else:
            self.proc.terminate()
