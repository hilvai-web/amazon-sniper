# 🎯 Amazon Sniper

Aplicación de escritorio para Windows que vigila miles de ASINs en Amazon España y Europa (ES, DE, FR, IT, NL, BE), detecta posibles **errores de precio** y avisa por **Telegram**. Es gratuita, no usa APIs de pago y se compila en un único `.exe` portable.

## Estructura

```
AmazonSniper/
├── main.py              # punto de entrada (lo que compila PyInstaller)
├── sniper/
│   ├── config.py        # ajustes persistentes (config.json junto al .exe)
│   ├── db.py            # SQLite alertas.db: histórico de precios y antiduplicados
│   ├── scraper.py       # descarga + parser tolerante de fichas de Amazon, rotación UA/proxy
│   ├── engine.py        # motor asíncrono (aiohttp) en hilo propio, backoff, reintentos
│   ├── telegram.py      # alertas vía Bot API
│   └── gui.py           # interfaz oscura CustomTkinter
├── extractor_amazon.py  # extractor del Top 1000 de Best Sellers por categoría (Playwright)
├── extractor/           # config (PROXIES, retrasos...), navegador, recorrido, guardado CSV
├── instalar_extractor.ps1  # instala y lanza el extractor desde PowerShell
├── tests/test_core.py   # pruebas sin red (pytest)
├── actualizar.ps1       # instala/actualiza todo desde GitHub con un comando
├── build.bat            # compila el .exe con un doble clic
├── requirements.txt
├── asins_ejemplo.txt
└── proxies_ejemplo.txt
```

## Cómo detecta un error de precio

Para cada ASIN y marketplace marcado descarga la ficha `amazon.xx/dp/ASIN` y calcula el descuento contra la mejor referencia disponible:

1. **PVP tachado** que muestra la propia Amazon (precio recomendado / precio anterior).
2. **Histórico local**: la mediana de los precios que la app ha ido guardando en `alertas.db` durante los últimos 90 días (necesita al menos 3 lecturas). Cuanto más tiempo la uses, mejor detecta caídas reales, porque es tu propio "Keepa" local.

Hay alerta cuando el descuento ≥ *Descuento mínimo*, el precio ≤ *Precio máximo* (0 = sin límite) y el producto está disponible. La base de datos evita duplicados: el mismo producto no vuelve a avisar en 24 h salvo que baje todavía más.

## Instalar o actualizar (Windows, PowerShell)

Con Python 3.12 instalado, un solo comando instala o actualiza la app en `C:\Users\<tu usuario>\AmazonSniper` y el extractor en `C:\Users\<tu usuario>\AmazonExtractor`. Tu configuración, tu historial y tus CSV se conservan:

```powershell
irm https://raw.githubusercontent.com/hilvai-web/amazon-sniper/main/actualizar.ps1 | iex
```

## Uso

1. Ejecuta `AmazonSniper.exe` (o `python main.py` en desarrollo).
2. **Elige la categoría** en el desplegable (Electrónica, TV y hogar · Smartphones · Auriculares y altavoces · Portátiles y tablets · Cámaras y vídeo). Al pulsar **INICIAR SNIPER** la app busca sola los 1000 productos más vendidos de esa categoría (si su lista tiene más de 24 h) y empieza a vigilarlos; no hace falta cargar ningún archivo. Si quieres usar tu propia lista, *Cargar otro archivo* sigue disponible.
3. Pon el **token del bot** (créalo con @BotFather) y tu **Chat ID** (te lo da @userinfobot). Pulsa *Probar Telegram*.
4. Ajusta descuento, precio máximo y marketplaces, y pulsa **INICIAR SNIPER**.
5. Doble clic en una fila abre el producto. *Solo errores* filtra la tabla.

`alertas.db`, `config.json` se crean en la misma carpeta que el `.exe`.

## Compilar el .exe (en Windows, con Python 3.10–3.12 instalado)

Doble clic en `build.bat`, o a mano:

```bat
pip install -r requirements.txt
pyinstaller --noconfirm --clean --onefile --noconsole --name AmazonSniper ^
  --collect-all customtkinter ^
  --collect-data certifi ^
  --hidden-import aiohttp --hidden-import lxml --hidden-import lxml.etree ^
  --hidden-import bs4 --hidden-import tkinter --hidden-import sqlite3 ^
  main.py
```

Resultado: `dist\AmazonSniper.exe`, que funciona en cualquier Windows 10/11 sin Python. Para añadir icono: `--icon icono.ico`. Si Windows Defender lo marca (pasa con muchos .exe de PyInstaller sin firmar), es un falso positivo habitual; firmarlo o compilar con `--onedir` lo reduce.

