"""Punto de entrada de Amazon Sniper (también el script que compila PyInstaller)."""
import asyncio
import sys


def main() -> None:
    if sys.platform == "win32":
        # el selector loop evita avisos de aiohttp al cerrar en Windows
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    from sniper.gui import run
    run()


if __name__ == "__main__":
    main()
