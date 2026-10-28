"""Punto de entrada de Amazon Sniper (también el script que compila PyInstaller)."""
import asyncio
import sys


def main() -> None:
    if sys.platform == "win32":
        # el selector loop evita avisos de aiohttp al cerrar en Windows
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if "--segundo-plano" in sys.argv or "--background" in sys.argv:
        # sin ventana (Programador de tareas): registro en ejecucion.log
        from sniper.background import run as run_background
        sys.exit(run_background(once="--una-vez" in sys.argv))
    from sniper.gui import run
    run()


if __name__ == "__main__":
    main()
