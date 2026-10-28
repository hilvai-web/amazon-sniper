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
├── tests/test_core.py   # pruebas sin red (pytest)
├── actualizar.ps1       # descarga el .exe de la última Release y crea accesos directos
├── .github/workflows/release.yml  # compila y publica el .exe
├── build.bat            # compila el .exe con un doble clic
├── requirements.txt
├── asins_ejemplo.txt
└── proxies_ejemplo.txt
```

## Cómo detecta un error de precio

Para cada ASIN y marketplace marcado descarga la ficha `amazon.xx/dp/ASIN` y calcula el descuento contra la mejor referencia disponible:

1. **PVP tachado** que muestra la propia Amazon (precio recomendado / precio anterior).
2. **Histórico local**: la mediana de los precios que la app ha ido guardando en `alertas.db` durante los últimos 90 días (necesita al menos 3 lecturas). Cuanto más tiempo la uses, mejor detecta caídas reales, porque es tu propio "Keepa" local.

Hay alerta cuando el descuento ≥ *Descuento mínimo*, el precio ≤ *Precio máximo* (0 = sin límite) y el producto está disponible. Con *Solo Prime* marcado (por defecto), además la oferta destacada tiene que ser Prime o enviada por Amazon; si la vende y envía otro vendedor, la fila sale como "no Prime" y no avisa. La base de datos evita duplicados: el mismo producto no vuelve a avisar en 24 h salvo que baje todavía más.

## Instalar en cualquier PC con Windows

Amazon Sniper es un único `AmazonSniper.exe` portable: no necesita Python ni instalar nada. Usa el Microsoft Edge que ya trae Windows para buscar las listas de productos.

**Opción A, un comando** (descarga el .exe en `C:\Users\<usuario>\AmazonSniper`, crea accesos directos y lo abre):

```powershell
irm https://raw.githubusercontent.com/hilvai-web/amazon-sniper/main/actualizar.ps1 | iex
```

**Opción B, a mano**: descarga `AmazonSniper.exe` desde [Releases](https://github.com/hilvai-web/amazon-sniper/releases/latest) y ponlo en cualquier carpeta (o un USB). Evita el Escritorio: la app guarda sus datos junto al .exe y Windows puede bloquear la escritura ahí.

Junto al .exe se crean `config.json` (ajustes), `alertas.db` (historial) y `listas/` (productos por categoría). Para llevarte la app con tus datos a otro PC, copia la carpeta entera.

## Actualizaciones

Al abrirse, la app consulta la última [Release](https://github.com/hilvai-web/amazon-sniper/releases/latest). Si hay una versión nueva aparece el botón verde **⬆ Actualizar a vX**: descarga el .exe nuevo, se cierra, se sustituye y vuelve a abrirse, sin tocar tus datos.

Para publicar una versión: sube el número `APP_VERSION` en `sniper/config.py` y haz push a `main`. GitHub Actions (`.github/workflows/release.yml`) pasa los tests, compila el .exe en Windows y crea la Release `vX.Y.Z`.

## Uso

1. Ejecuta `AmazonSniper.exe` (o `python main.py` en desarrollo).
2. **Elige la categoría** en el desplegable (Electrónica, TV y hogar · Smartphones · Auriculares y altavoces · Portátiles y tablets · Cámaras y vídeo). Al pulsar **INICIAR SNIPER** la app busca sola los 1000 productos más vendidos de esa categoría (si su lista tiene más de 24 h) y empieza a vigilarlos; no hace falta cargar ningún archivo. Si quieres usar tu propia lista, *Cargar otro archivo* sigue disponible.
3. Pon el **token del bot** (créalo con @BotFather) y tu **Chat ID** (te lo da @userinfobot). Pulsa *Probar Telegram*.
4. Ajusta descuento, precio máximo y marketplaces, y pulsa **INICIAR SNIPER**.
5. Doble clic en una fila abre el producto. *Solo errores* filtra la tabla.

`alertas.db`, `config.json` se crean en la misma carpeta que el `.exe`.

## Compilar el .exe a mano

Normalmente lo compila GitHub Actions. En local (Windows, Python 3.12): doble clic en `build.bat`, o:

```bat
pip install -r requirements.txt
pyinstaller --noconfirm --clean --onefile --noconsole --name AmazonSniper ^
  --collect-all customtkinter --collect-data certifi ^
  --hidden-import lxml.etree --hidden-import playwright.async_api ^
  main.py
