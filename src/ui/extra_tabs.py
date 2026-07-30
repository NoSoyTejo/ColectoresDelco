"""Pestañas: reloj, medidores del colector y carga masiva."""

from __future__ import annotations

import tkinter.filedialog as filedialog
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional

import customtkinter as ctk

from bulk_loader import BulkAddLine, build_bulk_add_sequence, parse_bulk_text, preview_commands
from command_queue import QueuedCommand
from polling_schedule import (
    PollingSlot,
    build_upload_commands,
    default_full_day_slot,
    default_standard_polling_slots,
    format_polling_table,
    parse_polling_line,
    parse_polling_response,
    parse_polling_total,
    polling_response_summary,
    slots_to_lines,
)
from protocol import (
    CMD_COUNT_METERS,
    CMD_POLLING_SCHEDULE,
    CMD_QUIT,
    CMD_WAKE,
    CMD_ZOOM,
    MeterReading,
    READ_FLAGS_MULTITARIFF,
    cmd_download_polling,
    cmd_multitariff_read,
    cmd_read_by_index,
    cmd_read_collector_clock,
    cmd_set_clock,
    cmd_set_clock_now,
    format_meter_reading_summary,
    is_empty_polling_response,
    parse_collector_clock,
    parse_collector_info,
    parse_meter_reading,
)
from meters_export import export_meters_xlsx

if TYPE_CHECKING:
    from ui.app_window import AppWindow


WAKE_SLOW = QueuedCommand(CMD_WAKE, rx_timeout_ms=20000, write_timeout_s=2.0, wait_rx=True, abort_on_no=True)
ZOOM_CMD = QueuedCommand(CMD_ZOOM, rx_timeout_ms=10000, write_timeout_s=2.0, wait_rx=True, abort_on_no=True)
POLLING_DOWNLOAD = QueuedCommand(
    CMD_POLLING_SCHEDULE,
    multiline_ms=2500,
    rx_timeout_ms=12000,
    write_timeout_s=2.0,
    wait_rx=True,
    abort_on_no=False,  # NO = sin horarios (informativo)
)


