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
</div><div id="availability">En stock</div></body></html>"""
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
                 delay_min=0, delay_max=0, rescan_hours=0)
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