```

## Escaneo continuo

Con **🔁 Escaneo continuo** marcado (por defecto), al terminar una vuelta la app espera `cycle_pause_seconds` (60 s) y empieza otra. Cada vuelta revisa los productos que no se han mirado en la última hora (*No re-escanear antes de*; pon 0 para revisarlo todo siempre) y rehace la lista de la categoría si tiene más de 24 h. Si no queda nada pendiente, espera 10 minutos antes de volver a mirar. DETENER para el bucle.

## Segundo plano (sin ventana)

`AmazonSniper.exe --segundo-plano` escanea en bucle sin abrir ninguna ventana, con los ajustes guardados desde la app, y apunta todo en `ejecucion.log` junto al .exe (rota a los 2 MB, guarda 3 copias). `--una-vez` hace una sola vuelta y termina. Si ya hay una copia oculta funcionando, la nueva se cierra sola.

Desde la app, sección *Segundo plano*: **Arrancar oculto al iniciar Windows** crea la tarea «Amazon Sniper (segundo plano)» en el Programador de tareas (o, si Windows no deja, un acceso directo en la carpeta Inicio); **Arrancar oculto ahora**; **Parar el oculto y quitarlo del arranque**; **Ver ejecucion.log**.

Autorregulación: si una ficha tarda más de 6 s, falla la red o Amazon responde 503/429 sin captcha, la pausa entre fichas se multiplica por 1,5 (hasta x6) y cada ficha rápida la va relajando hasta la normal. Solo un captcha de verdad para el escaneo: avisa por Telegram y reintenta solo a los 10 s y luego cada minuto.

## Proxies

Opcionales: un `.txt` con un proxy HTTP por línea (`IP:puerto`, `http://usuario:clave@host:puerto`; también vale `https://` o `IP puerto`). Al empezar cada vuelta la app prueba todos contra amazon.es y descarta los que no responden, tardan más de `proxy_max_seconds` (8 s) o reciben captcha/error. Durante el escaneo, un proxy que Amazon bloquea (captcha, 403, 503) sale al momento, y uno que falla o va lento dos veces, también. Si no queda ninguno, sigue con la conexión directa.

## API de Amazon (opcional)

Si tienes acceso a la **Creators API** de Amazon Afiliados (Associates Central > Herramientas > Creators API), rellena en la app *ID de credencial* (`amzn1.application-oa2-client…`), *Secret* (`amzn1.oa2-cs.v1…`) y tu *ID de afiliado* de amazon.es (`…-21`), y pulsa *Probar API*. Se guardan solo en `config.json` de tu PC.

Con la API activada, la API y la web se reparten la cola de amazon.es: la API consulta 10 productos por petición (una por segundo, `api_interval`) y la web sigue con el resto. Lo que la API no devuelve, o si falla, lo mira la web. La API no dice si una oferta es Prime: si la vende Amazon cuenta como Prime; si es de otro vendedor y parece un error de precio, la app lo confirma en la web antes de avisar. Si Amazon rechaza las credenciales (por ejemplo, porque la cuenta aún no tiene ventas suficientes para la API), se desactiva en esa vuelta y sigue solo la web.

## Velocidad, bloqueos y límites (léelo)

- **Velocidad**: con los valores por defecto (2 peticiones simultáneas, 5–10 s de pausa) son unas 800 consultas por hora. Subir la concurrencia acelera, pero Amazon empieza a responder con captchas.
- **Captcha**: con *Si sale captcha, parar y avisarme* (por defecto) el escaneo se para, suena un aviso, se abre el producto en tu navegador y aparece el botón amarillo *Ya lo he resuelto: continuar*; el producto se reintenta y sigue el bloque. Si tienes Telegram configurado, además te manda la imagen del captcha al bot: contesta con las letras (o «ok» si solo pide confirmar) y la app las envía a Amazon con la misma sesión del escaneo y sigue sola; si no valen, te manda otra (hasta 5). Si no respondes, reintenta solo con otra identidad a los 10 s y luego cada minuto (campo *Captcha sin responder: reintentar a los - luego cada*; 0 = esperar siempre a ti). El extractor de listas abre su propia ventana del navegador con el captcha y, en cuanto lo resuelves, continúa solo con esas cookies.
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

La categoría se pasa como argumento. `CATEGORIAS` en `extractor/config.py` asigna a cada clave la URL donde empieza. Las categorías concretas (Televisores, Smartphones, Auriculares, Portátiles, Cámaras) usan búsquedas de amazon.es dentro de Electrónica ordenadas por más vendidos (`/s?k=televisores&i=electronics`), más las de `BUSQUEDAS_EXTRA`, y se saltan los anuncios patrocinados. Un producto solo entra si su título tiene alguna palabra de `PALABRAS_CATEGORIA` (p. ej. "televisor", "TV", "OLED") y ninguna de `EXCLUIR_CATEGORIA` (soportes, mandos, CarPlay...). Solo "Electrónica, TV y hogar" recorre el árbol de Best Sellers, porque desde una subcategoría el menú lateral lleva a categorías vecinas:

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
