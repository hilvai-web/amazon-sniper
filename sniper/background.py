"""Modo en segundo plano: sin ventana, para el Programador de tareas de Windows.

    AmazonSniper.exe --segundo-plano        escanea en bucle hasta que se apague el PC
    AmazonSniper.exe --segundo-plano --una-vez   una sola vuelta y termina

Usa la misma configuración (config.json), base de datos (alertas.db) y listas
que la ventana. Todo lo que pasa se apunta en ejecucion.log junto al .exe.
Si sale un captcha, avisa por Telegram y reintenta solo (10 s y luego cada
minuto), como en la ventana; el extractor espera y reintenta en lugar de abrir
el navegador.
"""
from __future__ import annotations

import logging
import queue
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import DB_PATH, Settings, app_dir

LOG_FILE = app_dir() / "ejecucion.log"
LOCK_FILE = app_dir() / "segundo_plano.lock"
NOTHING_DUE_PAUSE = 600     # sin nada pendiente, espera 10 min antes de mirar otra vez

log = logging.getLogger("sniper")


def setup_logging() -> None:
    handler = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


class InstanceLock:
    """Evita dos copias en segundo plano a la vez (el Programador puede lanzar otra)."""

    def __init__(self, path: Path):
        self.path, self._fh = path, None

    def acquire(self) -> bool:
        self._fh = open(self.path, "a+")  # noqa: SIM115 - se mantiene abierto mientras dura
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._fh.close()
            self._fh = None
            return False
        return True


def is_running_in_background() -> bool:
    """True si hay una copia en segundo plano funcionando (para avisar desde la ventana)."""
    lock = InstanceLock(LOCK_FILE)
    if lock.acquire():
        lock._fh.close()
        return False
    return True


def _drain(events: "queue.Queue[tuple]", counters: dict) -> None:
    while True:
        try:
            event = events.get_nowait()
        except queue.Empty:
            return
        kind = event[0]
        if kind == "log":
            text = event[1][9:] if event[1][:2].isdigit() and event[1][8:9] == " " else event[1]
            log.info(text)
        elif kind == "row":
            row = event[1]
            counters["rows"] += 1
            if row["status"].startswith("🚨"):
                log.warning("%s %s %s %.2f € -%.0f%% %s", row["status"], row["asin"], row["market"],
                            row["price"] or 0, row["discount"], row["title"][:80])
        elif kind == "captcha":
            if len(event) > 2 and event[2]:
                log.info("Captcha de Amazon en %s: continúo solo.", event[1])
            else:
                log.warning("Captcha de Amazon en %s tras varios intentos: aviso por Telegram.", event[1])
        elif kind == "captcha_extract":
            log.warning(event[1])
        elif kind == "finished":
            counters["finished"] = event[1]
        elif kind == "extract_done":
            counters["extract_done"] = event[2]


def _product_list(settings: Settings) -> list[str]:
    """La lista que se vigila: el archivo propio si se cargó uno, o la de la categoría
    (rehecha si falta, está a medias o tiene más de list_max_age_hours)."""
    from . import extract_runner
    from .scraper import load_asins

    key = settings.category
    own = extract_runner.csv_path(key)
    if settings.last_asin_file and Path(settings.last_asin_file) != own and Path(settings.last_asin_file).exists():
        asins = load_asins(settings.last_asin_file)
        log.info("Lista propia: %s (%d productos)", settings.last_asin_file, len(asins))
        return asins
    age = extract_runner.list_age_hours(key)
    if age is None or age >= settings.list_max_age_hours or not extract_runner.is_complete(key):
        from extractor import config as xconfig
        xconfig.MANUAL_CAPTCHA = False          # sin ventanas: espera y reintenta
        xconfig.HEADLESS = True
        log.info("Rehaciendo la lista de %s…", key)
        events: queue.Queue[tuple] = queue.Queue()
        run = extract_runner.ExtractRun(key, events)
        run.start()
        counters = {"rows": 0}
        while run.running:
            _drain(events, counters)
            time.sleep(1)
        _drain(events, counters)
    return load_asins(own) if own.exists() else []