class ClockTab(ctk.CTkScrollableFrame):
    """Reloj y horarios — similar a RemoteCOM (Clock / Time for reading / Startup&Stop)."""

    def __init__(self, master: ctk.CTkBaseClass, app: AppWindow) -> None:
        super().__init__(master, label_text="Reloj y horarios")
        self.app = app
        self._slots: List[PollingSlot] = []
        self._selected_index: Optional[int] = None
        self._polling_capture = False
        self._polling_lines: List[str] = []
        self._maint_widgets: List[Any] = []

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
        b_sync = ctk.CTkButton(clk_btns, text="Sync hora", width=110, height=32, command=self._sync_system_time)
        b_sync.pack(side="left", padx=(0, 8))
        b_idx0 = ctk.CTkButton(
            clk_btns, text="Fecha índice 0", width=130, height=32, command=self._read_collector_clock
        )
        b_idx0.pack(side="left", padx=8)
        self._maint_widgets.extend([b_sync, b_idx0])
        ctk.CTkLabel(
            clock_box,
            text="§12: Sync envía C… (hora del PC). Fecha índice 0 lee la última lectura almacenada (R0000F0312).",
            text_color=("gray40", "gray60"),
            wraplength=520,
            justify="left",
        ).grid(row=6, column=0, columnspan=3, padx=10, pady=(0, 10), sticky="w")

        ctk.CTkLabel(clock_box, text="Comando C (AAMMDDHHMMSS):").grid(row=4, column=0, padx=10, pady=4, sticky="w")
        self.clock_var = ctk.StringVar(value="")
        ctk.CTkEntry(clock_box, textvariable=self.clock_var).grid(
            row=4, column=1, columnspan=2, padx=8, pady=4, sticky="ew"
        )
        self.btn_apply_clock = ctk.CTkButton(
            clock_box, text="Aplicar C", width=100, height=30, command=self._apply_clock
        )
        self.btn_apply_clock.grid(row=5, column=1, padx=8, pady=(0, 10), sticky="w")
        self._maint_widgets.append(self.btn_apply_clock)

        # --- 2. Time for reading (polling) ---
        poll_box = ctk.CTkFrame(self)
        poll_box.grid(row=1, column=0, padx=8, pady=8, sticky="nsew")
        poll_box.grid_columnconfigure(0, weight=1)
        poll_box.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(
            poll_box, text="§10 Horarios polling", font=ctk.CTkFont(weight="bold")
        ).grid(row=0, column=0, padx=10, pady=(10, 2), sticky="w")
        ctk.CTkLabel(
            poll_box,
            text="Ejemplo: 00:00–03:59 | 04:00–15:59 | 16:00–23:59",
            text_color=("gray40", "gray60"),
            wraplength=520,
            justify="left",
        ).grid(row=1, column=0, padx=10, pady=(0, 4), sticky="w")

        add_row = ctk.CTkFrame(poll_box, fg_color="transparent")
        add_row.grid(row=2, column=0, padx=10, pady=4, sticky="ew")
        ctk.CTkLabel(add_row, text="Desde").pack(side="left", padx=(0, 4))
        self.start_var = ctk.StringVar(value="00:00")
        ctk.CTkEntry(add_row, textvariable=self.start_var, width=70).pack(side="left", padx=4)
        ctk.CTkLabel(add_row, text="Hasta").pack(side="left", padx=(8, 4))
        self.end_var = ctk.StringVar(value="03:59")
        ctk.CTkEntry(add_row, textvariable=self.end_var, width=70).pack(side="left", padx=4)

        self.schedule_box = ctk.CTkTextbox(poll_box, height=160, font=ctk.CTkFont(family="Consolas", size=12))
        self.schedule_box.grid(row=3, column=0, padx=10, pady=8, sticky="nsew")
        self._show_schedule_text(
            "Pulse Descargar para leer horarios del colector (§10).\n"
            "Respuesta típica:\n"
            "  Total de poolings: 3\n"
            "  1 - De 00:00 até 03:59\n"
            "  2 - De 04:00 até 15:59\n"
            "  3 - De 16:00 até 23:59\n"
            "Botón '3 franjas' carga el ejemplo; luego Subir (§11) si el colector está vacío."
        )

        poll_btns = ctk.CTkFrame(poll_box, fg_color="transparent")
        poll_btns.grid(row=4, column=0, padx=10, pady=(0, 4), sticky="ew")
        for text, cmd, w in (
            ("Add", self._add_slot, 70),
            ("3 franjas", self._add_standard_three, 90),
            ("Día", self._add_full_day, 70),
            ("Borrar", self._delete_slot, 70),
            ("Limpiar", self._clear_slots, 70),
        ):
            ctk.CTkButton(poll_btns, text=text, width=w, height=30, command=cmd).pack(side="left", padx=2)

        io_btns = ctk.CTkFrame(poll_box, fg_color="transparent")
        io_btns.grid(row=5, column=0, padx=10, pady=(0, 10), sticky="ew")
        self.btn_download = ctk.CTkButton(
            io_btns, text="Descargar", width=110, height=32, command=self._download_schedules
        )
        self.btn_download.pack(side="left", padx=(0, 8))
        self.btn_upload = ctk.CTkButton(
            io_btns,
            text="Subir",
            width=110,
            height=32,
            command=self._upload_schedules,
            fg_color=("#2B7A4B", "#1E5A35"),
            hover_color=("#236B40", "#174A2B"),
        )
        self.btn_upload.pack(side="left", padx=2)
        self._maint_widgets.extend([self.btn_download, self.btn_upload])
        ctk.CTkLabel(
            io_btns,
            text="Descargar = i   ·   Subir = I… (§11, experimental)",
            text_color=("gray40", "gray60"),
        ).pack(side="left", padx=(12, 0))

        self.poll_status = ctk.StringVar(
            value="Descargar lee horarios del colector. Subir requiere lista local y colector vacío."
        )
        ctk.CTkLabel(poll_box, textvariable=self.poll_status, text_color=("gray40", "gray60"), wraplength=520).grid(
            row=6, column=0, padx=10, pady=(0, 10), sticky="w"
        )

        # --- 3. Startup & Stop ---
        ss_box = ctk.CTkFrame(self)
        ss_box.grid(row=2, column=0, padx=8, pady=8, sticky="ew")

        ctk.CTkLabel(ss_box, text="§9 Forzar lecturas", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=3, padx=10, pady=(10, 6), sticky="w"
        )
        ss_btns = ctk.CTkFrame(ss_box, fg_color="transparent")
        ss_btns.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="ew")
        b_quit = ctk.CTkButton(
            ss_btns, text="QUIT", width=80, height=30, command=lambda: self.app._send_one(CMD_QUIT)
        )
        b_quit.pack(side="left", padx=(0, 6))
        b_wake = ctk.CTkButton(ss_btns, text="WAKE", width=80, height=30, command=self._send_wake)
        b_wake.pack(side="left", padx=6)
        b_zoom = ctk.CTkButton(ss_btns, text="ZOOM", width=80, height=30, command=self._send_zoom)
        b_zoom.pack(side="left", padx=6)
        b_force = ctk.CTkButton(
            ss_btns, text="Forzar (Q+W+Z)", width=130, height=30, command=self._startup_full
        )
        b_force.pack(side="left", padx=6)
        self._maint_widgets.extend([b_wake, b_zoom, b_force])
        # QUIT queda habilitado también en TCP limitado.

        ctk.CTkLabel(
            ss_box,
            text="Si WAKE falla: QUIT y luego Forzar (Q+W+Z).",
            text_color=("gray40", "gray60"),
            wraplength=520,
        ).grid(row=2, column=0, padx=10, pady=(0, 10), sticky="w")

        self._tick_pc_time()
        self.after(1000, self._schedule_tick)

    def set_maintenance_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for w in self._maint_widgets:
            try:
                w.configure(state=state)
            except Exception:
                pass

    def _schedule_tick(self) -> None:
        self._tick_pc_time()
        self.after(1000, self._schedule_tick)

    def _tick_pc_time(self) -> None:
        now = datetime.now()
        self.pc_time_var.set(now.strftime("%Y-%m-%d %H:%M:%S"))

    def _sync_system_time(self) -> None:
        if not self.app.guard_maintenance("Sync hora"):
            return
        if not self.app.client.is_connected:
            self.app._append_log("ERR", "Conecte primero al colector")
            return
        cmd = cmd_set_clock_now()
        self.clock_var.set(cmd[1:])

        def _done() -> None:
            q = self.app.command_queue
            last = q.last_response.strip().upper()
            if q.was_aborted or last != "OK":
                self.app._append_log(
                    "WARN",
                    f"Reloj NO sincronizado ({q.last_command!r} → {last or 'sin respuesta'}). "
                    "Login → QUIT → C… (una vez). Si sigue NO, pruebe O o VER.",
                )
                return
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.collector_time_var.set(now)
            self.app._append_log("INFO", f"Reloj colector sincronizado: {now}")

        self.app._send_many([CMD_QUIT, cmd], wait_rx=True, on_done=_done)
        self.app._append_log("INFO", "Sync System Time → QUIT + C… enviado al colector")

    def _read_collector_clock(self) -> None:
        if not self.app.guard_maintenance("Fecha índice 0"):
            return
        if not self.app.client.is_connected:
            self.app._append_log("ERR", "Conecte primero al colector")
            return

        read_clock: List[Optional[str]] = [None]

        def on_step(cmd: str, response: str) -> None:
            if not cmd.strip().upper().startswith("R"):
                return
            clock = parse_collector_clock(response)
            if clock:
                read_clock[0] = clock
                self.collector_time_var.set(clock)

        def _done() -> None:
            clock = read_clock[0]
            if clock:
                self.app._append_log(
                    "INFO",
                    f"Fecha medidor índice 0: {clock} "
                    "(última lectura almacenada; use Sync para fijar reloj del colector)",
                )
                return
            q = self.app.command_queue
            last = q.last_response.strip().upper()
            self.collector_time_var.set("(sin leer)")
            self.app._append_log(
                "WARN",
                f"No se obtuvo fecha del medidor índice 0 ({q.last_command!r} → {last or 'sin respuesta'}). "
                "Login → QUIT → R0000F0312 (una vez).",
            )

        if self.app.command_queue.is_busy:
            self.app._append_log("ERR", "Hay una secuencia en curso")
            return
        try:
            self.app.queue_start(
                [CMD_QUIT, cmd_read_collector_clock()],
                wait_rx=True,
                on_step=on_step,
                on_done=_done,
            )
        except RuntimeError as exc:
            self.app._append_log("ERR", str(exc))

    def _apply_clock(self) -> None:
        if not self.app.guard_maintenance("Aplicar C"):
            return
        if not self.app.client.is_connected:
            self.app._append_log("ERR", "Conecte primero al colector")
            return
        try:
            cmd = cmd_set_clock(self.clock_var.get())
        except ValueError as exc:
            self.app._append_log("ERR", str(exc))
            return

        def _done() -> None:
            q = self.app.command_queue
            last = q.last_response.strip().upper()
            if q.was_aborted or last != "OK":
                self.app._append_log(
                    "WARN",
                    f"Reloj NO actualizado ({q.last_command!r} → {last or 'sin respuesta'}). "
                    "Login → QUIT → C… (una vez).",
                )
                return
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.collector_time_var.set(now)
            self.app._append_log("INFO", f"Reloj colector actualizado: {now}")

        self.app._send_many([CMD_QUIT, cmd], wait_rx=True, on_done=_done)

    def on_rx(self, text: str) -> None:
        self.capture_polling_line(text)
        # No actualizar "Última sync C…" con RX sueltos; solo _sync_system_time / _read_collector_clock.

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

    def _render_slots(self, total: Optional[int] = None) -> None:
        if self._slots:
            self._show_schedule_text(format_polling_table(self._slots, total=total))
        else:
            self._show_schedule_text("(sin horarios en la lista)")

    def _add_standard_three(self) -> None:
        """Carga las 3 franjas del ejemplo Comados.doc §10."""
        self._slots = default_standard_polling_slots()
        self.start_var.set("00:00")
        self.end_var.set("03:59")
        self._render_slots(total=3)
        self.poll_status.set(
            "Total de poolings: 3 (ejemplo §10) — 00:00-03:59 | 04:00-15:59 | 16:00-23:59"
        )
        self.app._append_log(
            "INFO",
            "Cargadas 3 franjas tipicas del manual (§10). "
            "Si el colector esta vacio, puede intentar Subir (§11).",
        )

    def _add_full_day(self) -> None:
        slot = default_full_day_slot()
        self._slots = [slot]
        self.start_var.set("00:00")
        self.end_var.set("23:59")
        self._render_slots(total=1)
        self.poll_status.set("Horario día completo: 00:00 – 23:59 (pulse Subir para enviarlo)")

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
            self._render_slots(total=len(self._slots))
            self.poll_status.set(f"Total de poolings (lista local): {len(self._slots)}")
        except ValueError as exc:
            self.app._append_log("ERR", str(exc))

    def _delete_slot(self) -> None:
        if not self._slots:
            return
        self._slots.pop()
        for i, s in enumerate(self._slots, start=1):
            s.index = i
        self._render_slots(total=len(self._slots) if self._slots else 0)

    def _clear_slots(self) -> None:
        self._slots.clear()
        self._show_schedule_text(
            format_polling_table([], total=0)
            + "\n\n# Use Descargar o '3 franjas'"
        )
        self.poll_status.set("Total de poolings: 0 (lista vacia)")

    def _apply_download_result(self, raw: str) -> None:
        slots = parse_polling_response(raw)
        total = parse_polling_total(raw)

        if slots:
            self._slots = slots
            shown_total = total if total is not None else len(slots)
            self._render_slots(total=shown_total)
            self.poll_status.set(
                f"Descarga OK — Total de poolings: {shown_total} "
                f"(horarios en el colector)"
            )
            self.app._append_log(
                "INFO",
                f"Horarios del colector — Total de poolings: {shown_total}",
            )
            for slot in slots:
                self.app._append_log("INFO", f"  {slot.display()}")
            return

        if is_empty_polling_response(raw) or total == 0:
            self._slots.clear()
            self._show_schedule_text(
                format_polling_table([], total=0)
                + "\n\n# Sin horarios. Use '3 franjas' o Add, luego Subir (§11)\n"
                "# (el colector debe estar vacio para insertar)."
            )
            self.poll_status.set("Total de poolings: 0 — sin horarios en el colector")
            self.app._append_log(
                "INFO",
                "Descarga OK: Total de poolings: 0 (colector sin horarios).",
            )
            return

        summary = polling_response_summary(raw)
        self.poll_status.set(summary)
        self.app._append_log("INFO", f"Horarios polling: {summary}")

        if raw.strip():
            self.app._append_log("INFO", f"RX polling completo:\n{raw}")
            header = ""
            if total is not None:
                header = f"Total de poolings: {total}\n\n"
            self._show_schedule_text(
                f"# Respuesta del colector:\n{header}{raw.strip()}\n\n"
                "# Si no se parsearon franjas, use '3 franjas' como plantilla."
            )
            self._slots.clear()
        else:
            self._slots.clear()
            self._show_schedule_text(
                "# El colector no respondio al comando i.\n"
                "# 1) Login (UnLock)  2) QUIT (OK)  3) Descargar de nuevo"
            )
            self.app._append_log(
                "WARN",
                "Sin respuesta al comando i. Pulse QUIT, espere OK, y Descargar otra vez.",
            )

    def _download_schedules(self) -> None:
        if not self.app.guard_maintenance("Descargar horarios"):
            return
        if not self.app.client.is_connected:
            self.app._append_log("ERR", "Conecte primero al colector")
            return
        if self.app.command_queue.is_busy:
            self.app._append_log("ERR", "Hay una secuencia en curso")
            return

        self.poll_status.set("Descargando horarios (QUIT + i)…")
        self._show_schedule_text("Leyendo horarios del colector…")
        self.begin_polling_capture()

        def on_step(cmd: str, response: str) -> None:
            if cmd.strip().lower() != "i":
                return
            # Respuesta acumulada del comando i (puede ser multilínea).
            for line in response.splitlines():
                self.capture_polling_line(line)

        def _done() -> None:
            q = self.app.command_queue
            if q.was_aborted:
                self.finish_polling_capture()
                self.poll_status.set("Descarga interrumpida — vea el log")
                self.app._append_log(
                    "WARN",
                    f"Descarga no completada ({q.last_command!r} falló).",
                )
                return
            raw = self.finish_polling_capture()
            last_cmd = q.last_command.strip().lower()
            if not raw.strip() and last_cmd == "i":
                raw = q.last_response
            if not raw.strip():
                self.poll_status.set("Descarga sin respuesta")
                self.app._append_log(
                    "WARN",
                    "Descarga: sin respuesta al comando i (timeout o fallo de comunicación).",
                )
                return
            self._apply_download_result(raw)

        try:
            self.app.queue_start(
                [CMD_QUIT, POLLING_DOWNLOAD],
                wait_rx=True,
                on_step=on_step,
                on_done=_done,
            )
        except RuntimeError as exc:
            self.finish_polling_capture()
            self.app._append_log("ERR", str(exc))

    def _send_wake(self) -> None:
        if not self.app.guard_maintenance("WAKE"):
            return
        if not self.app.client.is_connected:
            self.app._append_log("ERR", "Conecte primero al colector")
            return
        self.app._append_log(
            "INFO",
            "Secuencia QUIT → WAKE (WAKE solo suele fallar si el colector no está detenido)…",
        )
        self.app._send_many([CMD_QUIT, WAKE_SLOW], wait_rx=True)

    def _send_zoom(self) -> None:
        if not self.app.guard_maintenance("ZOOM"):
            return
        if not self.app.client.is_connected:
            self.app._append_log("ERR", "Conecte primero al colector")
            return
        self.app._send_many([ZOOM_CMD], wait_rx=True)

    def _upload_schedules(self) -> None:
        if not self.app.guard_maintenance("Subir horarios"):
            return
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
            "§11: el colector no debe tener horarios previos (verifique con Descargar). "
            "El comando de insercion NO esta en Comados.doc — la app prueba I+HHMM+HHMM. "
            "Si responde NO con colector vacio, el formato no es el de este firmware (SLD16).",
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
            f"Subir: QUIT + {len(upload_cmds)} horario(s) + WAKE + ZOOM. "
            "Si responde NO, el formato I… puede ser incorrecto o ya hay horarios en el colector.",
        )

        def on_done() -> None:
            q = self.app.command_queue
            last = q.last_response.strip().upper()
            if q.was_aborted or last == "NO":
                self.poll_status.set("Subida rechazada o incompleta — vea el log")
                if q.was_aborted:
                    self.app._append_log(
                        "WARN",
                        f"Subida interrumpida ({q.last_command!r} → {last or 'sin respuesta'}).",
                    )
            else:
                self.poll_status.set("Subida finalizada — verifique con Descargar")
                self.app._append_log("INFO", "Subida terminada. Pulse Descargar para verificar.")

        self.app.queue_start(commands, on_done=on_done, wait_rx=True)

    def _startup_full(self) -> None:
        if not self.app.guard_maintenance("Forzar lecturas"):
            return
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
        self._readings: List[MeterReading] = []
        self._scan_index = 0
        self._scan_total = 0
        self._scan_active = False
        self._maint_widgets: List[Any] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        top = ctk.CTkFrame(self)
        top.grid(row=0, column=0, padx=12, pady=12, sticky="ew")
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(top, text="Medidores del colector", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=4, padx=8, pady=(8, 4), sticky="w"
        )

        self.count_var = ctk.StringVar(value="Cantidad: ?")
        ctk.CTkLabel(top, textvariable=self.count_var).grid(row=1, column=0, padx=8, pady=6, sticky="w")
        self.btn_count = ctk.CTkButton(
            top, text="Cantidad (O)", width=110, height=30, command=self._fetch_count
        )
        self.btn_count.grid(row=1, column=1, padx=4, pady=6, sticky="w")

        ctk.CTkLabel(top, text="Máx. escaneo:").grid(row=1, column=2, padx=(16, 4), pady=6, sticky="e")
        self.max_var = ctk.StringVar(value="50")
        ctk.CTkEntry(top, textvariable=self.max_var, width=60).grid(row=1, column=3, padx=4, pady=6, sticky="w")
        self.btn_scan = ctk.CTkButton(top, text="Escanear", width=100, height=30, command=self._scan_all)
        self.btn_scan.grid(row=1, column=4, padx=8, pady=6, sticky="w")

        # Lectura directa por número de medidor
        ctk.CTkLabel(top, text="Medidor:").grid(row=2, column=0, padx=8, pady=6, sticky="e")
        self.meter_id_var = ctk.StringVar(
            value=(self.app.meter_var.get().strip() if hasattr(self.app, "meter_var") else "")
        )
        self.meter_entry = ctk.CTkEntry(
            top, textvariable=self.meter_id_var, width=130, placeholder_text="ej. 24096522"
        )
        self.meter_entry.grid(row=2, column=1, padx=4, pady=6, sticky="w")
        self.meter_entry.bind("<Return>", lambda _e: self._read_by_meter_id())
        self.btn_read_meter = ctk.CTkButton(
            top, text="Leer medidor", width=110, height=30, command=self._read_by_meter_id
        )
        self.btn_read_meter.grid(row=2, column=2, padx=4, pady=6, sticky="w")

        ctk.CTkLabel(top, text="Índice:").grid(row=3, column=0, padx=8, pady=6, sticky="e")
        self.index_var = ctk.StringVar(value="0")
        ctk.CTkEntry(top, textvariable=self.index_var, width=60).grid(row=3, column=1, padx=4, pady=6, sticky="w")
        self.btn_read_idx = ctk.CTkButton(
            top, text="Leer índice", width=110, height=30, command=self._read_by_index
        )
        self.btn_read_idx.grid(row=3, column=2, padx=4, pady=6, sticky="w")
        self._maint_widgets.extend(
            [self.btn_count, self.btn_scan, self.btn_read_meter, self.btn_read_idx]
        )

        self.progress_var = ctk.StringVar(value="")
        ctk.CTkLabel(top, textvariable=self.progress_var, text_color=("gray40", "gray60")).grid(
            row=4, column=0, columnspan=5, padx=8, pady=(0, 8), sticky="w"
        )

        ctk.CTkLabel(
            self,
            text="Leer medidor = por número. Leer índice / Escanear = por posición (R0000, R0001…).",
            text_color=("gray40", "gray60"),
            wraplength=520,
        ).grid(row=1, column=0, padx=12, pady=(0, 4), sticky="w")

        header = (
            f"{'Medidor':<14} {'T1':>10} {'T2':>10} {'T3':>10} {'T4':>10} "
            f"{'Total':>10} {'Fecha':<20}\n"
            + "-" * 90 + "\n"
        )
        self.table = ctk.CTkTextbox(self, font=ctk.CTkFont(family="Consolas", size=12))
        self.table.grid(row=4, column=0, padx=12, pady=(0, 12), sticky="nsew")
        self.table.insert("1.0", header)
        self.table.configure(state="disabled")

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=5, column=0, padx=12, pady=(0, 12), sticky="ew")
        ctk.CTkButton(actions, text="Limpiar", width=90, height=30, command=self._clear_table).pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkButton(actions, text="Cancelar escaneo", width=140, height=30, command=self._cancel_scan).pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkButton(
            actions,
            text="Exportar Excel",
            width=130,
            height=30,
            command=self._export_excel,
            fg_color=("#2B7A4B", "#1E5A35"),
            hover_color=("#236B40", "#174A2B"),
        ).pack(side="left")

    def set_maintenance_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for w in self._maint_widgets:
            try:
                w.configure(state=state)
            except Exception:
                pass

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
        self._readings.append(reading)
        line = (
            f"{reading.meter_id:<14} {self._fmt(reading.t1)} {self._fmt(reading.t2)} "
            f"{self._fmt(reading.t3)} {self._fmt(reading.t4)} {self._fmt(reading.display_total)} "
            f"{reading.datetime_text:<20}\n"
        )
        self.table.configure(state="normal")
        self.table.insert("end", line)
        self.table.see("end")
        self.table.configure(state="disabled")
        self.app._append_log("INFO", format_meter_reading_summary(reading))

    def _export_excel(self) -> None:
        if not self._readings:
            self.app._append_log("ERR", "No hay lecturas para exportar. Escanee o lea medidores primero.")
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"medidores_{stamp}.xlsx"
        path = filedialog.asksaveasfilename(
            title="Exportar medidores a Excel",
            defaultextension=".xlsx",
            initialfile=default_name,
            filetypes=[("Excel", "*.xlsx"), ("Todos", "*.*")],
        )
        if not path:
            return
        source = self.app._connection_status_text() if hasattr(self.app, "_connection_status_text") else ""
        count_txt = self.count_var.get()
        try:
            out = export_meters_xlsx(
                path,
                self._readings,
                source=source,
                collector_count=count_txt,
            )
            self.progress_var.set(f"Excel exportado: {len(self._readings)} filas")
            self.app._append_log("INFO", f"Excel guardado: {out}")
        except Exception as exc:
            self.app._append_log("ERR", f"No se pudo exportar Excel: {exc}")

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
        if not self.app.guard_maintenance("Cantidad (O)"):
            return
        if not self.app.client.is_connected:
            self.app._append_log("ERR", "Conecte primero al colector")
            return
        self.app._send_many(
            [CMD_QUIT, QueuedCommand(CMD_COUNT_METERS, multiline_ms=2000)],
            wait_rx=True,
        )

    def _sync_meter_to_app(self, meter: str) -> None:
        """Mantiene el campo Medidor de Comandos alineado al de esta pestaña."""
        if hasattr(self.app, "meter_var"):
            self.app.meter_var.set(meter)

    def _read_by_meter_id(self) -> None:
        if not self.app.guard_maintenance("Leer medidor"):
            return
        if not self.app.client.is_connected:
            self.app._append_log("ERR", "Conecte primero al colector")
            return
        if self.app.command_queue.is_busy:
            self.app._append_log("ERR", "Hay una secuencia en curso")
            return
        meter = self.meter_id_var.get().strip()
        if not meter:
            self.app._append_log("ERR", "Indique el número de medidor")
            return
        try:
            cmd = cmd_multitariff_read(meter)
        except ValueError as exc:
            self.app._append_log("ERR", str(exc))
            return

        self._sync_meter_to_app(meter)
        self.progress_var.set(f"Leyendo medidor {meter}…")
        self._scan_active = True
        got = [False]

        def on_step(_cmd: str, rx: str) -> None:
            if not _cmd.strip().upper().startswith("R"):
                return
            if parse_meter_reading(rx):
                got[0] = True
                self.on_meter_rx(rx)

        def on_done() -> None:
            self._scan_active = False
            q = self.app.command_queue
            if got[0]:
                self.progress_var.set(f"Lectura OK — medidor {meter}")
                return
            last = q.last_response.strip().upper()
            self.progress_var.set(f"Sin lectura — medidor {meter}")
            self.app._append_log(
                "WARN",
                f"No se obtuvo lectura del medidor {meter} "
                f"({q.last_command!r} → {last or 'sin respuesta'}).",
            )

        try:
            self.app.queue_start(
                [CMD_QUIT, QueuedCommand(cmd, wait_rx=True, rx_timeout_ms=12000, write_timeout_s=2.0)],
                wait_rx=True,
                on_step=on_step,
                on_done=on_done,
            )
        except RuntimeError as exc:
            self.app._append_log("ERR", str(exc))

    def _read_by_index(self) -> None:
        if not self.app.guard_maintenance("Leer índice"):
            return
        if not self.app.client.is_connected:
            self.app._append_log("ERR", "Conecte primero al colector")
            return
        if self.app.command_queue.is_busy:
            self.app._append_log("ERR", "Hay una secuencia en curso")
            return
        try:
            index = int(self.index_var.get().strip())
        except ValueError:
            self.app._append_log("ERR", "Índice debe ser un número (0, 1, 2…)")
            return
        if index < 0 or index > 9999:
            self.app._append_log("ERR", "Índice fuera de rango (0-9999)")
            return

        cmd = cmd_read_by_index(index, READ_FLAGS_MULTITARIFF)
        self.progress_var.set(f"Leyendo índice {index}…")
        self._scan_active = True
        got = [False]

        def on_step(_cmd: str, rx: str) -> None:
            if not _cmd.strip().upper().startswith("R"):
                return
            if parse_meter_reading(rx):
                got[0] = True
                self.on_meter_rx(rx)

        def on_done() -> None:
            self._scan_active = False
            q = self.app.command_queue
            if got[0]:
                self.progress_var.set(f"Lectura OK — índice {index}")
                return
            last = q.last_response.strip().upper()
            self.progress_var.set(f"Sin lectura — índice {index}")
            self.app._append_log(
                "WARN",
                f"No se obtuvo lectura del índice {index} "
                f"({q.last_command!r} → {last or 'sin respuesta'}).",
            )

        try:
            self.app.queue_start(
                [CMD_QUIT, QueuedCommand(cmd, wait_rx=True, rx_timeout_ms=12000, write_timeout_s=2.0)],
                wait_rx=True,
                on_step=on_step,
                on_done=on_done,
            )
        except RuntimeError as exc:
            self.app._append_log("ERR", str(exc))

    def _scan_all(self) -> None:
        if not self.app.guard_maintenance("Escanear"):
            return
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
        ok_reads = [0]
        self.progress_var.set(f"Escaneando 0/{total}…")

        def on_step(_cmd: str, rx: str) -> None:
            self._scan_index += 1
            self.progress_var.set(f"Escaneando {self._scan_index}/{total}…")
            if parse_meter_reading(rx):
                ok_reads[0] += 1
                self.on_meter_rx(rx)

        def on_done() -> None:
            self._scan_active = False
            q = self.app.command_queue
            if q.was_aborted:
                self.progress_var.set(f"Escaneo detenido: {ok_reads[0]}/{total} lecturas OK")
                self.app._append_log(
                    "WARN",
                    f"Escaneo interrumpido en índice {self._scan_index}/{total} "
                    f"({q.last_command!r} → {q.last_response.strip().upper() or 'sin respuesta'}). "
                    f"Lecturas válidas: {ok_reads[0]}.",
                )
            elif ok_reads[0] == 0:
                self.progress_var.set("Sin lecturas válidas")
                self.app._append_log(
                    "WARN",
                    f"Escaneo completado sin lecturas válidas (0/{total}). "
                    "Login → QUIT y reintente.",
                )
            else:
                self.progress_var.set(f"Listo: {ok_reads[0]} lecturas")

        self.app.queue_start(commands, on_step=on_step, on_done=on_done, wait_rx=True)

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
        self._maint_widgets: List[Any] = []

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
        ctk.CTkButton(btn_row, text="Archivo…", width=100, height=30, command=self._load_file).pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkButton(btn_row, text="Vista previa", width=110, height=30, command=self._preview).pack(
            side="left", padx=8
        )
        self.btn_execute = ctk.CTkButton(
            btn_row, text="Ejecutar", width=100, height=30, command=self._execute
        )
        self.btn_execute.pack(side="left", padx=8)
        self._maint_widgets.append(self.btn_execute)

        self.preview_box = ctk.CTkTextbox(self, font=ctk.CTkFont(family="Consolas", size=12))
        self.preview_box.grid(row=4, column=0, padx=12, pady=(4, 12), sticky="nsew")
        self.preview_box.configure(state="disabled")

        self.status_var = ctk.StringVar(value="")
        ctk.CTkLabel(self, textvariable=self.status_var, text_color=("gray40", "gray60")).grid(
            row=5, column=0, padx=12, pady=(0, 12), sticky="w"
        )

    def set_maintenance_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for w in self._maint_widgets:
            try:
                w.configure(state=state)
            except Exception:
                pass

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
        if not self.app.guard_maintenance("Carga masiva"):
            return
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
        # Usar timeouts largos en WAKE/ZOOM (Comados.doc: QUIT → A… → WAKE → ZOOM)
        queued = []
        for c in commands:
            if c == CMD_WAKE:
                queued.append(WAKE_SLOW)
            elif c == CMD_ZOOM:
                queued.append(ZOOM_CMD)
            else:
                queued.append(QueuedCommand(c, wait_rx=True, rx_timeout_ms=10000, write_timeout_s=2.0))
        self.status_var.set(f"Enviando {len(queued)} comandos…")

        def on_done() -> None:
            q = self.app.command_queue
            if q.was_aborted:
                self.status_var.set(
                    f"Carga interrumpida ({q.last_command!r} → "
                    f"{q.last_response.strip().upper() or 'sin respuesta'})"
                )
                self.app._append_log(
                    "WARN",
                    f"Carga masiva interrumpida. Medidores enviados: hasta {len(valid)} "
                    f"(verifique en el colector con O).",
                )
            else:
                self.status_var.set(f"Carga finalizada ({len(valid)} medidores)")

        self.app.queue_start(queued, on_done=on_done, wait_rx=True)
