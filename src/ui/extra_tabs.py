"""Pestañas: reloj, medidores del colector y carga masiva."""

from __future__ import annotations

import tkinter.filedialog as filedialog
from datetime import datetime
from typing import TYPE_CHECKING, Callable, List, Optional

import customtkinter as ctk

from bulk_loader import BulkAddLine, build_bulk_add_sequence, parse_bulk_text, preview_commands
from protocol import (
    CMD_COUNT_METERS,
    CMD_POLLING_SCHEDULE,
    CMD_QUIT,
    READ_FLAGS_MULTITARIFF,
    cmd_multitariff_read,
    cmd_read_by_index,
    cmd_set_clock,
    cmd_set_clock_now,
    format_meter_reading_summary,
    parse_collector_info,
    parse_meter_reading,
)

if TYPE_CHECKING:
    from ui.app_window import AppWindow


class ClockTab(ctk.CTkFrame):
    def __init__(self, master: ctk.CTkBaseClass, app: AppWindow) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self, text="Ajustar fecha y hora del colector", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=3, padx=12, pady=(12, 8), sticky="w"
        )

        self.pc_time_var = ctk.StringVar(value="")
        ctk.CTkLabel(self, text="Hora del PC:").grid(row=1, column=0, padx=12, pady=6, sticky="w")
        ctk.CTkLabel(self, textvariable=self.pc_time_var, font=ctk.CTkFont(family="Consolas", size=14)).grid(
            row=1, column=1, padx=8, pady=6, sticky="w"
        )
        ctk.CTkButton(self, text="Actualizar", width=100, command=self._tick_pc_time).grid(
            row=1, column=2, padx=12, pady=6
        )

        ctk.CTkLabel(self, text="Comando C (AAMMDDHHMMSS):").grid(row=2, column=0, padx=12, pady=6, sticky="w")
        self.clock_var = ctk.StringVar(value="")
        ctk.CTkEntry(self, textvariable=self.clock_var, placeholder_text="ej. 260728101530").grid(
            row=2, column=1, columnspan=2, padx=8, pady=6, sticky="ew"
        )

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=3, column=0, columnspan=3, padx=12, pady=8, sticky="ew")
        ctk.CTkButton(btn_row, text="Usar hora del PC", command=self._use_pc_time).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_row, text="Aplicar al colector", command=self._apply_clock).pack(side="left", padx=8)
        ctk.CTkButton(btn_row, text="Ver horarios polling (i)", command=self._show_polling).pack(
            side="left", padx=8
        )

        ctk.CTkLabel(
            self,
            text="Formato: AA MM DD HH MM SS (12 dígitos). Ejemplo doc: C091012104216",
            text_color=("gray40", "gray60"),
            wraplength=520,
            justify="left",
        ).grid(row=4, column=0, columnspan=3, padx=12, pady=(8, 12), sticky="w")

        self._tick_pc_time()
        self.after(1000, self._schedule_tick)

    def _schedule_tick(self) -> None:
        self._tick_pc_time()
        self.after(1000, self._schedule_tick)

    def _tick_pc_time(self) -> None:
        now = datetime.now()
        self.pc_time_var.set(now.strftime("%Y-%m-%d %H:%M:%S"))
        if not self.clock_var.get().strip():
            self.clock_var.set(now.strftime("%y%m%d%H%M%S"))

    def _use_pc_time(self) -> None:
        cmd = cmd_set_clock_now()
        self.clock_var.set(cmd[1:])
        self.app._append_log("INFO", f"Reloj PC -> {cmd}")

    def _apply_clock(self) -> None:
        try:
            cmd = cmd_set_clock(self.clock_var.get())
            self.app._send_one(cmd)
        except ValueError as exc:
            self.app._append_log("ERR", str(exc))

    def _show_polling(self) -> None:
        self.app._send_many([CMD_QUIT, CMD_POLLING_SCHEDULE])


class MetersTab(ctk.CTkFrame):
    def __init__(self, master: ctk.CTkBaseClass, app: AppWindow) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._readings: List[str] = []
        self._scan_index = 0
        self._scan_total = 0
        self._scan_active = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        top = ctk.CTkFrame(self)
        top.grid(row=0, column=0, padx=12, pady=12, sticky="ew")
        top.grid_columnconfigure(4, weight=1)

        ctk.CTkLabel(top, text="Medidores del colector", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=6, padx=8, pady=(8, 4), sticky="w"
        )

        self.count_var = ctk.StringVar(value="Cantidad: ?")
        ctk.CTkLabel(top, textvariable=self.count_var).grid(row=1, column=0, padx=8, pady=6, sticky="w")
        ctk.CTkButton(top, text="Obtener cantidad (O)", command=self._fetch_count).grid(
            row=1, column=1, padx=4, pady=6
        )

        ctk.CTkLabel(top, text="Máx. a leer:").grid(row=1, column=2, padx=(12, 4), pady=6)
        self.max_var = ctk.StringVar(value="50")
        ctk.CTkEntry(top, textvariable=self.max_var, width=70).grid(row=1, column=3, padx=4, pady=6)

        ctk.CTkButton(top, text="Leer medidor (T1-T4)", command=self._read_current_meter).grid(
            row=1, column=4, padx=4, pady=6
        )
        ctk.CTkButton(top, text="Escanear todos", command=self._scan_all).grid(
            row=1, column=5, padx=8, pady=6
        )

        self.progress_var = ctk.StringVar(value="")
        ctk.CTkLabel(top, textvariable=self.progress_var, text_color=("gray40", "gray60")).grid(
            row=2, column=0, columnspan=6, padx=8, pady=(0, 8), sticky="w"
        )

        ctk.CTkLabel(
            self,
            text="T1/T2/T3/T4 según flags F18/F20/F21/F22 (RemoteCOM). Total=F19.",
            text_color=("gray40", "gray60"),
        ).grid(row=1, column=0, padx=12, pady=(0, 4), sticky="w")

        header = (
            f"{'Medidor':<14} {'T1':>10} {'T2':>10} {'T3':>10} {'T4':>10} "
            f"{'Total':>10} {'Fecha':<20}\n"
            + "-" * 90 + "\n"
        )
        self.table = ctk.CTkTextbox(self, font=ctk.CTkFont(family="Consolas", size=12))
        self.table.grid(row=3, column=0, padx=12, pady=(0, 12), sticky="nsew")
        self.table.insert("1.0", header)
        self.table.configure(state="disabled")

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=4, column=0, padx=12, pady=(0, 12), sticky="ew")
        ctk.CTkButton(actions, text="Limpiar tabla", command=self._clear_table).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="Cancelar escaneo", command=self._cancel_scan).pack(side="left")

    def _clear_table(self) -> None:
        self._readings.clear()
        self.table.configure(state="normal")
        self.table.delete("1.0", "end")
        header = (
            f"{'Medidor':<14} {'T1':>10} {'T2':>10} {'T3':>10} {'T4':>10} "
            f"{'Total':>10} {'Fecha':<20}\n"
            + "-" * 90 + "\n"
        )
        self.table.insert("1.0", header)
        self.table.configure(state="disabled")

    def _fmt(self, val: Optional[float]) -> str:
        return f"{val:>10.2f}" if val is not None else f"{'—':>10}"

    def _append_row(self, reading) -> None:
        line = (
            f"{reading.meter_id:<14} {self._fmt(reading.t1)} {self._fmt(reading.t2)} "
            f"{self._fmt(reading.t3)} {self._fmt(reading.t4)} {self._fmt(reading.total)} "
            f"{reading.datetime_text:<20}\n"
        )
        self.table.configure(state="normal")
        self.table.insert("end", line)
        self.table.see("end")
        self.table.configure(state="disabled")
        self.app._append_log("INFO", format_meter_reading_summary(reading))

    def on_collector_info(self, raw: str) -> None:
        info = parse_collector_info(raw)
        if info and info.meter_count is not None:
            self.count_var.set(f"Cantidad: {info.meter_count}")
            self.max_var.set(str(info.meter_count))

    def on_meter_rx(self, raw: str) -> None:
        reading = parse_meter_reading(raw)
        if reading:
            self._append_row(reading)

    def _fetch_count(self) -> None:
        if not self.app.client.is_connected:
            self.app._append_log("ERR", "Conecte primero al colector")
            return
        self.app._send_many([CMD_QUIT, CMD_COUNT_METERS], pause_ms=400)

    def _read_current_meter(self) -> None:
        meter = self.app.meter_var.get().strip()
        if not meter:
            self.app._append_log("ERR", "Indique el medidor en la pestaña Comandos")
            return
        try:
            self.app._send_one(cmd_multitariff_read(meter))
        except ValueError as exc:
            self.app._append_log("ERR", str(exc))

    def _scan_all(self) -> None:
        if not self.app.client.is_connected:
            self.app._append_log("ERR", "Conecte primero al colector")
            return
        if self.app.command_queue.is_busy:
            self.app._append_log("ERR", "Hay otra secuencia en curso")
            return
        try:
            total = int(self.max_var.get().strip())
        except ValueError:
            self.app._append_log("ERR", "Máx. a leer debe ser un número")
            return
        if total <= 0 or total > 5000:
            self.app._append_log("ERR", "Use un máximo entre 1 y 5000")
            return

        commands = [cmd_read_by_index(i, READ_FLAGS_MULTITARIFF) for i in range(total)]
        self._scan_active = True
        self._scan_total = total
        self._scan_index = 0
        self.progress_var.set(f"Escaneando 0/{total}…")

        def on_step(_cmd: str, rx: str) -> None:
            self._scan_index += 1
            self.progress_var.set(f"Escaneando {self._scan_index}/{total}…")
            self.on_meter_rx(rx)

        def on_done() -> None:
            self._scan_active = False
            self.progress_var.set(f"Listo: {self._scan_index} lecturas")

        self.app.command_queue.start(commands, on_step=on_step, on_done=on_done, wait_rx=True)

    def _cancel_scan(self) -> None:
        if self.app.command_queue.is_busy:
            self.app.command_queue.cancel()
            self._scan_active = False
            self.progress_var.set("Escaneo cancelado")


class BulkLoadTab(ctk.CTkFrame):
    def __init__(self, master: ctk.CTkBaseClass, app: AppWindow) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._items: List[BulkAddLine] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(self, text="Carga masiva de medidores", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, padx=12, pady=(12, 4), sticky="w"
        )

        ctk.CTkLabel(
            self,
            text="Pegue líneas o cargue archivo. Formatos: A… completo | medidor,cabecera | medidor (CP4)",
            text_color=("gray40", "gray60"),
            wraplength=700,
            justify="left",
        ).grid(row=1, column=0, padx=12, pady=(0, 4), sticky="w")

        self.input_box = ctk.CTkTextbox(self, height=140, font=ctk.CTkFont(family="Consolas", size=12))
        self.input_box.grid(row=2, column=0, padx=12, pady=8, sticky="nsew")
        self.input_box.insert(
            "1.0",
            "# Ejemplos:\n"
            "# 23388410,90059613\n"
            "# A000023388410000090059613\n"
            "# 23388410\n",
        )

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=3, column=0, padx=12, pady=4, sticky="ew")
        ctk.CTkButton(btn_row, text="Cargar archivo…", command=self._load_file).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_row, text="Previsualizar", command=self._preview).pack(side="left", padx=8)
        ctk.CTkButton(btn_row, text="Ejecutar carga", command=self._execute).pack(side="left", padx=8)

        self.preview_box = ctk.CTkTextbox(self, font=ctk.CTkFont(family="Consolas", size=12))
        self.preview_box.grid(row=4, column=0, padx=12, pady=(4, 12), sticky="nsew")
        self.preview_box.configure(state="disabled")

        self.status_var = ctk.StringVar(value="")
        ctk.CTkLabel(self, textvariable=self.status_var, text_color=("gray40", "gray60")).grid(
            row=5, column=0, padx=12, pady=(0, 12), sticky="w"
        )

    def _load_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Archivo de medidores",
            filetypes=[
                ("Texto/CSV", "*.txt *.csv"),
                ("Todos", "*.*"),
            ],
        )
        if not path:
            return
        with open(path, "r", encoding="utf-8-sig") as fh:
            content = fh.read()
        self.input_box.delete("1.0", "end")
        self.input_box.insert("1.0", content)
        self._preview()

    def _preview(self) -> None:
        content = self.input_box.get("1.0", "end")
        self._items = parse_bulk_text(content)
        lines = preview_commands(self._items)
        errors = sum(1 for i in self._items if i.error)
        ok = len(self._items) - errors
        self.status_var.set(f"{ok} válidos, {errors} con error, {len(lines)} comandos")

        self.preview_box.configure(state="normal")
        self.preview_box.delete("1.0", "end")
        self.preview_box.insert("1.0", "\n".join(lines) if lines else "(sin líneas)")
        self.preview_box.configure(state="disabled")

    def _execute(self) -> None:
        if not self.app.client.is_connected:
            self.app._append_log("ERR", "Conecte primero al colector")
            return
        if self.app.command_queue.is_busy:
            self.app._append_log("ERR", "Hay otra secuencia en curso")
            return
        if not self._items:
            self._preview()
        valid = [i for i in self._items if not i.error]
        if not valid:
            self.app._append_log("ERR", "No hay líneas válidas para cargar")
            return

        commands = build_bulk_add_sequence(valid)
        self.status_var.set(f"Enviando {len(commands)} comandos…")

        def on_done() -> None:
            self.status_var.set(f"Carga finalizada ({len(valid)} medidores)")

        self.app.command_queue.start(commands, on_done=on_done, wait_rx=False)