def run_cycle(settings: Settings, db) -> tuple[int, str]:
    """Una vuelta completa. Devuelve (productos mirados, motivo de fin)."""
    from .engine import ScanEngine
    from .scraper import load_proxies

    asins = _product_list(settings)
    if not asins:
        log.error("No hay productos que vigilar (no se pudo crear la lista).")
        return 0, "sin lista"
    proxies: list[str] = []
    if settings.proxies_file:
        try:
            proxies = load_proxies(settings.proxies_file)
        except OSError:
            log.warning("No se pudo leer el archivo de proxies; se usa conexión directa.")
    events: queue.Queue[tuple] = queue.Queue()
    engine = ScanEngine(settings, asins, db, events, proxies)
    engine.start()
    counters = {"rows": 0, "finished": ""}
    while engine.running:
        _drain(events, counters)
        time.sleep(1)
    _drain(events, counters)
    log.info("Vuelta terminada: %d productos mirados, %d alertas. %s",
             counters["rows"], engine.alerts, counters["finished"])
    return counters["rows"], counters["finished"]


def run(once: bool = False) -> int:
    setup_logging()
    lock = InstanceLock(LOCK_FILE)
    if not lock.acquire():
        log.info("Ya hay una copia en segundo plano funcionando; esta se cierra.")
        return 0
    import os

    from .db import Database

    PID_FILE.write_text(str(os.getpid()))
    log.info("=== Amazon Sniper en segundo plano (%s) ===", "una vuelta" if once else "continuo")
    db = Database(DB_PATH)
    cycle = 0
    try:
        while True:
            cycle += 1
            settings = Settings.load()          # recoge cambios hechos desde la ventana
            log.info("Vuelta %d · categoría %s · mercados %s", cycle, settings.category, ",".join(settings.markets))
            try:
                done, reason = run_cycle(settings, db)
            except Exception:  # noqa: BLE001 - un fallo no debe tumbar el proceso de fondo
                log.exception("Error en la vuelta %d; se reintenta en la siguiente.", cycle)
                done, reason = 0, "error"
            if once:
                return 0
            pause = settings.cycle_pause_seconds if done else NOTHING_DUE_PAUSE
            log.info("Siguiente vuelta en %.0f s.", pause)
            time.sleep(pause)
    except KeyboardInterrupt:
        log.info("Detenido.")
        return 0
    finally:
        PID_FILE.unlink(missing_ok=True)


# ------------------------------------------------- arranque con Windows
TASK_NAME = "Amazon Sniper (segundo plano)"
PID_FILE = app_dir() / "segundo_plano.pid"


def _exe() -> str:
    return sys.executable if getattr(sys, "frozen", False) else f'"{sys.executable}" "{app_dir() / "main.py"}"'


def install_autostart() -> str:
    """Programa una tarea de Windows que arranca el modo oculto al iniciar sesión."""
    import subprocess
    target = f'"{sys.executable}" --segundo-plano' if getattr(sys, "frozen", False) \
        else f'{_exe()} --segundo-plano'
    result = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", target, "/SC", "ONLOGON",
                             "/RL", "LIMITED", "/F"], capture_output=True, text=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode == 0:
        return f"Tarea «{TASK_NAME}» creada: se iniciará oculta cada vez que entres en Windows."
    # sin permisos para el Programador: acceso directo en la carpeta Inicio del usuario
    startup = Path.home() / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup"
    link = startup / "Amazon Sniper (segundo plano).lnk"
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
          f"$s.TargetPath='{sys.executable}';$s.Arguments='--segundo-plano';"
          f"$s.WorkingDirectory='{app_dir()}';$s.WindowStyle=7;$s.Save()")
    alt = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if alt.returncode == 0:
        return "Añadido a la carpeta Inicio de Windows: se iniciará oculto cada vez que entres."
    return f"No se pudo programar: {(result.stderr or alt.stderr).strip()[:200]}"


def remove_autostart() -> str:
    import subprocess
    subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    link = Path.home() / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup/Amazon Sniper (segundo plano).lnk"
    link.unlink(missing_ok=True)
    return "Quitado del arranque de Windows."


def start_now() -> None:
    """Lanza ya el modo oculto, separado de la ventana."""
    import subprocess
    args = [sys.executable, "--segundo-plano"] if getattr(sys, "frozen", False) \
        else [sys.executable, str(app_dir() / "main.py"), "--segundo-plano"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(args, cwd=str(app_dir()), creationflags=flags, close_fds=True)


def stop_running() -> bool:
    """Para la copia en segundo plano que esté funcionando."""
    import os
    import signal
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return False
    try:
        if sys.platform == "win32":
            import subprocess
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    PID_FILE.unlink(missing_ok=True)
    return True
