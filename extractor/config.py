"""Ajustes del extractor. Edita este archivo para cambiar el comportamiento."""
from pathlib import Path

BASE_URL = "https://www.amazon.es"
TARGET_ASINS = 1000          # se detiene al llegar exactamente a esta cifra

# Categorías que acepta la línea de comandos: python extractor_amazon.py <clave>
# Cada clave apunta a la URL donde empieza la extracción:
#  - una búsqueda de amazon.es (/s?k=...&i=electronics): se recorren sus páginas
#    de resultados, ordenadas por "más vendidos";
#  - un listado de Best Sellers (/gp/bestsellers/...): se recorren también sus
#    subcategorías. Solo se usa para la categoría general, porque el menú lateral
#    lleva a categorías vecinas (de TV a "pantallas CarPlay", por ejemplo).
_S = "https://www.amazon.es/s?i=electronics&s=exact-aware-popularity-rank&k="
CATEGORIAS: dict[str, str] = {
    "electronica_hogar": "https://www.amazon.es/gp/bestsellers/electronics",       # TV, Home Cinema y Hogar
    "televisores": _S + "televisores",                                             # Smart TV, OLED, QLED...
    "telefonia_movil": _S + "smartphone+libre",                                    # Smartphones y Smartwatches
    "audio_y_video": _S + "auriculares+inalambricos",                              # Auriculares y Altavoces
    "computacion_y_portatiles": "https://www.amazon.es/s?i=computers&s=exact-aware-popularity-rank&k=portatil",
    "fotografia_y_video": _S + "camara+fotos",                                     # Cámaras y Vídeo profesional
}
# Más búsquedas por categoría, para pasar de las ~300 que da una sola
BUSQUEDAS_EXTRA: dict[str, list[str]] = {
    "televisores": [_S + q for q in (
        "smart+tv+55+pulgadas", "smart+tv+65+pulgadas", "televisor+oled", "televisor+qled",
        "smart+tv+43+pulgadas", "televisor+75+pulgadas", "smart+tv+50+pulgadas")],
    "telefonia_movil": [_S + q for q in (
        "iphone", "samsung+galaxy", "xiaomi+smartphone", "smartwatch", "google+pixel", "movil+5g")],
    "audio_y_video": [_S + q for q in (
        "altavoz+bluetooth", "barra+de+sonido", "auriculares+cancelacion+ruido", "home+cinema",
        "auriculares+gaming")],
    "computacion_y_portatiles": ["https://www.amazon.es/s?i=computers&s=exact-aware-popularity-rank&k=" + q
                                 for q in ("tablet", "portatil+gaming", "macbook", "ipad", "monitor",
                                           "ordenador+sobremesa")],
    "fotografia_y_video": [_S + q for q in (
        "camara+reflex", "camara+mirrorless", "objetivo+camara", "camara+deportiva", "dron+camara",
        "gimbal")],
}
# Un producto solo entra en la categoría si su título contiene alguna de estas
# palabras (sin tildes ni mayúsculas). Vacío o ausente = no se comprueba.
PALABRAS_CATEGORIA: dict[str, list[str]] = {
    "televisores": ["televisor", "televisores", "tv", "television", "smart tv", "oled", "qled"],
    "telefonia_movil": ["smartphone", "movil", "telefono", "iphone", "galaxy", "pixel", "xiaomi",
                        "redmi", "poco", "oneplus", "motorola", "oppo", "realme", "honor",
                        "smartwatch", "reloj inteligente", "watch"],
    "audio_y_video": ["auriculares", "auricular", "cascos", "headphones", "earbuds", "airpods",
                      "altavoz", "altavoces", "speaker", "barra de sonido", "soundbar", "home cinema",
                      "subwoofer", "receptor av", "amplificador"],
    "computacion_y_portatiles": ["portatil", "laptop", "notebook", "ultrabook", "chromebook", "macbook",
                                 "tablet", "ipad", "ordenador", "pc", "monitor", "mac mini", "imac"],
    "fotografia_y_video": ["camara", "objetivo", "reflex", "mirrorless", "gopro", "dji", "videocamara",
                           "dron", "gimbal", "estabilizador", "lente"],
}
# Palabras que sacan un producto de una categoría concreta (además de la lista
# negra general): accesorios que se cuelan en la búsqueda
EXCLUIR_CATEGORIA: dict[str, list[str]] = {
    "televisores": ["soporte", "mando", "stick", "cube", "antena", "receptor", "decodificador",
                    "barra", "proyector", "carplay", "android auto", "mueble", "monitor", "coche"],
    "telefonia_movil": ["soporte", "tripode", "gimbal", "correa", "auriculares", "altavoz"],
    "audio_y_video": ["soporte", "almohadillas", "estuche"],
    "computacion_y_portatiles": ["soporte", "mochila", "maletin", "teclado", "raton", "base refrigeradora",
                                 "docking", "hub"],
    "fotografia_y_video": ["soporte", "tripode", "mochila", "bolsa", "filtro", "tarjeta", "bateria"],
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
MAX_SEARCH_PAGES = 20                            # páginas de resultados por búsqueda (Amazon suele dar 7-20)


# Navegador que usa Playwright, en orden de preferencia. "msedge" y "chrome" son
# los que ya tiene instalados el PC (Edge viene con Windows), así el .exe no
# necesita descargar ningún navegador. None = Chromium propio de Playwright.
BROWSER_CHANNELS = ["msedge", "chrome", None]
HEADLESS = True              # False para ver el navegador mientras trabaja
DELAY_RANGE = (5.0, 10.0)    # espera aleatoria entre páginas (segundos)
# Si sale captcha, abre una ventana visible del navegador para que lo resuelvas
# tú; al resolverlo sigue solo, con las mismas cookies. False = esperar y reintentar.
MANUAL_CAPTCHA = False      # True = abrir una ventana para resolverlo a mano
MANUAL_CAPTCHA_MINUTES = 15  # tiempo máximo esperando a que lo resuelvas
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
