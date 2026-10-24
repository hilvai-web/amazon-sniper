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

from extractor import config
from extractor.crawler import log
from extractor.runner import run_extraction, start_url_for  # noqa: F401 (start_url_for: API)


def print_categories() -> None:
    log("Categorías disponibles:")
    for key, url in config.CATEGORIAS.items():
        mark = "  (por defecto)" if key == config.DEFAULT_CATEGORY else ""
        log(f"  {key:<26} {config.NOMBRES.get(key, key)}{mark}\n  {'':<26} {url}")


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
    return await run_extraction(key, reset)


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
