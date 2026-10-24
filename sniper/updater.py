"""Auto-actualización desde GitHub Releases.

Cada versión publicada en https://github.com/hilvai-web/amazon-sniper/releases
lleva adjunto AmazonSniper.exe. La app compara su versión con la última
publicada y, si hay una nueva, descarga el .exe junto al actual, cierra y un
pequeño .bat sustituye el archivo y vuelve a abrirla. config.json, alertas.db
y la carpeta listas no se tocan.
"""
from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Callable

import certifi

REPO = "hilvai-web/amazon-sniper"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
ASSET_NAME = "AmazonSniper.exe"

_SSL = ssl.create_default_context(cafile=certifi.where())


def parse_version(text: str) -> tuple[int, ...]:
    """'v2.1.0' -> (2, 1, 0). Partes no numéricas cuentan como 0."""
    parts = text.strip().lstrip("vV").split(".")
    return tuple(int(p) if p.isdigit() else 0 for p in parts)


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def check_latest(timeout: float = 10) -> tuple[str, str] | None:
    """(versión, url del .exe) de la última Release, o None si no hay/sin red."""
    req = urllib.request.Request(API_LATEST, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "AmazonSniper-updater"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as resp:
            data = json.load(resp)
    except (OSError, ValueError):
        return None
    for asset in data.get("assets", []):
        if asset.get("name") == ASSET_NAME:
            return data.get("tag_name", ""), asset["browser_download_url"]
    return None


def download(url: str, dest: Path, progress: Callable[[int], None] | None = None) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "AmazonSniper-updater"})
    tmp = dest.with_suffix(".part")
    with urllib.request.urlopen(req, timeout=60, context=_SSL) as resp, tmp.open("wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 16):
            out.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done * 100 // total)
    os.replace(tmp, dest)


def install_and_restart(new_exe: Path) -> None:
    """Lanza un .bat que espera a que la app se cierre, cambia el .exe y la reabre.
    Quien llama debe cerrar la app justo después."""
    current = Path(sys.executable).resolve()
    bat = current.with_name("_actualizar_sniper.bat")
    bat.write_text(
        "@echo off\r\n"
        "ping -n 3 127.0.0.1 >nul\r\n"
        ":retry\r\n"
        f'move /y "{new_exe}" "{current}" >nul 2>&1 || (ping -n 2 127.0.0.1 >nul & goto retry)\r\n'
        f'start "" "{current}"\r\n'
        'del "%~f0"\r\n',
        encoding="ascii", errors="replace",
    )
    subprocess.Popen(["cmd", "/c", str(bat)], cwd=str(current.parent),
                     creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
                     close_fds=True)
