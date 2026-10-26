"""Interfaz gráfica oscura con CustomTkinter."""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

import sys

import customtkinter as ctk

from .config import APP_NAME, APP_VERSION, DB_PATH, MARKETPLACES, Settings
from .db import Database
from . import extract_runner, updater
from .engine import ScanEngine
from .scraper import load_asins, load_proxies

MAX_TABLE_ROWS = 2000  # la tabla guarda las últimas N filas para no saturar Tk

ACCENT = "#ff9900"
ACCENT_HOVER = "#e68a00"
DANGER = "#c0392b"
DANGER_HOVER = "#a93226"


class SniperApp(ctk.CTk):
    def __init__(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("1280x800")
        self.minsize(1000, 650)

        self.settings = Settings.load()
        self.db = Database(DB_PATH)
        self.events: queue.Queue[tuple] = queue.Queue()
        self.engine: ScanEngine | None = None
        self.asins: list[str] = []
        self.show_only_deals = tk.BooleanVar(value=False)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_main()
        self._style_table()

        if self.settings.lists_version < 2:
            # v2.2.0: las listas viejas mezclaban productos de otras categorías
            if extract_runner.purge_lists():
                self._log("Listas de productos antiguas borradas: se rehacen al pulsar INICIAR.")
            self.settings.lists_version = 2
            self.settings.save()
        own = extract_runner.csv_path(self.settings.category)
        if self.settings.last_asin_file and Path(self.settings.last_asin_file) != own:
            self._load_asin_file(self.settings.last_asin_file, quiet=True)
            self.custom_file = bool(self.asins)
        else:
            self._load_category_list(quiet=True)
        self._refresh_db_stats()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(200, self._poll_events)
        threading.Thread(target=self._check_update, daemon=True).start()

    # ================================================================ layout
    def _build_sidebar(self) -> None:
        holder = ctk.CTkFrame(self, width=340, corner_radius=0)
        holder.grid(row=0, column=0, sticky="nsew")
        holder.grid_rowconfigure(0, weight=1)
        holder.grid_columnconfigure(0, weight=1)
        side = ctk.CTkScrollableFrame(holder, width=320, corner_radius=0, fg_color="transparent")
        side.grid(row=0, column=0, sticky="nsew")
        # los botones grandes quedan fijos abajo, siempre visibles
        actions = ctk.CTkFrame(holder, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="ew")

        ctk.CTkLabel(side, text="🎯 AMAZON SNIPER", font=ctk.CTkFont(size=24, weight="bold"),
                     text_color=ACCENT).pack(pady=(18, 2))
        self.update_anchor = ctk.CTkLabel(side, text=f"Detector de errores de precio · v{APP_VERSION}",
                                          text_color="gray70")
        self.update_anchor.pack(pady=(0, 14))
        # aparece solo cuando hay una versión nueva en GitHub
        self.update_btn = ctk.CTkButton(side, text="", fg_color="#2e7d32", hover_color="#256628",
                                        command=self._apply_update)
        self._update_info: tuple[str, str] | None = None

        # --- ASINs
        self._section(side, "Productos")
        self.categories, default_cat = extract_runner.categories()
        if self.settings.category not in self.categories:
            self.settings.category = default_cat
        self.extract_run: extract_runner.ExtractRun | None = None
        self.custom_file = False          # True si el usuario cargó su propio archivo
        self._scan_after_extract = False  # INICIAR pidió extraer antes de escanear
        ctk.CTkLabel(side, text="Categoría", anchor="w", text_color="gray80").pack(fill="x", padx=16, pady=(4, 0))
        self.category_menu = ctk.CTkOptionMenu(
            side, values=list(self.categories.values()), command=self._on_category,
            fg_color="gray25", button_color="gray30", button_hover_color="gray35")
        self.category_menu.set(self.categories[self.settings.category])
        self.category_menu.pack(fill="x", padx=16, pady=(0, 4))
        self.extract_btn = ctk.CTkButton(side, text="⬇ Extraer Top 1000 de esta categoría",
                                         command=self._toggle_extract)
        self.extract_btn.pack(fill="x", padx=16, pady=4)
        ctk.CTkButton(side, text="📂 Cargar otro archivo (.txt / .csv)", fg_color="gray25", hover_color="gray30",
                      command=self._pick_asin_file).pack(fill="x", padx=16, pady=4)
        self.asin_label = ctk.CTkLabel(side, text="Ningún archivo cargado", text_color="gray70", wraplength=280)
        self.asin_label.pack(padx=16, pady=(0, 6))

        # --- Telegram
        self._section(side, "Telegram")
        self.token_entry = self._entry(side, "Token del bot", self.settings.telegram_token, show="•")
        self.chat_entry = self._entry(side, "Chat ID", self.settings.telegram_chat_id)

        # --- Filtros
        self._section(side, "Filtros")
        self.discount_entry = self._entry(side, "Descuento mínimo (%)", f"{self.settings.min_discount:g}")
        self.maxprice_entry = self._entry(side, "Precio máximo (€) · 0 = sin límite", f"{self.settings.max_price:g}")
        self.minvalue_entry = self._entry(side, "Valor mínimo del producto (€) · precio normal",
                                          f"{self.settings.min_value:g}")
        self.prime_var = tk.BooleanVar(value=self.settings.only_prime)
        ctk.CTkCheckBox(side, text="Solo Prime (enviado por Amazon)", variable=self.prime_var,
                        fg_color=ACCENT, hover_color=ACCENT_HOVER).pack(anchor="w", padx=16, pady=(8, 2))
        ctk.CTkLabel(side, text="Excluir si el título contiene (separado por comas)", anchor="w",
                     text_color="gray80").pack(fill="x", padx=16, pady=(4, 0))
        self.exclude_box = ctk.CTkTextbox(side, height=90, wrap="word")
        self.exclude_box.insert("1.0", self.settings.exclude_keywords)
        self.exclude_box.pack(fill="x", padx=16, pady=(0, 2))

        ctk.CTkLabel(side, text="Marketplaces", anchor="w").pack(fill="x", padx=16, pady=(8, 0))
        grid = ctk.CTkFrame(side, fg_color="transparent")
        grid.pack(fill="x", padx=16)
        self.market_vars: dict[str, tk.BooleanVar] = {}
        for i, code in enumerate(MARKETPLACES):
            var = tk.BooleanVar(value=code in self.settings.markets)
            self.market_vars[code] = var
            ctk.CTkCheckBox(grid, text=code, variable=var, width=80,
                            fg_color=ACCENT, hover_color=ACCENT_HOVER).grid(row=i // 3, column=i % 3, pady=3, sticky="w")

        # --- Avanzado
        self._section(side, "Rendimiento")
        self.conc_entry = self._entry(side, "Peticiones simultáneas", str(self.settings.concurrency))
        self.delay_entry = self._entry(side, "Pausa entre peticiones (s) min-max",
                                       f"{self.settings.delay_min:g}-{self.settings.delay_max:g}")
        self.rescan_entry = self._entry(side, "No re-escanear antes de (horas)", f"{self.settings.rescan_hours:g}")
        ctk.CTkButton(side, text="🌐 Cargar proxies (.txt, opcional)", fg_color="gray25", hover_color="gray30",
                      command=self._pick_proxy_file).pack(fill="x", padx=16, pady=4)
        self.proxy_label = ctk.CTkLabel(side, text=self._proxy_text(), text_color="gray70", wraplength=280)
        self.proxy_label.pack(padx=16)

        # --- Botones grandes
        ctk.CTkButton(side, text="Probar Telegram", fg_color="gray25", hover_color="gray30",
                      command=self._test_telegram).pack(fill="x", padx=16, pady=(16, 12))

        self.continuous_var = tk.BooleanVar(value=self.settings.continuous)
        ctk.CTkCheckBox(actions, text="🔁 Escaneo continuo (al acabar, vuelve a empezar)",
                        variable=self.continuous_var, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                        command=self._on_continuous).pack(anchor="w", padx=16, pady=(12, 0))
        self._cycle = 0
        self._next_cycle_at = 0.0
        self._cycle_after: str | None = None
        self._user_stopped = False

        self.start_btn = ctk.CTkButton(actions, text="▶  INICIAR SNIPER", height=64, fg_color=ACCENT,
                                       hover_color=ACCENT_HOVER, text_color="black",
                                       font=ctk.CTkFont(size=20, weight="bold"), command=self._start)
        self.start_btn.pack(fill="x", padx=16, pady=(14, 8))
        self.stop_btn = ctk.CTkButton(actions, text="■  DETENER", height=52, fg_color=DANGER,
                                      hover_color=DANGER_HOVER, font=ctk.CTkFont(size=18, weight="bold"),
                                      command=self._stop, state="disabled")
        self.stop_btn.pack(fill="x", padx=16, pady=(0, 16))

    def _build_main(self) -> None:
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=14, pady=14)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(2, weight=1)

        # tarjetas de estadísticas
        cards = ctk.CTkFrame(main, fg_color="transparent")
        cards.grid(row=0, column=0, sticky="ew")
        self.stat_labels: dict[str, ctk.CTkLabel] = {}
        for i, (key, label) in enumerate([("done", "Escaneados"), ("total", "En cola"),
                                          ("alerts", "Errores detectados"), ("db", "Histórico BD")]):
            cards.grid_columnconfigure(i, weight=1)
            card = ctk.CTkFrame(cards, corner_radius=10)
            card.grid(row=0, column=i, sticky="ew", padx=4)
            ctk.CTkLabel(card, text=label, text_color="gray70").pack(pady=(10, 0))
            value = ctk.CTkLabel(card, text="0", font=ctk.CTkFont(size=26, weight="bold"))
            value.pack(pady=(0, 10))
            self.stat_labels[key] = value

        bar = ctk.CTkFrame(main, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", pady=(12, 6))
        bar.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(bar, progress_color=ACCENT)
        self.progress.set(0)
        self.progress.grid(row=0, column=0, sticky="ew", padx=(4, 12))
        self.status_label = ctk.CTkLabel(bar, text="Listo", width=160, anchor="e")
        self.status_label.grid(row=0, column=1)
        ctk.CTkCheckBox(bar, text="Solo errores", variable=self.show_only_deals,
                        command=self._apply_filter).grid(row=0, column=2, padx=(12, 0))

        # tabla
        table_frame = ctk.CTkFrame(main)
        table_frame.grid(row=2, column=0, sticky="nsew")
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(0, weight=1)
        cols = ("asin", "market", "title", "price", "reference", "discount", "status")
        heads = ("ASIN", "País", "Producto", "Precio", "Referencia", "Dto.", "Estado")
        widths = (110, 50, 360, 90, 90, 60, 110)
        self.table = ttk.Treeview(table_frame, columns=cols, show="headings", style="Sniper.Treeview")
        for col, head, width in zip(cols, heads, widths):
            self.table.heading(col, text=head)
            self.table.column(col, width=width, anchor="w" if col == "title" else "center",
                              stretch=col == "title")
        scroll = ctk.CTkScrollbar(table_frame, command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        scroll.grid(row=0, column=1, sticky="ns", pady=6)
        self.table.tag_configure("deal", background="#4a1f1f", foreground="#ffb3b3")
        self.table.tag_configure("muted", foreground="gray55")
        self.table.bind("<Double-1>", self._open_selected)
        self._rows: list[tuple[tuple, str, str]] = []  # (valores, tag, url)
        self._urls: dict[str, str] = {}

        # registro
        self.log_box = ctk.CTkTextbox(main, height=150, font=ctk.CTkFont(family="Consolas", size=12))
        self.log_box.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.log_box.configure(state="disabled")
        self._log("Doble clic en una fila para abrir el producto en el navegador.")

    def _style_table(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Sniper.Treeview", background="#1e1e1e", fieldbackground="#1e1e1e",
                        foreground="#e0e0e0", rowheight=26, borderwidth=0, font=("Segoe UI", 10))
        style.configure("Sniper.Treeview.Heading", background="#2b2b2b", foreground=ACCENT,
                        relief="flat", font=("Segoe UI", 10, "bold"))
        style.map("Sniper.Treeview", background=[("selected", "#3a3a3a")])
        style.map("Sniper.Treeview.Heading", background=[("active", "#333333")])

    def _section(self, parent, text: str) -> None:
        ctk.CTkLabel(parent, text=text.upper(), font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=ACCENT, anchor="w").pack(fill="x", padx=16, pady=(14, 2))

    def _entry(self, parent, label: str, value: str, show: str | None = None) -> ctk.CTkEntry:
        ctk.CTkLabel(parent, text=label, anchor="w", text_color="gray80").pack(fill="x", padx=16, pady=(4, 0))
        entry = ctk.CTkEntry(parent, show=show) if show else ctk.CTkEntry(parent)
        entry.insert(0, value)
        entry.pack(fill="x", padx=16, pady=(0, 2))
        return entry

    # ============================================================== acciones
    def _pick_asin_file(self) -> None:
        path = filedialog.askopenfilename(title="Archivo de ASINs",
                                          filetypes=[("ASINs", "*.txt *.csv"), ("Todos", "*.*")])
        if path:
            self._load_asin_file(path)
            self.custom_file = True

    def _load_asin_file(self, path: str, quiet: bool = False) -> None:
        try:
            self.asins = load_asins(path)
        except OSError as exc:
            if not quiet:
                messagebox.showerror(APP_NAME, f"No se pudo leer el archivo:\n{exc}")
            return
        self.settings.last_asin_file = path
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        self.asin_label.configure(text=f"{name}: {len(self.asins):,} ASINs únicos".replace(",", "."))
        self._log(f"Cargados {len(self.asins)} ASINs desde {name}")

    # ------------------------------------------------------- categorías
    def _category_key(self) -> str:
        name = self.category_menu.get()
        return next((k for k, v in self.categories.items() if v == name), self.settings.category)

    def _on_category(self, _name: str) -> None:
        self.settings.category = self._category_key()
        self.settings.save()
        self._load_category_list()

    def _load_category_list(self, quiet: bool = False) -> None:
        """Carga el CSV ya extraído de la categoría elegida, si existe."""
        key = self.settings.category
        self.custom_file = False
        path = extract_runner.csv_path(key)
        if path.exists():
            self._load_asin_file(str(path), quiet=quiet)
        else:
            self.asins = []
            self.settings.last_asin_file = ""
            self.asin_label.configure(text=f"{self.categories[key]}: la lista se creará al pulsar INICIAR")

    def _toggle_extract(self) -> None:
        if self.extract_run and self.extract_run.running:
            self._cancel_extract()
            return
        self._begin_extract()

    def _cancel_extract(self) -> None:
        if self._scan_after_extract:
            self._reset_buttons("Detenido")
        self._scan_after_extract = False
        if self.extract_run:
            self.extract_run.cancel()
        self._log("Extracción cancelada. Lo ya extraído queda guardado; al volver a empezar continúa.")

    def _begin_extract(self) -> bool:
        key = self._category_key()
        self.extract_run = extract_runner.ExtractRun(key, self.events)
        try:
            self.extract_run.start()
        except RuntimeError as exc:
            messagebox.showerror(APP_NAME, f"No se pudo lanzar la búsqueda de productos:\n{exc}")
            return False
        self._log(f"Buscando los 1000 productos más vendidos de {self.categories[key]} (tarda unos minutos)…")
        self.extract_btn.configure(text="✖ Cancelar extracción", fg_color=DANGER, hover_color=DANGER_HOVER)
        self.category_menu.configure(state="disabled")
        return True

    def _on_extract_done(self, key: str, code: int) -> None:
        self.extract_btn.configure(text="⬇ Extraer Top 1000 de esta categoría",
                                   fg_color=("#3a7ebf", "#1f538d"), hover_color=("#325882", "#14375e"))
        self.category_menu.configure(state="normal")
        path = extract_runner.csv_path(key)
        if path.exists():
            self._load_asin_file(str(path))
        self.custom_file = False
        if code == 0:
            self._log(f"✅ Lista de {self.categories[key]} lista y cargada.")
        else:
            self._log(f"La extracción de {self.categories[key]} no llegó a 1000 (código {code}); "
                      "se usa lo obtenido.")
        if self._scan_after_extract:
            self._scan_after_extract = False
            if self.asins:
                self._start_scan()
            else:
                self._reset_buttons("No se pudo obtener la lista de productos")

    # ------------------------------------------------------ actualización
    def _check_update(self) -> None:
        latest = updater.check_latest()
        if latest and updater.is_newer(latest[0], APP_VERSION):
            self.events.put(("update_available", *latest))

    def _show_update(self, version: str, url: str) -> None:
        self._update_info = (version, url)
        self.update_btn.configure(text=f"⬆ Actualizar a {version}")
        self.update_btn.pack(fill="x", padx=16, pady=(0, 10), after=self.update_anchor)
        self._log(f"Hay una versión nueva disponible: {version} (tienes v{APP_VERSION}).")

    def _apply_update(self) -> None:
        if not self._update_info:
            return
        version, url = self._update_info
        if not updater.is_frozen():
            webbrowser.open(updater.RELEASES_PAGE)
            return
        if (self.engine and self.engine.running) or (self.extract_run and self.extract_run.running):
            messagebox.showinfo(APP_NAME, "Detén el escaneo antes de actualizar.")
            return
        if not messagebox.askyesno(APP_NAME, f"¿Descargar {version} y reiniciar Amazon Sniper?\n"
                                             "Tu configuración e historial se conservan."):
            return
        self.update_btn.configure(state="disabled", text="Descargando…")
        new_exe = Path(sys.executable).resolve().with_name("AmazonSniper.new.exe")

        def work() -> None:
            try:
                updater.download(url, new_exe,
                                 lambda pct: self.events.put(("log", f"Descargando actualización… {pct}%"))
                                 if pct % 20 == 0 else None)
                self.events.put(("update_ready", str(new_exe)))
            except OSError as exc:
                self.events.put(("log", f"No se pudo descargar la actualización: {exc}"))

        threading.Thread(target=work, daemon=True).start()

    def _finish_update(self, new_exe: str) -> None:
        try:
            updater.install_and_restart(Path(new_exe))
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"No se pudo instalar la actualización:\n{exc}\n\n"
                                           "Mueve la app a una carpeta que no sea el Escritorio.")
            return
        self._on_close()

    def _pick_proxy_file(self) -> None:
        path = filedialog.askopenfilename(title="Lista de proxies (host:puerto por línea)",
                                          filetypes=[("Texto", "*.txt"), ("Todos", "*.*")])
        if path:
            self.settings.proxies_file = path
            self.proxy_label.configure(text=self._proxy_text())

    def _proxy_text(self) -> str:
        if not self.settings.proxies_file:
            return "Sin proxies: se usa tu conexión directa"
        try:
            n = len(load_proxies(self.settings.proxies_file))
        except OSError:
            return "No se pudo leer el archivo de proxies"
        if n == 0:
            return "El archivo no tiene proxies (¿está vacío o sin guardar?)"
        return f"{n} proxies cargados"

    def _read_settings(self) -> bool:
        s = self.settings
        try:
            s.telegram_token = self.token_entry.get().strip()
            s.telegram_chat_id = self.chat_entry.get().strip()
            s.min_discount = float(self.discount_entry.get().replace(",", ".") or 0)
            s.max_price = float(self.maxprice_entry.get().replace(",", ".") or 0)
            s.concurrency = max(1, min(32, int(self.conc_entry.get() or 1)))
            lo, _, hi = self.delay_entry.get().replace(",", ".").partition("-")
            s.delay_min = max(0.0, float(lo or 0))
            s.delay_max = max(s.delay_min, float(hi or lo or 0))
            s.rescan_hours = max(0.0, float(self.rescan_entry.get().replace(",", ".") or 0))
            s.min_value = max(0.0, float(self.minvalue_entry.get().replace(",", ".") or 0))
        except ValueError:
            messagebox.showerror(APP_NAME, "Revisa los valores numéricos de la configuración.")
            return False
        s.only_prime = bool(self.prime_var.get())
        s.exclude_keywords = " ".join(self.exclude_box.get("1.0", "end").split())
        s.markets = [c for c, v in self.market_vars.items() if v.get()]
        if not s.markets:
            messagebox.showerror(APP_NAME, "Selecciona al menos un marketplace.")
            return False
        s.save()
        return True

    def _start(self) -> None:
        """INICIAR: si hace falta, primero saca la lista de la categoría y luego escanea."""
        if (self.engine and self.engine.running) or (self.extract_run and self.extract_run.running):
            return
        self._cancel_next_cycle()
        self._user_stopped = False
        if not self._read_settings():
            return
        if not self.custom_file:
            key = self._category_key()
            age = extract_runner.list_age_hours(key)
            fresh = (age is not None and age < self.settings.list_max_age_hours
                     and extract_runner.is_complete(key))
            if not fresh:
                self._scan_after_extract = True
                if self._begin_extract():
                    self.start_btn.configure(state="disabled", text="⏳ BUSCANDO PRODUCTOS...")
                    self.stop_btn.configure(state="normal")
                    self.status_label.configure(text="Preparando lista…")
                else:
                    self._scan_after_extract = False
                return
            self._load_category_list(quiet=True)
        self._start_scan()

    def _start_scan(self) -> None:
        if not self.asins:
            messagebox.showwarning(APP_NAME, "No hay productos que vigilar.")
            return
        if not self.settings.telegram_token:
            self._log("Aviso: Telegram sin configurar, las alertas solo se verán en la tabla.")
        proxies: list[str] = []
        if self.settings.proxies_file:
            try:
                proxies = load_proxies(self.settings.proxies_file)
            except OSError:
                self._log("No se pudo leer el archivo de proxies; se usa conexión directa.")

        self.engine = ScanEngine(self.settings, self.asins, self.db, self.events, proxies)
        self.engine.start()
        self.start_btn.configure(state="disabled", text="⏳ ESCANEANDO...")
        self.stop_btn.configure(state="normal")
        self.status_label.configure(text="Escaneando…")

    def _reset_buttons(self, status: str) -> None:
        self.status_label.configure(text=status)
        self.start_btn.configure(state="normal", text="▶  INICIAR SNIPER")
        self.stop_btn.configure(state="disabled")

    def _stop(self) -> None:
        self._user_stopped = True
        if self._cycle_after:
            self._cancel_next_cycle()
            self._reset_buttons("Escaneo continuo detenido")
            self._log("Escaneo continuo detenido.")
            return
        if self.extract_run and self.extract_run.running:
            self._cancel_extract()
            self._reset_buttons("Detenido")
            return
        if self.engine:
            self.engine.stop()
            self.status_label.configure(text="Deteniendo…")
            self.stop_btn.configure(state="disabled")

    def _test_telegram(self) -> None:
        import asyncio
        import threading

        import aiohttp

        from . import telegram

        token, chat = self.token_entry.get().strip(), self.chat_entry.get().strip()

        def run() -> None:
            async def go():
                async with aiohttp.ClientSession() as session:
                    return await telegram.send_message(session, token, chat,
                                                       "✅ Amazon Sniper conectado correctamente.")
            ok, detail = asyncio.run(go())
            self.events.put(("log", "Telegram OK ✅" if ok else f"Telegram falló: {detail}"))

        threading.Thread(target=run, daemon=True).start()

    def _open_selected(self, _event=None) -> None:
        for item in self.table.selection():
            url = self._urls.get(item)
            if url:
                webbrowser.open(url)

    # ========================================================= bucle eventos
    def _poll_events(self) -> None:
        handled = 0
        try:
            while handled < 300:
                event = self.events.get_nowait()
                handled += 1
                kind = event[0]
                if kind == "log":
                    self._log(event[1])
                elif kind == "row":
                    self._add_row(event[1])
                elif kind == "progress":
                    done, total, alerts = event[1:]
                    self.stat_labels["done"].configure(text=f"{done:,}".replace(",", "."))
                    self.stat_labels["total"].configure(text=f"{total:,}".replace(",", "."))
                    self.stat_labels["alerts"].configure(text=str(alerts))
                    self.progress.set(done / total if total else 0)
                elif kind == "finished":
                    self._on_finished(event[1])
                elif kind == "extract_done":
                    self._on_extract_done(event[1], event[2])
                elif kind == "update_available":
                    self._show_update(event[1], event[2])
                elif kind == "update_ready":
                    self._finish_update(event[1])
        except queue.Empty:
            pass
        self.after(150 if handled else 300, self._poll_events)

    def _add_row(self, row: dict) -> None:
        fmt = lambda v: f"{v:.2f} €".replace(".", ",") if v else "—"  # noqa: E731
        values = (row["asin"], row["market"], row["title"][:90], fmt(row["price"]),
                  fmt(row["reference"]), f"-{row['discount']:.0f}%" if row["discount"] > 0 else "",
                  row["status"])
        tag = "deal" if row["status"].startswith("🚨") else ("" if row["status"] == "ok" else "muted")
        self._rows.append((values, tag, row["url"]))
        if len(self._rows) > MAX_TABLE_ROWS + 250:
            # recorta en bloque las filas normales más antiguas; los errores se conservan
            deals = [r for r in self._rows if r[1] == "deal"]
            others = [r for r in self._rows if r[1] != "deal"]
            room = max(0, MAX_TABLE_ROWS - len(deals))
            keep = set(map(id, deals + others[len(others) - room:]))
            self._rows = [r for r in self._rows if id(r) in keep]
            self._rebuild_table()
            return
        if not self.show_only_deals.get() or tag == "deal":
            self._insert(values, tag, row["url"])

    def _insert(self, values: tuple, tag: str, url: str) -> None:
        item = self.table.insert("", 0, values=values, tags=(tag,) if tag else ())
        self._urls[item] = url

    def _rebuild_table(self) -> None:
        self.table.delete(*self.table.get_children())
        self._urls.clear()
        only = self.show_only_deals.get()
        for values, tag, url in self._rows:
            if not only or tag == "deal":
                self._insert(values, tag, url)

    def _apply_filter(self) -> None:
        self._rebuild_table()

    def _on_finished(self, reason: str) -> None:
        self._log(reason)
        self._refresh_db_stats()
        if self.continuous_var.get() and not self._user_stopped and not reason.startswith("Error"):
            self._schedule_next_cycle()
            return
        self.status_label.configure(text=reason)
        self.start_btn.configure(state="normal", text="▶  INICIAR SNIPER")
        self.stop_btn.configure(state="disabled")

    # ------------------------------------------------------ escaneo continuo
    def _on_continuous(self) -> None:
        self.settings.continuous = bool(self.continuous_var.get())
        self.settings.save()
        if not self.settings.continuous and self._cycle_after:
            self._stop()

    def _schedule_next_cycle(self) -> None:
        """Programa el siguiente ciclo. Si en este no quedaba nada por revisar
        (todo escaneado hace poco), espera más para no dar vueltas en vacío."""
        self._cycle += 1
        idle = bool(self.engine and self.engine.total == 0)
        wait = 600 if idle else max(5, self.settings.cycle_pause_seconds)
        self._next_cycle_at = time.time() + wait
        self._log(f"🔁 Ciclo {self._cycle} terminado. "
                  + ("Todo está revisado hace poco; el siguiente" if idle else "El siguiente")
                  + f" empieza en {int(wait)} s.")
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._tick_countdown()

    def _tick_countdown(self) -> None:
        left = int(self._next_cycle_at - time.time())
        if left <= 0:
            self._cycle_after = None
            self.start_btn.configure(state="normal")
            self._start()
            return
        mins, secs = divmod(left, 60)
        self.start_btn.configure(text=f"⏳ PRÓXIMO CICLO EN {mins}:{secs:02d}")
        self.status_label.configure(text=f"Esperando ciclo {self._cycle + 1}")
        self._cycle_after = self.after(1000, self._tick_countdown)

    def _cancel_next_cycle(self) -> None:
        if self._cycle_after:
            self.after_cancel(self._cycle_after)
            self._cycle_after = None

    def _refresh_db_stats(self) -> None:
        st = self.db.stats()
        self.stat_labels["db"].configure(text=f"{st['products']:,}".replace(",", "."))

    def _log(self, msg: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        lines = int(self.log_box.index("end-1c").split(".")[0])
        if lines > 1000:
            self.log_box.delete("1.0", f"{lines - 1000}.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _on_close(self) -> None:
        if self.engine and self.engine.running:
            self.engine.stop()
        if self.extract_run and self.extract_run.running:
            self.extract_run.cancel()
        self._read_settings_silent()
        self.after(300, self._shutdown)

    def _read_settings_silent(self) -> None:
        try:
            self.settings.telegram_token = self.token_entry.get().strip()
            self.settings.telegram_chat_id = self.chat_entry.get().strip()
            self.settings.save()
        except Exception:  # noqa: BLE001
            pass

    def _shutdown(self) -> None:
        self.db.close()
        self.destroy()


def run() -> None:
    SniperApp().mainloop()
