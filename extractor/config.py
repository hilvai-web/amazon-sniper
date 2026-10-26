"""Ajustes del extractor. Edita este archivo para cambiar el comportamiento."""
from pathlib import Path

BASE_URL = "https://www.amazon.es"
TARGET_ASINS = 1000          # se detiene al llegar exactamente a esta cifra

# Categorías que acepta la línea de comandos: python extractor_amazon.py <clave>
# Cada clave apunta al listado de Best Sellers de amazon.es donde empieza la
# extracción; desde ahí se recorren sus subcategorías hasta reunir 1000 ASINs.
CATEGORIAS: dict[str, str] = {
    "electronica_hogar": "https://www.amazon.es/gp/bestsellers/electronics",               # TV, Home Cinema y Hogar
    "televisores": "https://www.amazon.es/gp/bestsellers/electronics/934359031",           # Televisores (Smart TV, OLED, QLED...)
    "telefonia_movil": "https://www.amazon.es/gp/bestsellers/electronics/17425698031",     # Smartphones y Smartwatches
    "audio_y_video": "https://www.amazon.es/gp/bestsellers/electronics/665477031",         # Auriculares y Altavoces
    "computacion_y_portatiles": "https://www.amazon.es/gp/bestsellers/computers",          # Laptops y Tablets
    "fotografia_y_video": "https://www.amazon.es/gp/bestsellers/electronics/664660031",    # Cámaras y Vídeo profesional
}
# Nombre que se muestra en la app y en los mensajes
NOMBRES: dict[str, str] = {
    "electronica_hogar": "Electrónica, TV y hogar",
    "televisores": "Televisores",
    "telefonia_movil": "Smartphones y smartwatches",
    "audio_y_video": "Auriculares y altavoces",
    "computacion_y_portatiles": "Portátiles, tablets e informática",
    "fotografia_y_video": "Cámaras y vídeo",
}
DEFAULT_CATEGORY = "electronica_hogar"


# Carpeta donde se guardan los CSV y el progreso. La app la cambia a su carpeta
# "listas" junto al .exe; en línea de comandos es la carpeta actual.
DATA_DIR = Path(".")


def output_csv(category: str) -> Path:
    return DATA_DIR / f"amazon_top{TARGET_ASINS}_{category}.csv"


def state_file(category: str) -> Path:
    return DATA_DIR / f"extractor_estado_{category}.json"

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


# Navegador que usa Playwright, en orden de preferencia. "msedge" y "chrome" son
# los que ya tiene instalados el PC (Edge viene con Windows), así el .exe no
# necesita descargar ningún navegador. None = Chromium propio de Playwright.
BROWSER_CHANNELS = ["msedge", "chrome", None]
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
