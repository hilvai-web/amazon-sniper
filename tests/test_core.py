"""Pruebas sin red del parser, la BD y el motor (python -m pytest tests)."""
import queue
import tempfile
from pathlib import Path

from sniper.config import Settings
from sniper.db import Database
from sniper.engine import ScanEngine, evaluate
from sniper.scraper import load_asins, parse_price, parse_product

PAGE = """<html><body><span id="productTitle"> Monitor Gaming 27" </span>
<div id="corePriceDisplay_desktop_feature_div">
 <span class="a-price aok-align-center"><span class="a-offscreen">{price}</span></span>
 <span class="basisPrice">PVPR: <span class="a-price a-text-price"><span class="a-offscreen">{lp}</span></span></span>
</div><div id="availability">En stock</div>
<div id="merchant-info">Vendido y enviado por Amazon.</div></body></html>"""
CAPTCHA = '<form action="/errors/validateCaptcha"><input id="captchacharacters"></form>'


def test_parse_price():
    assert parse_price("1.234,56 €") == 1234.56
    assert parse_price("€1,234.56") == 1234.56
    assert parse_price("19,99 €") == 19.99
    assert parse_price("1.299 €") == 1299
    assert parse_price("") is None


def test_parse_product_and_captcha():
    info = parse_product(PAGE.format(price="49,99 €", lp="399,00 €"), "B000000001", "ES", "u")
    assert info.title == 'Monitor Gaming 27"' and info.price == 49.99 and info.list_price == 399
    assert evaluate(info, None)[0] > 87
    assert parse_product(CAPTCHA, "B000000001", "ES", "u").status == "captcha"