## Velocidad, bloqueos y límites (léelo)

- **Velocidad**: con los valores por defecto (4 peticiones simultáneas, 2–5 s de pausa) son unas 3.000 consultas por hora. Subir la concurrencia acelera, pero Amazon empieza a responder con captchas.
- **Resiliencia, no invisibilidad**: la app rota el User-Agent cada 10 productos, admite una lista de **tus propios proxies** (archivo `.txt`), detecta captchas/503 y aplica pausas exponenciales (30 s → 10 min) cambiando de identidad. Nada de esto garantiza que Amazon no limite tu IP; si ves muchos "bloqueado", baja la concurrencia o sube la pausa.
- **No incluye** listas automáticas de proxies públicos gratuitos ni suplantación de huella TLS. Los proxies públicos suelen ser lentos, estar ya vetados por Amazon y pueden leer o alterar tu tráfico; y la evasión activa de los sistemas antibots no es algo que vaya a construir. Tampoco consulta Keepa ni CamelCamelCamel: sus condiciones prohíben el scraping (Keepa tiene API oficial de pago); el histórico local cumple esa función.
- **Términos de servicio**: las Condiciones de Uso de Amazon no permiten la extracción automatizada. Úsala a ritmo moderado, para uso personal, y asume ese riesgo. Un "error de precio" no obliga al vendedor a servir el pedido; Amazon suele cancelar esas compras.

## Extractor del Top 1000 de Electrónica

Herramienta aparte que genera tu lista de ASINs. Abre Best Sellers > Electrónica en amazon.es con un Chromium real (Playwright). Como cada ranking solo enseña 100 productos, recorre después las subcategorías (Audio, Fotografía, Accesorios, Wearables...) en anchura, y luego sus hijas. Extrae los ASINs de la rejilla del ranking con `/dp/([A-Z0-9]{10})`, quita duplicados y para justo en 1000.

En PowerShell, dentro de la carpeta:

```powershell
powershell -ExecutionPolicy Bypass -File .\instalar_extractor.ps1          # instala y ejecuta (reanuda si se cortó)
powershell -ExecutionPolicy Bypass -File .\instalar_extractor.ps1 -Reset   # empieza de cero
```

La primera vez crea un entorno `.venv` y descarga Chromium (~150 MB). Sale `amazon_top1000_<categoria>.csv` con la columna `ASIN`, que se carga directamente en Amazon Sniper con *Cargar ASINs*.

La categoría se pasa como argumento. `CATEGORIAS` en `extractor/config.py` asigna a cada clave la URL de Best Sellers de amazon.es donde empieza:

```powershell
.\.venv\Scripts\python.exe extractor_amazon.py telefonia_movil      # -> amazon_top1000_telefonia_movil.csv
.\.venv\Scripts\python.exe extractor_amazon.py --listar             # categorías disponibles
```

Sin argumento, o con una categoría que no existe, muestra las válidas y usa `electronica_hogar`. Cada categoría tiene su propio CSV y su propio progreso guardado.

- **Orden**: primero el top 100 general, luego el top 100 de cada subcategoría. No es un ranking global 1–1000, porque Amazon no lo publica.
- **Guardado continuo**: el CSV se reescribe tras cada página y `extractor_estado_<categoria>.json` recuerda las categorías pendientes. Si sale un captcha, cambia de identidad (contexto nuevo, otro User-Agent y el siguiente proxy) y espera. Tras 4 páginas fallidas seguidas guarda y para; al relanzarlo continúa donde lo dejó.
- **Solo electrónica de valor**: descarta productos con precio menor que `MIN_PRICE` (100 € por defecto) y los que tienen en el nombre o en la URL alguna palabra de `EXCLUDE_KEYWORDS` (pilas, baterías, fundas, protectores, cables, HDMI, cargadores, regletas, adaptadores...). Las subcategorías con esos nombres no se visitan. Solo se mira el nombre (el título hasta la primera coma), así unos auriculares con "30 h de batería" en la descripción no se descartan. Amazon Sniper aplica el mismo filtro (campos *Valor mínimo* y *Excluir si el título contiene*) y no vuelve a pedir en 7 días los productos descartados.
- **Proxies**: rellena `PROXIES` en `extractor/config.py` con los datos de tu proveedor residencial. Vacío = tu conexión. Con las pausas de 2–5 s, 1000 ASINs son unas 20–25 páginas, que llevan unos 3–5 minutos.

## Desarrollo

```bash
pip install -r requirements.txt pytest
python -m pytest tests
python main.py
```

Si Amazon cambia su HTML y deja de leer precios, los selectores están al principio de `sniper/scraper.py` (`PRICE_SELECTORS`, `LIST_PRICE_SELECTORS`).
