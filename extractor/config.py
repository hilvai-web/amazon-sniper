"""Ajustes del extractor. Edita este archivo para cambiar el comportamiento."""
from pathlib import Path

BASE_URL = "https://www.amazon.es"
TARGET_ASINS = 1000          # se detiene al llegar exactamente a esta cifra

# Categorías que acepta la línea de comandos: python extractor_amazon.py <clave>
#   ruta: departamento de Best Sellers en amazon.es
#   sub:  (opcional) texto de la subcategoría que se busca dentro del departamento
#         al arrancar; si Amazon le cambia el nombre, se usa el departamento entero
CATEGORIAS: dict[str, dict] = {
    "electronica_hogar": {"nombre": "Electrónica", "ruta": "/gp/bestsellers/electronics"},
    "telefonia_movil":   {"nombre": "Móviles y telefonía", "ruta": "/gp/bestsellers/electronics", "sub": "movil"},
    "audio_hifi":        {"nombre": "Audio y Hi-Fi", "ruta": "/gp/bestsellers/electronics", "sub": "audio"},
    "fotografia":        {"nombre": "Fotografía y videocámaras", "ruta": "/gp/bestsellers/electronics", "sub": "fotografia"},
    "tv_video":          {"nombre": "TV, vídeo y home cinema", "ruta": "/gp/bestsellers/electronics", "sub": "tv"},
    "wearables":         {"nombre": "Wearables y relojes inteligentes", "ruta": "/gp/bestsellers/electronics", "sub": "wearable"},
    "informatica":       {"nombre": "Informática", "ruta": "/gp/bestsellers/computers"},
    "videojuegos":       {"nombre": "Videojuegos", "ruta": "/gp/bestsellers/videogames"},
    "grandes_electrodomesticos": {"nombre": "Grandes electrodomésticos", "ruta": "/gp/bestsellers/appliances"},
    "hogar_cocina":      {"nombre": "Hogar y cocina", "ruta": "/gp/bestsellers/kitchen"},
}
DEFAULT_CATEGORY = "electronica_hogar"


def output_csv(category: str) -> Path:
    return Path(f"amazon_top{TARGET_ASINS}_{category}.csv")


def state_file(category: str) -> Path:
    return Path(f"extractor_estado_{category}.json")

# Solo productos de valor alto: precio visible en el ranking >= MIN_PRICE (euros)
MIN_PRICE = 100.0
# Lista negra: se descarta el producto (o la subcategoría) si alguna de estas
# palabras aparece en el nombre del producto o en su URL.
# Sin tildes ni mayúsculas. Añade o quita las que quieras.
EXCLUDE_KEYWORDS = [
    "pila", "pilas", "alcalina", "alcalinas", "bateria", "baterias", "battery", "batteries",
    "funda", "fundas", "case", "carcasa", "protector", "cristal templado",
    "cable", "cables", "hdmi", "cargador", "cargadores", "charger", "regleta", "regletas",
    "adaptador", "adaptadores", "cartucho", "cartuchos", "tinta", "toner", "recambio",
    "recambios", "correa", "consumible", "consumibles", "bombilla", "bombillas",
]
# Solo se mira el nombre del producto: el título hasta la primera coma o
# paréntesis (máx. TITLE_WORDS palabras). Así "Sony WH-1000XM5 Auriculares,
# 30 h de batería, cable incluido" no se descarta, pero "Duracell Pilas AA" sí.
TITLE_WORDS = 10

PAGES_PER_CATEGORY = 2                           # Amazon muestra 50 por página, máx. 100 por categoría


HEADLESS = True              # False para ver el navegador mientras trabaja
DELAY_RANGE = (2.0, 5.0)     # espera aleatoria entre páginas (segundos)
PAGE_TIMEOUT_MS = 45_000
MAX_RETRIES = 2              # reintentos por página ante captcha/timeout
MAX_CONSECUTIVE_FAILS = 4    # tras N páginas fallidas seguidas guarda y para

# Proxy opcional (tu servicio residencial contratado). Deja "server" vacío para
# usar tu conexión. Formato de Playwright:
#   {"server": "http://host:puerto", "username": "usuario", "password": "clave"}
# Puedes poner varios en la lista: se rota al siguiente cuando una IP falla.
PROXIES: list[dict] = [
    {"server": "", "username": "", "password": ""},
]

# User-Agents reales de Windows con navegadores Chromium modernos (coherentes
# con el motor que lanza Playwright).
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
]
