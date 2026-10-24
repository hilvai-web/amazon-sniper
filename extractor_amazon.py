"""Extrae los 1000 primeros ASINs de valor de una categoría de Best Sellers de amazon.es.

Uso:  python extractor_amazon.py <categoria>          (reanuda si hay progreso guardado)
      python extractor_amazon.py <categoria> --reset  (empieza de cero)
      python extractor_amazon.py --listar             (muestra las categorías)

Sin categoría, o con una que no existe, muestra las válidas y usa la de por
defecto (electronica_hogar). El resultado, amazon_top1000_<categoria>.csv, se
carga directamente en Amazon Sniper.
"""
import argparse
import asyncio
import sys

from playwright.async_api import async_playwright

from extractor import config
from extractor.browser import BrowserSession
from extractor.crawler import Blocked, crawl, log
from extractor.storage import AsinStore, load_state, save_state


def print_categories() -> None:
    log("Categorías disponibles:")
    for key, cat in config.CATEGORIAS.items():
        mark = "  (por defecto)" if key == config.DEFAULT_CATEGORY else ""
        log(f"  {key:<28} {cat['nombre']}{mark}")


def resolve_category(arg: str | None) -> str:
    """Valida la categoría; si falta o no existe, avisa y usa la de por defecto."""
    key = (arg or "").strip().lower()
    if key in config.CATEGORIAS:
        return key
    log(f"Categoría '{arg}' no válida." if arg else "No se indicó categoría.")
    print_categories()
    log(f"\nUsando la categoría por defecto: {config.DEFAULT_CATEGORY}\n")
    return config.DEFAULT_CATEGORY


async def main(key: str, reset: bool) -> int:
    category = config.CATEGORIAS[key]
    csv_path, state_path = config.output_csv(key), config.state_file(key)
    log(f"Categoría: {category['nombre']} ({key}) -> {csv_path}")

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
            await crawl(session, store, queue, visited, category, state_path)
        except Blocked as exc:
            log(f"\nParado: {exc}. Vuelve a ejecutarlo más tarde y continuará donde lo dejó.")
        except (KeyboardInterrupt, asyncio.CancelledError):
            log("\nInterrumpido por el usuario.")
        finally:
            store.save()
            save_state(state_path, queue, visited)
            await session.close()

    log(f"\n{len(store)} ASINs únicos guardados en {csv_path.resolve()}")
    if store.full:
        state_path.unlink(missing_ok=True)
        return 0
    if not queue:
        log("Aviso: no quedan subcategorías por recorrer antes de llegar al objetivo.")
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("categoria", nargs="?", help="clave de CATEGORIAS, p. ej. telefonia_movil")
    parser.add_argument("--reset", action="store_true", help="ignora el progreso guardado")
    parser.add_argument("--listar", action="store_true", help="muestra las categorías y sale")
    args = parser.parse_args()
    if args.listar:
        print_categories()
        sys.exit(0)
    try:
        sys.exit(asyncio.run(main(resolve_category(args.categoria), args.reset)))
    except KeyboardInterrupt:
        sys.exit(130)
