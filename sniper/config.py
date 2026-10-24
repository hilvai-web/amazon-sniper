"""Configuración persistente y rutas de la aplicación.

Todo se guarda junto al ejecutable (o junto a main.py en desarrollo), así la
app es 100% portable: copiar la carpeta es copiar la app con su historial.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "Amazon Sniper"
APP_VERSION = "1.0.0"


def app_dir() -> Path:
    """Carpeta donde vive el .exe (PyInstaller) o el proyecto (desarrollo)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


DB_PATH = app_dir() / "alertas.db"
CONFIG_PATH = app_dir() / "config.json"
LOG_PATH = app_dir() / "sniper.log"

# Marketplaces europeos soportados: código -> (dominio, idioma Accept-Language)
MARKETPLACES: dict[str, tuple[str, str]] = {
    "ES": ("www.amazon.es", "es-ES,es;q=0.9"),
    "DE": ("www.amazon.de", "de-DE,de;q=0.9"),
    "FR": ("www.amazon.fr", "fr-FR,fr;q=0.9"),
    "IT": ("www.amazon.it", "it-IT,it;q=0.9"),
    "NL": ("www.amazon.nl", "nl-NL,nl;q=0.9"),
    "BE": ("www.amazon.com.be", "fr-BE,fr;q=0.9"),
}


# Productos baratos o consumibles que no interesa vigilar (ES/DE/FR/IT/EN)
DEFAULT_EXCLUDE = (
    "pila, pilas, alcalina, alcalinas, bateria, baterias, battery, batteries, batterie, batterien, "
    "piles, funda, fundas, case, carcasa, hulle, custodia, protector, cristal templado, cable, "
    "cables, kabel, cavo, hdmi, cargador, cargadores, charger, ladegerat, chargeur, regleta, "
    "regletas, adaptador, adaptadores, cartucho, cartuchos, cartouche, tinta, toner, recambio, "
    "recambios, correa, consumible, consumibles, bombilla, bombillas"
)


@dataclass
class Settings:
    telegram_token: str = ""
    telegram_chat_id: str = ""
    min_discount: float = 50.0          # % mínimo para considerar error de precio
    max_price: float = 0.0              # 0 = sin límite
    markets: list[str] = field(default_factory=lambda: ["ES"])
    concurrency: int = 4                # peticiones simultáneas máximas
    delay_min: float = 2.0              # segundos entre peticiones (por worker)
    delay_max: float = 5.0
    timeout: float = 20.0
    proxies_file: str = ""              # .txt opcional, un proxy por línea
    batch_size: int = 10                # ASINs por identidad (proxy + UA)
    rescan_hours: float = 6.0           # no re-escanear un ASIN visto hace menos
    alert_cooldown_hours: float = 24.0  # antiduplicados de alertas
    use_history: bool = True            # comparar también con el histórico local
    min_value: float = 100.0            # solo productos cuyo precio normal sea >= esto
    exclude_keywords: str = DEFAULT_EXCLUDE
    last_asin_file: str = ""
    category: str = "electronica_hogar"  # categoría elegida para el extractor
    list_max_age_hours: float = 24.0    # al iniciar, rehace la lista si es más antigua
    extractor_dir: str = ""             # vacío = C:\Users\<usuario>\AmazonExtractor

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self) -> None:
        try:
            CONFIG_PATH.write_text(
                json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass
