"""Pestañas: reloj, medidores del colector y carga masiva."""

from __future__ import annotations

import tkinter.filedialog as filedialog
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

import customtkinter as ctk

from bulk_loader import BulkAddLine, build_bulk_add_sequence, parse_bulk_text, preview_commands
from command_queue import QueuedCommand
from polling_schedule import (
    PollingSlot,
    build_upload_commands,
    default_full_day_slot,
    parse_polling_line,
    parse_polling_response,
    polling_response_summary,
    slots_to_lines,
)
from protocol import (
    CMD_COUNT_METERS,
    CMD_POLLING_SCHEDULE,
    CMD_QUIT,
    CMD_WAKE,
    CMD_ZOOM,
    READ_FLAGS_MULTITARIFF,
    cmd_download_polling,
    cmd_multitariff_read,
    cmd_read_by_index,
    cmd_read_collector_clock,
    cmd_set_clock,
    cmd_set_clock_now,
    format_meter_reading_summary,
    parse_collector_clock,
    parse_collector_info,
    parse_meter_reading,
)

if TYPE_CHECKING:
    from ui.app_window import AppWindow


WAKE_SLOW = QueuedCommand(CMD_WAKE, rx_timeout_ms=45000, write_timeout_s=3.0, wait_rx=True)
ZOOM_CMD = QueuedCommand(CMD_ZOOM, rx_timeout_ms=15000, write_timeout_s=3.0, wait_rx=True)
POLLING_DOWNLOAD = QueuedCommand(
    cmd_download_polling(),
    multiline_ms=5000,
    rx_timeout_ms=20000,
    write_timeout_s=3.0,
    wait_rx=True,
)


class ClockTab(ctk.CTkScrollableFrame):
    """Reloj y horarios — similar a RemoteCOM (Clock / Time for reading / Startup&Stop)."""

    def __init__(self, master: ctk.CTkBaseClass, app: AppWindow) -> None:
        super().__init__(master, label_text="Reloj y horarios — Comados.doc §9–§12")
        self.app = app
        self._slots: List[PollingSlot] = []
        self._selected_index: Optional[int] = None
        self._polling_capture = False
        self._polling_lines: List[str] = []

        self.grid_columnconfigure(0, weight=1)

        # --- 1. Reloj del sistema / colector ---
        clock_box = ctk.CTkFrame(self)
        clock_box.grid(row=0, column=0, padx=8, pady=8, sticky="ew")
        clock_box.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(clock_box, text="§12 Fecha y hora del colector", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=3, padx=10, pady=(10, 6), sticky="w"
        )

        self.pc_time_var = ctk.StringVar(value="")
        self.collector_time_var = ctk.StringVar(value="(sin sincronizar)")
        ctk.CTkLabel(clock_box, text="Hora del PC:").grid(row=1, column=0, padx=10, pady=4, sticky="w")
        ctk.CTkLabel(
            clock_box, textvariable=self.pc_time_var, font=ctk.CTkFont(family="Consolas", size=13)
        ).grid(row=1, column=1, padx=8, pady=4, sticky="w")

        ctk.CTkLabel(clock_box, text="Última sync C…:").grid(row=2, column=0, padx=10, pady=4, sticky="w")
        ctk.CTkLabel(
            clock_box, textvariable=self.collector_time_var, font=ctk.CTkFont(family="Consolas", size=13)
        ).grid(row=2, column=1, padx=8, pady=4, sticky="w")

        clk_btns = ctk.CTkFrame(clock_box, fg_color="transparent")
        clk_btns.grid(row=3, column=0, columnspan=3, padx=10, pady=(6, 10), sticky="ew")
        ctk.CTkButton(clk_btns, text="Sincronizar hora (C…)", command=self._sync_system_time).pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkButton(clk_btns, text="Ver fecha medidor índice 0", command=self._read_collector_clock).pack(
            side="left", padx=8
        )
        ctk.CTkLabel(
            clock_box,
            text="El manual (§12) solo documenta C… para fijar hora. No hay comando oficial para leer el reloj interno.",
            text_color=("gray40", "gray60"),
            wraplength=720,
            justify="left",
        ).grid(row=6, column=0, columnspan=3, padx=10, pady=(0, 10), sticky="w")

        ctk.CTkLabel(clock_box, text="Comando C (AAMMDDHHMMSS):").grid(row=4, column=0, padx=10, pady=4, sticky="w")
        self.clock_var = ctk.StringVar(value="")
        ctk.CTkEntry(clock_box, textvariable=self.clock_var).grid(
            row=4, column=1, columnspan=2, padx=8, pady=4, sticky="ew"
        )
        ctk.CTkButton(clock_box, text="Aplicar C…", command=self._apply_clock).grid(
            row=5, column=1, padx=8, pady=(0, 10), sticky="w"
        )

        # --- 2. Time for reading (polling) ---
        poll_box = ctk.CTkFrame(self)
        poll_box.grid(row=1, column=0, padx=8, pady=8, sticky="nsew")
        poll_box.grid_columnconfigure(0, weight=1)
        poll_box.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            poll_box, text="§10–§11 Horarios polling (comando i)", font=ctk.CTkFont(weight="bold")
        ).grid(row=0, column=0, padx=10, pady=(10, 6), sticky="w")

        add_row = ctk.CTkFrame(poll_box, fg_color="transparent")
        add_row.grid(row=1, column=0, padx=10, pady=4, sticky="ew")
        ctk.CTkLabel(add_row, text="Desde").pack(side="left", padx=(0, 4))
        self.start_var = ctk.StringVar(value="00:00")
        ctk.CTkEntry(add_row, textvariable=self.start_var, width=70).pack(side="left", padx=4)
        ctk.CTkLabel(add_row, text="Hasta").pack(side="left", padx=(8, 4))
        self.end_var = ctk.StringVar(value="23:59")
        ctk.CTkEntry(add_row, textvariable=self.end_var, width=70).pack(side="left", padx=4)

        self.schedule_box = ctk.CTkTextbox(poll_box, height=140, font=ctk.CTkFont(family="Consolas", size=12))
        self.schedule_box.grid(row=2, column=0, padx=10, pady=8, sticky="nsew")
        self._show_schedule_text(
            "Pulse Download (§10: QUIT + i) para ver horarios del colector.\n"
            "§11: para insertar, el colector no debe tener horarios. Use Día completo + Upload."
        )

        poll_btns = ctk.CTkFrame(poll_box, fg_color="transparent")
        poll_btns.grid(row=3, column=0, padx=10, pady=(0, 10), sticky="ew")
        for text, cmd in (
            ("Add", self._add_slot),
            ("Día completo", self._add_full_day),
            ("Delete", self._delete_slot),
            ("Clear", self._clear_slots),
            ("Download (i)", self._download_schedules),
            ("Upload", self._upload_schedules),
        ):
            ctk.CTkButton(poll_btns, text=text, width=110, command=cmd).pack(side="left", padx=3)

        self.poll_status = ctk.StringVar(
            value="Download = QUIT + i (§10). Upload requiere colector sin horarios (§11)."
        )
        ctk.CTkLabel(poll_box, textvariable=self.poll_status, text_color=("gray40", "gray60"), wraplength=560).grid(
            row=4, column=0, padx=10, pady=(0, 10), sticky="w"
        )

        # --- 3. Startup & Stop ---
        ss_box = ctk.CTkFrame(self)
        ss_box.grid(row=2, column=0, padx=8, pady=8, sticky="ew")

        ctk.CTkLabel(ss_box, text="§9 Forzar lecturas (QUIT / WAKE / ZOOM)", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=3, padx=10, pady=(10, 6), sticky="w"
        )
        ss_btns = ctk.CTkFrame(ss_box, fg_color="transparent")
        ss_btns.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="ew")
        ctk.CTkButton(ss_btns, text="QUIT (detener)", command=lambda: self.app._send_one(CMD_QUIT)).pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkButton(ss_btns, text="WAKE (tras QUIT)", command=self._send_wake).pack(side="left", padx=8)
        ctk.CTkButton(ss_btns, text="ZOOM (start read)", command=self._send_zoom).pack(side="left", padx=8)
        ctk.CTkButton(ss_btns, text="QUIT → WAKE → ZOOM", command=self._startup_full).pack(side="left", padx=8)

        ctk.CTkLabel(
            ss_box,
            text="Si WAKE responde ROUTERERROR: use QUIT primero y luego QUIT → WAKE → ZOOM.",
            text_color=("gray40", "gray60"),
            wraplength=720,
        ).grid(row=2, column=0, padx=10, pady=(0, 10), sticky="w")

        self._tick_pc_time()
        self.after(1000, self._schedule_tick)

    def _schedule_tick(self) -> None:
        self._tick_pc_time()
        self.after(1000, self._schedule_tick)

    def _tick_pc_time(self) -> None:
        now = datetime.now()
        self.pc_time_var.set(now.strftime("%Y-%m-%d %H:%M:%S"))

    def _sync_system_time(self) -> None:
        cmd = cmd_set_clock_now()
        self.clock_var.set(cmd[1:])

        def _done() -> None:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.collector_time_var.set(now)
            self.app._append_log("INFO", f"Reloj colector sincronizado: {now}")

        self.app._send_many([cmd], wait_rx=True, on_done=_done)
        self.app._append_log("INFO", "Sync System Time -> enviado al colector")

    def _read_collector_clock(self) -> None:
        if not self.app.client.is_connected:
            self.app._append_log("ERR", "Conecte primero al colector")
            return

        def _done() -> None:
            clock = self.collector_time_var.get().strip()
            if clock and clock != "(sin leer)":
                self.app._append_log(
                    "INFO",
                    f"Fecha medidor índice 0: {clock} "
                    "(última lectura almacenada; use Sync para fijar reloj del colector)",
                )
            else:
                self.app._append_log("WARN", "No se obtuvo fecha del medidor índice 0")

        self.app._send_many(
            [CMD_QUIT, cmd_read_collector_clock()],
            wait_rx=True,
            on_done=_done,
        )

    def _apply_clock(self) -> None:
        try:
            self.app._send_one(cmd_set_clock(self.clock_var.get()))
        except ValueError as exc:
            self.app._append_log("ERR", str(exc))

    def on_rx(self, text: str) -> None:
        self.capture_polling_line(text)
        clock = parse_collector_clock(text)
        if clock:
            self.collector_time_var.set(clock)

    def begin_polling_capture(self) -> None:
        self._polling_capture = True
        self._polling_lines = []

    def finish_polling_capture(self) -> str:
        self._polling_capture = False
        return "\n".join(self._polling_lines)

    def capture_polling_line(self, text: str) -> None:
        if not self._polling_capture:
            return
        line = text.strip()
        if not line or line.upper() == "OK":
            return
        # Evitar duplicar líneas idénticas consecutivas.
        if self._polling_lines and self._polling_lines[-1] == line:
            return
        self._polling_lines.append(line)

    def _show_schedule_text(self, text: str) -> None:
        self.schedule_box.configure(state="normal")
        self.schedule_box.delete("1.0", "end")
        self.schedule_box.insert("1.0", text.rstrip() + "\n")

    def _render_slots(self) -> None:
        if self._slots:
            self._show_schedule_text("\n".join(s.display() for s in self._slots))
        else:
            self._show_schedule_text("(sin horarios en la lista)")

    def _add_full_day(self) -> None:
        slot = default_full_day_slot()
        self._slots = [slot]
        self.start_var.set("00:00")
        self.end_var.set("23:59")
        self._render_slots()
        self.poll_status.set("Horario día completo: 00:00 – 23:59 (pulse Upload para enviarlo)")

    def _add_slot(self) -> None:
        try:
            from polling_schedule import _norm_time

            slot = PollingSlot(
                index=len(self._slots) + 1,
                start=_norm_time(self.start_var.get().strip()),
                end=_norm_time(self.end_var.get().strip()),
            )
            self._slots.append(slot)
            for i, s in enumerate(self._slots, start=1):
                s.index = i
            self._render_slots()
            self.poll_status.set(f"{len(self._slots)} horario(s) en la lista")
        except ValueError as exc:
            self.app._append_log("ERR", str(exc))

    def _delete_slot(self) -> None:
        if not self._slots:
            return
        self._slots.pop()
        for i, s in enumerate(self._slots, start=1):
            s.index = i
        self._render_slots()

    def _clear_slots(self) -> None:
        self._slots.clear()
        self._show_schedule_text("(lista vacía — Download o Día completo)")
        self.poll_status.set("Lista de horarios vacía")

    def _apply_download_result(self, raw: str) -> None:
        slots = parse_polling_response(raw)
        if slots:
            self._slots = slots
            self._render_slots()
            self.poll_status.set(f"Download OK: {len(slots)} horario(s) del colector")
            self.app._append_log("INFO", f"Horarios del colector ({len(slots)}):")
            for slot in slots:
                self.app._append_log("INFO", f"  {slot.display()}")
            return

        summary = polling_response_summary(raw)
        self.poll_status.set(summary)
        self.app._append_log("INFO", f"Horarios polling: {summary}")

        if raw.strip():
            self.app._append_log("INFO", f"RX polling completo:\n{raw}")
            self._show_schedule_text(
                f"# Respuesta del colector (sin franjas parseadas):\n{raw.strip()}\n\n"
                "# Si no hay horarios, pulse 'Día completo' y luego Upload."
            )
            self._slots.clear()
        else:
            self._slots.clear()
            self._show_schedule_text(
                "# El colector no respondió al comando i (o no tiene horarios).\n"
                "# 1) Verifique que haya hecho login (UnLock)\n"
                "# 2) Pulse Download de nuevo\n"
                "# 3) O use 'Día completo' (00:00-23:59) + Upload"
            )
            self.app._append_log(
                "WARN",
                "Sin respuesta al comando i. Si el colector no tiene horarios, "
                "use Día completo + Upload.",
            )

    def _download_schedules(self) -> None:
        if not self.app.client.is_connected:
            self.app._append_log("ERR", "Conecte primero al colector")
            return
        if self.app.command_queue.is_busy:
            self.app._append_log("ERR", "Hay una secuencia en curso")
            return

        self.poll_status.set("Descargando horarios del colector (QUIT + i)…")
        self._show_schedule_text("Leyendo horarios del colector…")
        self.begin_polling_capture()

        def on_step(cmd: str, response: str) -> None:
            if cmd.strip().lower() != "i":
                return
            # Respuesta acumulada del comando i (puede ser multilínea).
            for line in response.splitlines():
                self.capture_polling_line(line)

        def _done() -> None:
            raw = self.finish_polling_capture()
            if not raw.strip():
                raw = self.app.command_queue.last_response
            self._apply_download_result(raw)

        try:
            self.app.command_queue.start(
                [CMD_QUIT, POLLING_DOWNLOAD],
                wait_rx=True,
                on_step=on_step,
                on_done=_done,
            )
        except RuntimeError as exc:
            self.finish_polling_capture()
            self.app._append_log("ERR", str(exc))

    def _send_wake(self) -> None:
        if not self.app.client.is_connected:
            self.app._append_log("ERR", "Conecte primero al colector")
            return
        self.app._append_log(
            "INFO",
            "Secuencia QUIT → WAKE (WAKE solo suele fallar si el colector no está detenido)…",
        )
        self.app._send_many([CMD_QUIT, WAKE_SLOW], wait_rx=True)

    def _send_zoom(self) -> None:
        if not self.app.client.is_connected:
            self.app._append_log("ERR", "Conecte primero al colector")
            return
        self.app._send_many([ZOOM_CMD], wait_rx=True)

    def _upload_schedules(self) -> None:
        if not self.app.client.is_connected:
            self.app._append_log("ERR", "Conecte primero al colector")
            return
        if not self._slots:
            self.app._append_log("ERR", "No hay horarios para subir (use Add o Día completo)")
            return
        if self.app.command_queue.is_busy:
            self.app._append_log("ERR", "Hay otra secuencia en curso")
            return

        self.app._append_log(
            "WARN",
            "§11: el colector no debe tener horarios previos. "
            "Si Upload falla, vacíe horarios en el colector y reintente.",
        )
        # QUIT espera OK; comandos I… no bloquean esperando (formato no oficial);
        # luego WAKE/ZOOM con espera de respuesta.
        upload_cmds = [
            QueuedCommand(c, wait_rx=True, rx_timeout_ms=2500, write_timeout_s=3.0)
            for c in build_upload_commands(self._slots)
        ]
        commands = [
            QueuedCommand(CMD_QUIT, wait_rx=True, rx_timeout_ms=10000, write_timeout_s=3.0),
            *upload_cmds,
            WAKE_SLOW,
            ZOOM_CMD,
        ]
        self.poll_status.set(f"Subiendo {len(self._slots)} horario(s)…")
        self.app._append_log(
            "INFO",
            f"Upload: QUIT + {len(upload_cmds)} horario(s) + WAKE + ZOOM. "
            "Si responde NO, el formato I… puede ser incorrecto o ya hay horarios en el colector.",
        )

        def on_done() -> None:
            last = self.app.command_queue.last_response.strip().upper()
            if last == "NO":
                self.poll_status.set("Upload rechazado (NO) — vea el log")
            else:
                self.poll_status.set("Upload finalizado — verifique con Download (i)")
                self.app._append_log("INFO", "Upload terminado. Pulse Download (i) para verificar.")

        self.app.command_queue.start(commands, on_done=on_done, wait_rx=True)

    def _startup_full(self) -> None:
        if not self.app.client.is_connected:
            self.app._append_log("ERR", "Conecte primero al colector")
            return
        self.app._append_log(
            "INFO",
            "Secuencia QUIT → WAKE → ZOOM — espere respuesta de cada paso…",
        )
        self.app._send_many([CMD_QUIT, WAKE_SLOW, ZOOM_CMD], wait_rx=True)


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
        self.app._send_many(
            [CMD_QUIT, QueuedCommand(CMD_COUNT_METERS, multiline_ms=2000)],
            wait_rx=True,
        )

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