def test_load_asins(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("B0ABCDEFGH\nhttps://www.amazon.es/dp/B0ZZZZZZZZ?x=1\nb0abcdefgh\nbasura\n")
    assert load_asins(f) == ["B0ABCDEFGH", "B0ZZZZZZZZ"]


def test_engine_end_to_end(tmp_path):
    calls = {"n": 0}

    async def fake_fetch(session, url, headers, proxy, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return 200, CAPTCHA            # primer intento bloqueado -> reintento
        if "B0DEAL0001" in url:
            return 200, PAGE.format(price="9,99 €", lp="199,00 €")
        if "B0MISSING1" in url:
            return 404, ""
        return 200, PAGE.format(price="180,00 €", lp="199,00 €")

    db = Database(tmp_path / "t.db")
    events = queue.Queue()
    s = Settings(min_discount=50, max_price=100, markets=["ES"], concurrency=2,
                 delay_min=0, delay_max=0, rescan_hours=0, captcha_pause=False)
    eng = ScanEngine(s, ["B0DEAL0001", "B0NORMAL01", "B0MISSING1"], db, events, fetcher=fake_fetch)
    eng._on_block = lambda reason: None     # sin pausa de 30 s en la prueba
    eng.start(); eng._thread.join(20)
    evs = []
    while not events.empty():
        evs.append(events.get())
    rows = {e[1]["asin"]: e[1] for e in evs if e[0] == "row"}
    assert rows["B0DEAL0001"]["status"] == "🚨 ERROR"
    assert rows["B0NORMAL01"]["status"] == "ok"
    assert rows["B0MISSING1"]["status"] == "no existe"
    assert evs[-1] == ("finished", "Escaneo completado")
    # segunda pasada: misma oferta no se vuelve a alertar
    eng2 = ScanEngine(s, ["B0DEAL0001"], db, queue.Queue(), fetcher=fake_fetch)
    eng2.start(); eng2._thread.join(20)
    assert eng2.alerts == 0
    assert db.stats()["alerts"] == 1


def test_extractor_helpers(tmp_path):
    from extractor.crawler import extract_asins, extract_subcategories
    from extractor.storage import AsinStore
    items = [
        {"href": "https://www.amazon.es/Marca/dp/B0ABCDEFGH/ref=zg_bs", "text": "Portátil 15\n1.299,00 €"},
        {"href": "https://www.amazon.es/dp/B0ABCDEFGH", "text": "Portátil 15\n1.299,00 €"},
        {"href": "https://www.amazon.es/dp/B0CHEAP001", "text": "Auriculares\n19,99 €"},
        {"href": "https://www.amazon.es/dp/B0PILAS001", "text": "Duracell Pilas AA pack 200\n149,00 €"},
        {"href": "https://www.amazon.es/dp/B0NOPRICE1", "text": "Agotado"},
        {"href": "https://www.amazon.es/dp/8408123456?th=1", "text": "Cámara réflex\n2 ofertas desde 450,50 €"},
        {"href": "https://www.amazon.es/Belkin-Regleta-proteccion-sobretensiones/dp/B0REGLETA1", "text": "Belkin 8 tomas\n129,00 €"},
        {"href": "https://www.amazon.es/dp/B0AURIS001", "text": "1.\nSony WH-1000XM5 Auriculares inalámbricos, 30 h de batería, cable incluido\n299,00 €"},
    ]
    assert extract_asins(items) == (["B0ABCDEFGH", "8408123456", "B0AURIS001"], 4)
    root = "https://www.amazon.es/gp/bestsellers/electronics"
    cats = extract_subcategories([
        {"href": root + "/934197031/ref=zg", "text": "Audio y Hi-Fi"},
        {"href": root + "/111111111/ref=zg", "text": "Pilas y cargadores"},
    ], root, "/gp/bestsellers/electronics")
    assert cats == [root + "/934197031"]
    store = AsinStore(tmp_path / "o.csv", limit=2)
    assert store.add_many(["A000000001", "A000000001", "A000000002", "A000000003"]) == 2
    store.save()
    again = AsinStore(tmp_path / "o.csv", limit=2); again.load()
    assert len(again) == 2 and again.full


def test_low_value_filter(tmp_path):
    from sniper.engine import keyword_matcher, low_value_reason
    from sniper.scraper import ProductInfo
    match = keyword_matcher(Settings().exclude_keywords)
    cheap = ProductInfo("B0X", "ES", "u", title="Ratón inalámbrico", price=15.0, list_price=25.0)
    pilas = ProductInfo("B0Y", "ES", "u", title="Pack 40 Pilas AA Alcalinas", price=300.0)
    error = ProductInfo("B0Z", "ES", "u", title="Televisor OLED 65", price=9.99, list_price=1999.0)
    unknown = ProductInfo("B0W", "ES", "u", title="Portátil", price=None)
    assert low_value_reason(cheap, None, 100, match) == "excluido: barato"
    assert low_value_reason(pilas, None, 100, match) == "excluido: consumible"
    assert low_value_reason(error, None, 100, match) is None
    assert low_value_reason(cheap, 180.0, 100, match) is None   # el histórico dice que era caro
    assert low_value_reason(unknown, None, 100, match) is None
    assert not match("Batidora")  # no confunde 'bateria' con palabras parecidas
    assert not match("Sony WH-1000XM5 Auriculares inalámbricos con cancelación de ruido, 30 h de batería")
    assert match("Amazon Basics - Pack de 48 pilas alcalinas AA, 1,5 V")
    assert match("Anker Cargador USB C 65W")


def test_category_argument(capsys):
    import extractor_amazon
    from extractor import config
    assert extractor_amazon.resolve_category("telefonia_movil") == "telefonia_movil"
    assert extractor_amazon.resolve_category("Telefonia_Movil") == "telefonia_movil"
    assert extractor_amazon.resolve_category("no_existe") == "electronica_hogar"
    assert "telefonia_movil" in capsys.readouterr().out
    assert extractor_amazon.resolve_category(None) == "electronica_hogar"
    assert str(config.output_csv("telefonia_movil")) == "amazon_top1000_telefonia_movil.csv"


def test_load_proxies(tmp_path):
    from sniper.scraper import load_proxies
    f = tmp_path / "p.txt"
    f.write_text("# lista\nhttp://47.91.65.23:3128\nhttps://198.199.86.11:8080\n10.0.0.1\t8080\n"
                 "socks5://1.1.1.1:1080\nhttp://u:p@h.com:3128\n\nhttp://47.91.65.23:3128\n")
    assert load_proxies(f) == ["http://47.91.65.23:3128", "http://198.199.86.11:8080",
                               "http://10.0.0.1:8080", "http://u:p@h.com:3128"]
    f.write_text("1.2.3.4:80\n", encoding="utf-16")
    assert load_proxies(f) == ["http://1.2.3.4:80"]


def test_engine_drops_bad_proxies(tmp_path):
    """Proxies caídos o bloqueados se descartan en la prueba inicial y durante el escaneo."""
    import asyncio as aio

    async def fetch(session, url, headers, proxy, timeout):
        if proxy == "http://dead:1":
            raise aio.TimeoutError()
        if proxy == "http://captcha:1":
            return 200, CAPTCHA
        if proxy == "http://later:1" and "/dp/" in url:
            return 503, ""
        return 200, PAGE.format(price="180,00 €", lp="199,00 €")

    db = Database(tmp_path / "t.db")
    events = queue.Queue()
    s = Settings(markets=["ES"], concurrency=1, delay_min=0, delay_max=0, rescan_hours=0, batch_size=1)
    eng = ScanEngine(s, ["B0NORMAL01", "B0NORMAL02", "B0NORMAL03"], db, events,
                     proxies=["http://dead:1", "http://captcha:1", "http://good:1", "http://later:1"],
                     fetcher=fetch)
    eng._on_block = lambda reason: None
    eng.start(); eng._thread.join(20)
    logs = [e[1] for e in list(events.queue) if e[0] == "log"]
    rows = [e[1] for e in list(events.queue) if e[0] == "row"]
    assert any("Proxies útiles: 2 de 4" in l for l in logs)
    assert all(r["status"] == "ok" for r in rows) and len(rows) == 3
    assert eng.identities.proxies_alive == 1          # 'later' cayó al recibir 503


def test_updater_clean_env(monkeypatch):
    from sniper import updater
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "C:/Temp/_MEI123")
    monkeypatch.setenv("_MEIPASS2", "C:/Temp/_MEI123")
    env = updater.clean_env()
    assert "_PYI_APPLICATION_HOME_DIR" not in env and "_MEIPASS2" not in env
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_category_filter_and_search_urls():
    from extractor import config
    from extractor.crawler import CategoryFilter, extract_asins, is_search, search_page_number, search_page_url
    tv = CategoryFilter("televisores")
    assert tv.accepts("Samsung TV QLED 55\" Q60D 2024, Smart TV 4K")
    assert tv.accepts("LG 65UT73006LA - Televisor 65 pulgadas 4K UHD")
    assert not tv.accepts("Pantalla CarPlay inalámbrica 9 pulgadas para coche, Android Auto")
    assert not tv.accepts("Soporte TV pared para 32-75 pulgadas")
    assert not tv.accepts("Apple iPhone 15 128 GB")
    assert CategoryFilter("electronica_hogar").accepts("Pantalla CarPlay para coche")
    items = [
        {"href": "https://www.amazon.es/dp/B0TV000001", "title": "Samsung Smart TV 55 Crystal UHD", "text": "449,00 €\n..."},
        {"href": "https://www.amazon.es/dp/B0CARPLAY1", "title": "Pantalla CarPlay inalámbrica 10,26\"", "text": "129,99 €"},
    ]
    assert extract_asins(items, tv) == (["B0TV000001"], 1)
    url = ("https://www.amazon.es/s?k=televisores&__mk_es_ES=%C3%85M%C3%85&crid=8J18YELDO481"
           "&sprefix=televisore%2Caps%2C346&ref=nb_sb_noss_2")
    assert is_search(url) and not is_search(config.CATEGORIAS["electronica_hogar"])
    assert search_page_url(url, 1) == "https://www.amazon.es/s?k=televisores"
    p3 = search_page_url(config.CATEGORIAS["televisores"], 3)
    assert search_page_number(p3) == 3 and "i=electronics" in p3
    for key in config.CATEGORIAS:
        assert key in config.NOMBRES


def test_crawl_search_pages(tmp_path, monkeypatch):
    import asyncio
    from extractor import config, crawler
    from extractor.storage import AsinStore
    monkeypatch.setattr(config, "DELAY_RANGE", (0, 0))
    pages = {}

    async def fake_fetch(session, url):
        pages[url] = pages.get(url, 0) + 1
        n = crawler.search_page_number(url)
        q = dict(crawler.parse_qsl(crawler.urlsplit(url).query))["k"]
        items = [{"href": f"https://www.amazon.es/dp/{q[:4].upper():<4}{n:03d}{i:03d}".replace(" ", "X"),
                  "title": f"Smart TV {q} {i}", "text": "499,00 €"} for i in range(2)]
        items.append({"href": "https://www.amazon.es/dp/B0CARPLAY1", "title": "Pantalla CarPlay coche",
                      "text": "150,00 €"})
        return items, [], n < 3
    monkeypatch.setattr(crawler, "fetch_with_retries", fake_fetch)
    store = AsinStore(tmp_path / "o.csv", limit=100)
    queue, visited = [], set()
    urls = [config.CATEGORIAS["televisores"], config.BUSQUEDAS_EXTRA["televisores"][0]]
    asyncio.run(crawler.crawl(None, store, queue, visited, urls, tmp_path / "st.json",
                              crawler.CategoryFilter("televisores")))
    assert len(store) == 12 and not queue          # 2 búsquedas x 3 páginas x 2 TV
    assert "B0CARPLAY1" not in store._asins
    assert all(v == 1 for v in pages.values()) and len(pages) == 6


def test_price_fallbacks():
    t = '<span id="productTitle">Samsung TV 55</span>'
    cases = {
        1299.0: t + '<div id="corePriceDisplay_desktop_feature_div"><span class="a-price priceToPay">'
                    '<span class="a-offscreen"> </span><span aria-hidden="true"><span class="a-price-whole">'
                    '1.299<span>,</span></span><span class="a-price-fraction">00</span><span>€</span></span></span></div>',
        449.0: t + '<input type="hidden" name="items[0.base][customerVisiblePrice][amount]" value="449.0">',
        529.0: t + '<script>{"displayPrice":"529,00 €","priceAmount":529.00}</script>',
        389.9: t + '<div id="buybox-see-all-buying-choices"><a>Ver todas las opciones</a> desde 389,90&nbsp;€</div>',
    }
    for price, html in cases.items():
        info = parse_product(html, "B0X", "ES", "u")
        assert (info.price, info.status) == (price, "ok")


def test_prime_detection():
    head = '<span id="productTitle">TV</span><div id="corePrice_feature_div"><span class="a-price">' \
           '<span class="a-offscreen">499,00 €</span></span></div>'
    def prime(extra):
        return parse_product(head + extra, "B0X", "ES", "u").prime
    assert prime('<div id="merchant-info">Vendido y enviado por Amazon.</div>')
    assert prime('<div id="tabular-buybox">Enviado desde Amazon Vendido por TiendaX</div>')
    assert prime('<div id="desktop_buybox"><i class="a-icon a-icon-prime" aria-label="Amazon Prime"></i></div>')
    assert not prime('<div id="merchant-info">Vendido y enviado por TiendaX.</div>')
    assert not prime('')
    only_others = parse_product('<span id="productTitle">TV</span><div id="buybox-see-all-buying-choices">'
                                'desde 389,90 € <i class="a-icon-prime"></i></div>', "B0X", "ES", "u")
    assert only_others.price == 389.9 and not only_others.prime


def test_api_parse_item():
    from sniper.amazon_api import parse_item
    item = {"asin": "B0API00001", "itemInfo": {"title": {"displayValue": "Samsung Smart TV 55"}},
            "offersV2": {"listings": [
                {"condition": {"value": "Used"}, "price": {"money": {"amount": 100.0}}},
                {"condition": {"value": "New"}, "isBuyBoxWinner": True,
                 "availability": {"type": "IN_STOCK"}, "merchantInfo": {"name": "Amazon.es"},
                 "price": {"money": {"amount": 449.0},
                           "savingBasis": {"money": {"amount": 699.0}, "savingBasisType": "LIST_PRICE"}}}]}}
    info = parse_item(item, "ES")
    assert (info.price, info.list_price, info.prime, info.available, info.source) == (449.0, 699.0, True, True, "api")
    other = parse_item({"ASIN": "B0API00002", "OffersV2": {"Listings": [
        {"IsBuyBoxWinner": True, "MerchantInfo": {"Name": "TiendaX"},
         "Availability": {"Type": "OUT_OF_STOCK"}, "Price": {"Money": {"Amount": 50}}}]}}, "ES")
    assert (other.asin, other.price, other.prime, other.available) == ("B0API00002", 50.0, False, False)
    assert parse_item({"asin": "B0API00003"}, "ES").status == "no_price"


def test_engine_shares_queue_with_api(tmp_path):
    from sniper.amazon_api import parse_item

    def listing(price, lp, seller):
        return {"isBuyBoxWinner": True, "merchantInfo": {"name": seller},
                "price": {"money": {"amount": price}, "savingBasis": {"money": {"amount": lp}}}}

    api_items = {
        "B0APIAMZ01": listing(180.0, 199.0, "Amazon.es"),     # normal, vendido por Amazon
        "B0APIDEAL1": listing(15.0, 199.0, "Amazon.es"),      # error, Prime seguro -> alerta directa
        "B0APIEXT01": listing(20.0, 199.0, "TiendaX"),        # posible error de otro vendedor -> web
    }
    web_urls = []

    class FakeApi:
        async def get_items(self, session, asins, market):
            return {a: parse_item({"asin": a, "offersV2": {"listings": [api_items[a]]}}, market)
                    for a in asins if a in api_items}

    async def fake_fetch(session, url, headers, proxy, timeout):
        import asyncio
        await asyncio.sleep(0.05)       # como la red real: deja turno a la API
        web_urls.append(url)
        return 200, PAGE.format(price="180,00 €", lp="199,00 €")

    s = Settings(min_discount=50, max_price=100, markets=["ES"], concurrency=1,
                 delay_min=0, delay_max=0, rescan_hours=0, api_interval=0)
    asins = list(api_items) + ["B0WEBONLY1"]
    eng = ScanEngine(s, asins, Database(tmp_path / "t.db"), queue.Queue(), fetcher=fake_fetch)
    eng.api = FakeApi()
    eng.start(); eng._thread.join(20)
    rows = {}
    while not eng.events.empty():
        e = eng.events.get()
        if e[0] == "row":
            rows[e[1]["asin"]] = e[1]["status"]
    assert set(rows) == set(asins)
    # la web y la API se reparten la cola, así que depende de quién coge cada uno
    by_web = {a for a in asins if any(a in u for u in web_urls)}
    assert eng.api_done > 0
    assert rows["B0APIDEAL1"] == ("ok" if "B0APIDEAL1" in by_web else "🚨 ERROR")
    assert rows["B0APIAMZ01"] == "ok"
    assert rows["B0APIEXT01"] == "ok" and "B0APIEXT01" in by_web   # sin Prime claro: lo confirma la web
    assert "B0WEBONLY1" in by_web


AOD = """<div id="aod-offer-list">
<div id="aod-offer"><div id="aod-offer-heading"><span>Usado - Como nuevo</span></div>
 <span class="a-price"><span class="a-offscreen">90,00 €</span></span></div>
<div id="aod-offer"><div id="aod-offer-heading"><span>Nuevo</span></div>
 <span class="a-price"><span class="a-offscreen">120,00 €</span></span>
 <div id="aod-offer-shipsFrom"><span>Envío desde</span><span>TiendaX</span></div>
 <div id="aod-offer-soldBy"><span>Vendido por</span><a>TiendaX</a></div></div>
<div id="aod-offer"><div id="aod-offer-heading"><span>Nuevo</span></div>
 <span class="a-price"><span class="a-offscreen">135,50 €</span></span>
 <div id="aod-offer-shipsFrom"><span>Envío desde</span><span>Amazon</span></div>
 <div id="aod-offer-soldBy"><span>Vendido por</span><a>OtraTienda</a></div></div>
</div>"""
NO_FEATURED = """<span id="productTitle">Samsung TV 27 F6005</span>
<div id="rightCol"><div id="buybox">No hay ofertas destacadas disponibles
<span id="buybox-see-all-buying-choices"><a class="a-button-text">Ver todas las opciones de compra</a></span>
</div></div>"""


def test_offers_panel():
    from sniper.scraper import parse_offers
    offers = parse_offers(AOD)
    assert [(o.price, o.prime) for o in offers] == [(120.0, False), (135.5, True)]
    info = parse_product(NO_FEATURED, "B0X", "ES", "u")
    assert info.price is None and info.other_offers
    assert not parse_product('<span id="productTitle">TV</span>', "B0X", "ES", "u").other_offers


def test_engine_reads_offers_panel(tmp_path):
    async def fetch(session, url, headers, proxy, timeout):
        if "/gp/product/ajax" in url:
            return 200, AOD if "B0OFFERS01" in url else "<div></div>"
        return 200, NO_FEATURED

    for only_prime, expected in ((True, 135.5), (False, 120.0)):
        s = Settings(markets=["ES"], concurrency=1, delay_min=0, delay_max=0, rescan_hours=0,
                     min_value=0, only_prime=only_prime)
        eng = ScanEngine(s, ["B0OFFERS01", "B0NOBODY01"], Database(tmp_path / f"{only_prime}.db"),
                         queue.Queue(), fetcher=fetch)
        eng.start(); eng._thread.join(20)
        rows = {}
        while not eng.events.empty():
            e = eng.events.get()
            if e[0] == "row":
                rows[e[1]["asin"]] = e[1]
        assert rows["B0OFFERS01"]["price"] == expected and rows["B0OFFERS01"]["status"] == "ok"
        assert rows["B0NOBODY01"]["status"] == "sin ofertas"


def test_prime_tabular_labels():
    head = '<span id="productTitle">TV</span><div id="corePrice_feature_div"><span class="a-price">' \
           '<span class="a-offscreen">197,99 €</span></span></div>'
    def prime(extra):
        return parse_product(head + extra, "B0X", "ES", "u").prime
    assert prime('<div id="tabular-buybox"><span>Envío desde</span> <span>Amazon</span>'
                 '<span>Vendido por</span> <span>Amazon</span></div>')
    assert prime('<div id="merchantInfoFeature_feature_div">Vendido por Amazon.es</div>')
    assert prime('<div id="offer-display-features">Versand Amazon Verkäufer Amazon</div>')
    assert not prime('<div id="tabular-buybox"><span>Envío desde</span> <span>TiendaX</span>'
                     '<span>Vendido por</span> <span>TiendaX</span></div>')


def test_captcha_pause_and_resume(tmp_path):
    import threading
    import time as _t
    calls = {"n": 0}

    async def fetch(session, url, headers, proxy, timeout):
        calls["n"] += 1
        return 200, CAPTCHA if calls["n"] == 1 else PAGE.format(price="180,00 €", lp="199,00 €")

    d = Settings()
    assert (d.concurrency, d.delay_min, d.delay_max, d.captcha_pause) == (2, 5.0, 10.0, True)
    s = Settings(markets=["ES"], delay_min=0, delay_max=0, rescan_hours=0)
    events = queue.Queue()
    eng = ScanEngine(s, ["B0CAPTCHA1", "B0NORMAL02"], Database(tmp_path / "t.db"), events, fetcher=fetch)
    eng.start()
    deadline = _t.time() + 5
    while not eng.waiting_captcha and _t.time() < deadline:
        _t.sleep(0.05)
    assert eng.waiting_captcha
    _t.sleep(0.5)
    assert eng.running                      # sigue esperando, sin terminar ni descartar nada
    eng.resume()
    eng._thread.join(20)
    evs = []
    while not events.empty():
        evs.append(events.get())
    assert any(e[0] == "captcha" and "B0CAPTCHA1" in e[1] for e in evs)
    rows = {e[1]["asin"]: e[1]["status"] for e in evs if e[0] == "row"}
    assert rows == {"B0CAPTCHA1": "ok", "B0NORMAL02": "ok"}


CAPTCHA_FORM = """<html><body><h4>Introduce los caracteres que aparecen a continuación</h4>
<form method="get" action="/errors/validateCaptcha">
<input type="hidden" name="amzn" value="tok123"><input type="hidden" name="amzn-r" value="&#047;dp&#047;B0CAPTCHA1">
<img src="https://images-na.ssl-images-amazon.com/captcha/abc/Captcha_xyz.jpg">
<input autocomplete="off" type="text" id="captchacharacters" name="field-keywords">
<button type="submit">Seguir comprando</button></form></body></html>"""


def test_captcha_form_parse():
    from sniper.scraper import parse_captcha_form
    form = parse_captcha_form(CAPTCHA_FORM, "https://www.amazon.es/dp/B0CAPTCHA1")
    assert form["action"] == "https://www.amazon.es/errors/validateCaptcha"
    assert form["fields"] == {"amzn": "tok123", "amzn-r": "/dp/B0CAPTCHA1"}
    assert form["text_field"] == "field-keywords" and form["image"].endswith("Captcha_xyz.jpg")


def test_captcha_solved_via_telegram(tmp_path, monkeypatch):
    import asyncio
    from sniper import engine as engmod
    sent, replies, submitted = [], ["xx", "abc def"], []
    state = {"solved": False, "blocked": 4}      # 3 "continuar" solos fallan; el 4.º va a Telegram

    async def fetch(session, url, headers, proxy, timeout):
        if "validateCaptcha" in url:
            submitted.append(url)
            if "field-keywords=ABCDEF" in url:
                state["solved"] = True
                return 200, PAGE.format(price="180,00 €", lp="199,00 €")
            return 200, CAPTCHA_FORM
        if state["blocked"]:
            state["blocked"] -= 1
            return 200, CAPTCHA_FORM
        assert state["solved"], "no debe seguir escaneando hasta resolver el captcha"
        return 200, PAGE.format(price="180,00 €", lp="199,00 €")

    async def send_photo(session, token, chat, photo, caption):
        sent.append(("photo", caption)); return True

    async def send_message(session, token, chat, text):
        sent.append(("text", text)); return True, "ok"

    async def next_offset(session, token):
        return 0

    async def get_updates(session, token, offset, wait=25):
        await asyncio.sleep(0.05)
        if replies and len([x for x in sent if x[0] == "photo"]) > len(submitted):
            return [{"update_id": offset, "message": {"chat": {"id": 42}, "text": replies.pop(0)}}]
        return []

    async def fetch_bytes(self, session, url, headers):
        return b"jpg"
    for name, fn in (("send_photo", send_photo), ("send_message", send_message),
                     ("next_offset", next_offset), ("get_updates", get_updates)):
        monkeypatch.setattr(engmod.telegram, name, fn)
    monkeypatch.setattr(engmod.ScanEngine, "_fetch_bytes", fetch_bytes)

    s = Settings(markets=["ES"], concurrency=1, delay_min=0, delay_max=0, rescan_hours=0,
                 telegram_token="T", telegram_chat_id="42", captcha_retry_first=0.1, captcha_retry_every=1.0,
                 captcha_telegram=True)
    events = queue.Queue()
    eng = ScanEngine(s, ["B0CAPTCHA1", "B0NORMAL02"], Database(tmp_path / "t.db"), events, fetcher=fetch)
    eng.start(); eng._thread.join(20)
    evs = []
    while not events.empty():
        evs.append(events.get())
    rows = {e[1]["asin"]: e[1]["status"] for e in evs if e[0] == "row"}
    assert rows == {"B0CAPTCHA1": "ok", "B0NORMAL02": "ok"}
    assert ("captcha_solved",) in evs
    quiet = [e[2] for e in evs if e[0] == "captcha"]
    assert quiet == [True, True, True, False]       # solo el 4.º avisa
    assert len(submitted) == 2 and "amzn=tok123" in submitted[0]
    assert [k for k, _ in sent] == ["photo", "photo", "text"] and "Sigo escaneando" in sent[-1][1]


def test_captcha_auto_resume(tmp_path):
    calls = {"n": 0}

    async def fetch(session, url, headers, proxy, timeout):
        calls["n"] += 1
        return 200, CAPTCHA if calls["n"] <= 2 else PAGE.format(price="180,00 €", lp="199,00 €")

    s = Settings(markets=["ES"], concurrency=1, delay_min=0, delay_max=0, rescan_hours=0,
                 captcha_retry_first=0.3, captcha_retry_every=0.6)
    events = queue.Queue()
    eng = ScanEngine(s, ["B0CAPTCHA1"], Database(tmp_path / "t.db"), events, fetcher=fetch)
    eng.start(); eng._thread.join(20)
    evs = []
    while not events.empty():
        evs.append(events.get())
    assert [e[0] for e in evs].count("captcha") == 2
    assert {e[1]["asin"]: e[1]["status"] for e in evs if e[0] == "row"} == {"B0CAPTCHA1": "ok"}
    assert any("Reintentando solo" in e[1] for e in evs if e[0] == "log")
    assert Settings().captcha_retry_first == 10 and Settings().captcha_retry_every == 60
    assert eng._captcha_streak == 0


def test_adaptive_pace(tmp_path):
    calls = {"n": 0}

    async def fetch(session, url, headers, proxy, timeout):
        calls["n"] += 1
        if calls["n"] <= 2:
            return 503, "<html>Disculpa las molestias</html>"   # frena, sin captcha
        return 200, PAGE.format(price="180,00 €", lp="199,00 €")

    s = Settings(markets=["ES"], concurrency=1, delay_min=0, delay_max=0, rescan_hours=0)
    events = queue.Queue()
    eng = ScanEngine(s, ["B0SLOW0001", "B0SLOW0002"], Database(tmp_path / "t.db"), events, fetcher=fetch)
    eng._on_block = lambda reason: None
    eng.start(); eng._thread.join(20)
    evs = []
    while not events.empty():
        evs.append(events.get())
    assert not any(e[0] == "captcha" for e in evs)          # un 503 no pide intervención
    assert any("Amazon va lento" in e[1] for e in evs if e[0] == "log")
    assert 1.0 < eng.pace < 2.25                             # subió x1,5 dos veces y se relaja
    assert {e[1]["status"] for e in evs if e[0] == "row"} == {"ok"}


def test_background_once(tmp_path, monkeypatch):
    from sniper import background, engine as engmod

    async def fetch(session, url, headers, proxy, timeout):
        return 200, PAGE.format(price="9,99 €", lp="199,00 €")

    class Eng(engmod.ScanEngine):
        def __init__(self, *a, **kw):
            super().__init__(*a, fetcher=fetch, **kw)
    lst = tmp_path / "mis.txt"
    lst.write_text("B0BGTEST01\nB0BGTEST02\n")
    s = Settings(markets=["ES"], concurrency=1, delay_min=0, delay_max=0, rescan_hours=0,
                 last_asin_file=str(lst), min_discount=50)
    for name, value in (("LOG_FILE", tmp_path / "ejecucion.log"), ("LOCK_FILE", tmp_path / "l.lock"),
                        ("PID_FILE", tmp_path / "p.pid"), ("DB_PATH", tmp_path / "t.db")):
        monkeypatch.setattr(background, name, value)
    monkeypatch.setattr(engmod, "ScanEngine", Eng)
    monkeypatch.setattr(background.Settings, "load", classmethod(lambda cls: s))
    assert background.run(once=True) == 0
    for h in list(background.log.handlers):
        h.flush(); h.close(); background.log.removeHandler(h)
    text = (tmp_path / "ejecucion.log").read_text(encoding="utf-8")
    assert "Lista propia" in text and "Vuelta terminada: 2 productos mirados, 2 alertas" in text
    assert "🚨 ERROR B0BGTEST01" in text and not (tmp_path / "p.pid").exists()


def test_captcha_not_sent_to_telegram_by_default(tmp_path, monkeypatch):
    from sniper import engine as engmod
    sent = []

    async def no_send(*a, **kw):
        sent.append(a); return True, "ok"
    monkeypatch.setattr(engmod.telegram, "send_message", no_send)
    monkeypatch.setattr(engmod.telegram, "send_photo", no_send)
    calls = {"n": 0}

    async def fetch(session, url, headers, proxy, timeout):
        calls["n"] += 1
        return 200, CAPTCHA_FORM if calls["n"] <= 4 else PAGE.format(price="180,00 €", lp="199,00 €")

    s = Settings(markets=["ES"], concurrency=1, delay_min=0, delay_max=0, rescan_hours=0,
                 telegram_token="T", telegram_chat_id="42", captcha_retry_first=0.05, captcha_retry_every=0.1)
    assert s.captcha_telegram is False
    eng = ScanEngine(s, ["B0CAPTCHA1"], Database(tmp_path / "t.db"), queue.Queue(), fetcher=fetch)
    eng.start(); eng._thread.join(20)
    assert not eng.running and sent == []
