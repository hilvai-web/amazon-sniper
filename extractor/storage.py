"""Almacén de ASINs únicos con guardado incremental y estado para reanudar."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path


class AsinStore:
    def __init__(self, csv_path: Path, limit: int):
        self.csv_path = csv_path
        self.limit = limit
        self._asins: dict[str, None] = {}   # dict = set que conserva el orden de ranking

    def __len__(self) -> int:
        return len(self._asins)

    @property
    def full(self) -> bool:
        return len(self._asins) >= self.limit

    def add_many(self, asins: list[str]) -> int:
        """Añade en orden hasta llegar al límite. Devuelve cuántos eran nuevos."""
        added = 0
        for asin in asins:
            if self.full:
                break
            if asin not in self._asins:
                self._asins[asin] = None
                added += 1
        return added

    def load(self) -> None:
        if not self.csv_path.exists():
            return
        with self.csv_path.open(newline="", encoding="utf-8") as f:
            self.add_many([row["ASIN"].strip() for row in csv.DictReader(f) if row.get("ASIN")])

    def save(self) -> None:
        """Escritura atómica: nunca deja un CSV a medias aunque se corte."""
        tmp = self.csv_path.with_suffix(".tmp")
        with tmp.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["ASIN"])
            writer.writerows([a] for a in self._asins)
        os.replace(tmp, self.csv_path)


def load_state(path: Path) -> tuple[list[str], set[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data["queue"], set(data["visited"])
    except (OSError, ValueError, KeyError):
        return [], set()


def save_state(path: Path, queue: list[str], visited: set[str]) -> None:
    path.write_text(json.dumps({"queue": queue, "visited": sorted(visited)}, indent=1),
                    encoding="utf-8")
