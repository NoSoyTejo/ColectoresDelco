"""Ventana principal: puertos COM, conectar, log y comandos del colector."""

from __future__ import annotations

import json
import queue
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import customtkinter as ctk

from app_paths import logs_dir, settings_path
from command_queue import QueuedCommand, SerialCommandQueue
from session_log import SessionLog
from serial_client import SerialClient, describe_ports, list_serial_ports
from ui.extra_tabs import BulkLoadTab, ClockTab, MetersTab
from protocol import (
    CMD_COUNT_METERS,
    CMD_DELETE_BASE,
    CMD_LOGIN,
    CMD_POLLING_SCHEDULE,
    CMD_QUIT,
    CMD_VERSION,
    CMD_WAKE,
    CMD_ZOOM,
    cmd_add_meter,
    cmd_delete_meter,
    cmd_direct_read,
    cmd_display,
    cmd_forced_refresh,
    cmd_multitariff_read,
    cmd_restart_reading,
    describe_lock_state,
    describe_status_response,
    format_command,
    format_meter_reading_summary,
    is_clock_only_reading,
    is_lock_response,
    is_unlock_response,
    parse_collector_clock,
    parse_collector_info,
    parse_direct_read_response,
    parse_meter_reading,
)


def _settings_path() -> Path:
    return settings_path()


def load_settings() -> Dict[str, Any]:
    path = _settings_path()
    defaults: Dict[str, Any] = {
        "port": "",
        "baudrate": 9600,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1,
        "timeout": 1.0,
        "line_ending": "\r\n",
        "last_meter": "",
        "last_header": "",
    }
    if not path.exists():
        defaults_copy = dict(defaults)
        save_settings(defaults_copy)
        return defaults
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        defaults.update(data)
    except (OSError, json.JSONDecodeError):
        pass
    return defaults


def save_settings(settings: Dict[str, Any]) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


class AppWindow(ctk.CTk):
    """Aplicación de escritorio Proyecto Colectores."""

    BAUD_OPTIONS = ["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200"]
    ENDING_OPTIONS = {
        "CR+LF (\\r\\n)": "\r\n",
        "CR (\\r)": "\r",
        "LF (\\n)": "\n",
        "Ninguno": "",
    }

    def __init__(self) -> None:
        super().__init__()
        self.title("Proyecto Colectores")
        self.geometry("1180x860")
        self.minsize(1000, 720)

        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self.settings = load_settings()
        self._ui_queue: queue.Queue = queue.Queue()
        self._line_ending_label = self._ending_label_for(self.settings.get("line_ending", "\r\n"))

        self.client = SerialClient(
            on_data=self._on_serial_data,
            on_error=self._on_serial_error,
            on_status=self._on_serial_status,
        )
        self.session_log = SessionLog()
        self.command_queue = SerialCommandQueue(
            send_command=lambda cmd, wt=0.0: self._send_raw(cmd, write_timeout_s=wt),
            on_log=self._append_log,
            pause_ms=500,
            rx_timeout_ms=15000,
            schedule=lambda ms, fn: self.after(ms, fn),
        )
        self._last_error_msg = ""
        self._auto_login_pending = False

        self._build_ui()
        self._refresh_ports()
        self._apply_settings_to_ui()
        self._set_connected_ui(False)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._process_ui_queue)

    def _ending_label_for(self, ending: str) -> str:
        for label, value in self.ENDING_OPTIONS.items():
            if value == ending:
                return label
        return "CR+LF (\\r\\n)"

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self.grid_rowconfigure(4, weight=1)

        header = ctk.CTkLabel(
            self,
            text="Proyecto Colectores",
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        header.grid(row=0, column=0, padx=16, pady=(16, 4), sticky="w")

        title_row = ctk.CTkFrame(self, fg_color="transparent")
        title_row.grid(row=1, column=0, padx=16, pady=(0, 8), sticky="ew")
        title_row.grid_columnconfigure(0, weight=1)

        subtitle = ctk.CTkLabel(
            title_row,
            text="Comandos según documentación Delco (lectura, login, medidores, K, etc.)",
            font=ctk.CTkFont(size=13),
            text_color=("gray30", "gray70"),
            anchor="w",
        )
        subtitle.grid(row=0, column=0, sticky="w")

        ctk.CTkButton(title_row, text="Qué hace cada cosa", width=160, command=self._show_help).grid(
            row=0, column=1, padx=(8, 0)
        )

        # --- Conexión ---
        conn = ctk.CTkFrame(self)
        conn.grid(row=2, column=0, padx=16, pady=8, sticky="ew")
        for i in range(8):
            conn.grid_columnconfigure(i, weight=0)
        conn.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(conn, text="Puerto COM").grid(row=0, column=0, padx=(12, 4), pady=12, sticky="w")
        self.port_var = ctk.StringVar(value="")
        self.port_menu = ctk.CTkOptionMenu(conn, variable=self.port_var, values=["(sin puertos)"])
        self.port_menu.grid(row=0, column=1, padx=4, pady=12, sticky="ew")

        self.btn_refresh = ctk.CTkButton(conn, text="Refrescar", width=90, command=self._refresh_ports)
        self.btn_refresh.grid(row=0, column=2, padx=4, pady=12)

        ctk.CTkLabel(conn, text="Velocidad").grid(row=0, column=3, padx=(12, 4), pady=12)
        self.baud_var = ctk.StringVar(value="9600")
        self.baud_menu = ctk.CTkOptionMenu(conn, variable=self.baud_var, values=self.BAUD_OPTIONS, width=100)
        self.baud_menu.grid(row=0, column=4, padx=4, pady=12)

        ctk.CTkLabel(conn, text="Fin de línea").grid(row=0, column=5, padx=(12, 4), pady=12)
        self.ending_var = ctk.StringVar(value=self._line_ending_label)
        self.ending_menu = ctk.CTkOptionMenu(
            conn,
            variable=self.ending_var,
            values=list(self.ENDING_OPTIONS.keys()),
            width=140,
        )
        self.ending_menu.grid(row=0, column=6, padx=4, pady=12)

        self.btn_connect = ctk.CTkButton(conn, text="Conectar", width=110, command=self._toggle_connect)
        self.btn_connect.grid(row=0, column=7, padx=(8, 4), pady=12)
        self.btn_cancel_seq = ctk.CTkButton(
            conn,
            text="Cancelar secuencia",
            width=140,
            fg_color=("gray70", "gray35"),
            command=self._cancel_sequence,
        )
        self.btn_cancel_seq.grid(row=0, column=8, padx=(4, 12), pady=12)

        self.status_var = ctk.StringVar(value="Desconectado — el colector no está en uso")
        ctk.CTkLabel(conn, textvariable=self.status_var, anchor="w").grid(
            row=1, column=0, columnspan=9, padx=12, pady=(0, 10), sticky="ew"
        )

        # --- Pestañas ---
        self.tabview = ctk.CTkTabview(self)
        self.tabview.grid(row=3, column=0, padx=16, pady=8, sticky="nsew")

        tab_cmds = self.tabview.add("Comandos")
        tab_clock = self.tabview.add("Reloj")
        tab_meters = self.tabview.add("Medidores")
        tab_bulk = self.tabview.add("Carga masiva")

        tab_cmds.grid_columnconfigure(0, weight=1)
        tab_cmds.grid_rowconfigure(0, weight=1)

        cmds = ctk.CTkScrollableFrame(tab_cmds, label_text="Comandos según Comados.doc")
        cmds.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        cmds.grid_columnconfigure(0, weight=1)
        cmds.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(cmds, text="Medidor (hasta 12 dígitos)").grid(
            row=0, column=0, columnspan=2, padx=8, pady=(8, 2), sticky="w"
        )
        self.meter_var = ctk.StringVar(value=str(self.settings.get("last_meter") or ""))
        ctk.CTkEntry(cmds, textvariable=self.meter_var, placeholder_text="ej. 23388410").grid(
            row=1, column=0, columnspan=2, padx=8, pady=(0, 8), sticky="ew"
        )

        ctk.CTkLabel(cmds, text="Cabecera (solo al agregar medidor con cabezal)").grid(
            row=2, column=0, columnspan=2, padx=8, pady=(4, 2), sticky="w"
        )
        self.header_var = ctk.StringVar(value=str(self.settings.get("last_header") or ""))
        ctk.CTkEntry(cmds, textvariable=self.header_var, placeholder_text="ej. 90059613").grid(
            row=3, column=0, columnspan=2, padx=8, pady=(0, 8), sticky="ew"
        )

        # §5, §9 — Login y flujo base
        ctk.CTkLabel(cmds, text="1. Flujo base (QUIT / Login / WAKE / ZOOM)", font=ctk.CTkFont(weight="bold")).grid(
            row=4, column=0, columnspan=2, padx=8, pady=(8, 4), sticky="w"
        )
        self._cmd_btn(cmds, 5, 0, "QUIT (detener)", lambda: self._send_one(CMD_QUIT))
        self._cmd_btn(cmds, 5, 1, "Login PWR666666", lambda: self._send_one(CMD_LOGIN))
        self._cmd_btn(cmds, 6, 0, "QUIT→WAKE→ZOOM", self._do_quit_wake_zoom)
        self._cmd_btn(cmds, 6, 1, "Diagnóstico VER+O", self._do_quick_diag)

        # §1 lectura, §2 refresco
        ctk.CTkLabel(cmds, text="2. Lectura y refresco (§1–§2)", font=ctk.CTkFont(weight="bold")).grid(
            row=7, column=0, columnspan=2, padx=8, pady=(12, 4), sticky="w"
        )
        self._cmd_btn(cmds, 8, 0, "Lectura R…F031812", self._do_direct_read)
        self._cmd_btn(cmds, 8, 1, "Refresco SGXF…", self._do_refresh)
        self._cmd_btn(cmds, 9, 0, "Lectura T1–T4", self._do_multitariff_read)

        # §3–§4 medidores
        ctk.CTkLabel(cmds, text="3. Medidores (§3–§4, §13)", font=ctk.CTkFont(weight="bold")).grid(
            row=10, column=0, columnspan=2, padx=8, pady=(12, 4), sticky="w"
        )
        self._cmd_btn(cmds, 11, 0, "Agregar A…", self._do_add_meter)
        self._cmd_btn(cmds, 11, 1, "Borrar E…", self._do_delete_meter)
        self._cmd_btn(cmds, 12, 0, "Agregar CP4 (A…00)", self._do_add_individual)
        self._cmd_btn(cmds, 12, 1, "DEL (borrar base)", self._do_delete_base)

        # §6–§8 info
        ctk.CTkLabel(cmds, text="4. Información (§6–§8)", font=ctk.CTkFont(weight="bold")).grid(
            row=13, column=0, columnspan=2, padx=8, pady=(12, 4), sticky="w"
        )
        self._cmd_btn(cmds, 14, 0, "VER (versión)", lambda: self._send_one(CMD_VERSION))
        self._cmd_btn(cmds, 14, 1, "O (cant. medidores)", self._do_count_meters)
        self._cmd_btn(cmds, 15, 0, "K k=10100000", lambda: self._send_one(cmd_restart_reading()))
        self._cmd_btn(cmds, 15, 1, "i (horarios) → pestaña Reloj", lambda: self.tabview.set("Reloj"))

        note = ctk.CTkLabel(
            cmds,
            text="Según Comados.doc: casi todo usa QUIT → comando → WAKE → ZOOM. "
            "Cabecera/display están obsoletos. Horarios y reloj: pestaña Reloj.",
            wraplength=420,
            justify="left",
            text_color=("gray40", "gray60"),
        )
        note.grid(row=16, column=0, columnspan=2, padx=8, pady=(12, 8), sticky="ew")

        ctk.CTkButton(
            cmds,
            text="Abrir carpeta de logs",
            command=self._open_logs_folder,
        ).grid(row=17, column=0, columnspan=2, padx=8, pady=(0, 8), sticky="ew")

        self.clock_tab = ClockTab(tab_clock, self)
        self.clock_tab.grid(row=0, column=0, sticky="nsew")
        tab_clock.grid_columnconfigure(0, weight=1)
        tab_clock.grid_rowconfigure(0, weight=1)
        self.meters_tab = MetersTab(tab_meters, self)
        self.meters_tab.pack(fill="both", expand=True)
        self.bulk_tab = BulkLoadTab(tab_bulk, self)
        self.bulk_tab.pack(fill="both", expand=True)

        # --- Log compartido ---
        log_frame = ctk.CTkFrame(self)
        log_frame.grid(row=4, column=0, padx=16, pady=8, sticky="nsew")
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            log_frame,
            text="Historial (TX = enviado, RX = respuesta)",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, padx=12, pady=(10, 4), sticky="w")
        self.log_box = ctk.CTkTextbox(log_frame, font=ctk.CTkFont(family="Consolas", size=13))
        self.log_box.grid(row=1, column=0, padx=12, pady=(0, 12), sticky="nsew")
        self.log_box.configure(state="disabled")

        # --- Envío libre ---
        send_frame = ctk.CTkFrame(self)
        send_frame.grid(row=5, column=0, padx=16, pady=(0, 14), sticky="ew")
        send_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(send_frame, text="Comando libre").grid(row=0, column=0, padx=(12, 4), pady=12)
        self.cmd_var = ctk.StringVar(value="")
        self.cmd_entry = ctk.CTkEntry(send_frame, textvariable=self.cmd_var)
        self.cmd_entry.grid(row=0, column=1, padx=4, pady=12, sticky="ew")
        self.cmd_entry.bind("<Return>", lambda _e: self._send_command())

        self.btn_send = ctk.CTkButton(send_frame, text="Enviar", width=100, command=self._send_command)
        self.btn_send.grid(row=0, column=2, padx=4, pady=12)

        self.btn_clear = ctk.CTkButton(send_frame, text="Limpiar historial", width=130, command=self._clear_log)
        self.btn_clear.grid(row=0, column=3, padx=(4, 12), pady=12)

    def _cmd_btn(self, parent: Any, row: int, col: int, text: str, command: Any) -> None:
        ctk.CTkButton(parent, text=text, command=command).grid(
            row=row, column=col, padx=6, pady=4, sticky="ew"
        )

    def _apply_settings_to_ui(self) -> None:
        baud = str(self.settings.get("baudrate", 9600))
        if baud in self.BAUD_OPTIONS:
            self.baud_var.set(baud)
        ending = self.settings.get("line_ending", "\r\n")
        self.ending_var.set(self._ending_label_for(ending))
        saved_port = self.settings.get("port") or ""
        if saved_port and saved_port in list_serial_ports():
            for label in self.port_menu.cget("values"):
                if label.startswith(saved_port):
                    self.port_var.set(label)
                    break

    def _selected_port(self) -> Optional[str]:
        value = self.port_var.get().strip()
        if not value or value.startswith("("):
            return None
        return value.split(" — ", 1)[0].strip()

    def _current_line_ending(self) -> str:
        return self.ENDING_OPTIONS.get(self.ending_var.get(), "\r\n")

    def _refresh_ports(self) -> None:
        labels = describe_ports()
        if not labels:
            labels = ["(sin puertos)"]
            self.port_var.set(labels[0])
        self.port_menu.configure(values=labels)
        current = self.port_var.get()
        if current not in labels:
            self.port_var.set(labels[0])
        self._append_log("INFO", f"Puertos: {', '.join(list_serial_ports()) or 'ninguno'}")

    def _toggle_connect(self) -> None:
        if self.client.is_connected:
            if self.command_queue.is_busy:
                self.command_queue.cancel()
            self.client.disconnect()
            self._append_log("INFO", "Desconectado")
            return

        port = self._selected_port()
        if not port:
            self._append_log("ERR", "Seleccione un puerto COM válido")
            return

        baud = int(self.baud_var.get())
        try:
            self.client.connect(
                port=port,
                baudrate=baud,
                bytesize=int(self.settings.get("bytesize", 8)),
                parity=str(self.settings.get("parity", "N")),
                stopbits=float(self.settings.get("stopbits", 1)),
                timeout=0.2,
            )
        except ConnectionError as exc:
            self._append_log("ERR", str(exc))
            return

        self.settings["port"] = port
        self.settings["baudrate"] = baud
        self.settings["line_ending"] = self._current_line_ending()
        save_settings(self.settings)
        self._last_error_msg = ""
        self._append_log("INFO", f"Conectado a {port} @ {baud} baud")
        self.after(400, self._send_initial_login)

    def _persist_meter_fields(self) -> None:
        self.settings["last_meter"] = self.meter_var.get().strip()
        self.settings["last_header"] = self.header_var.get().strip()
        save_settings(self.settings)

    def _require_meter(self) -> Optional[str]:
        meter = self.meter_var.get().strip()
        if not meter:
            self._append_log("ERR", "Indique el número de medidor")
            return None
        self._persist_meter_fields()
        return meter

    def _cancel_sequence(self) -> None:
        if self.command_queue.is_busy:
            self.command_queue.cancel()
            self._append_log("INFO", "Secuencia cancelada por el usuario")
        else:
            self._append_log("INFO", "No hay secuencia en curso")

    def _send_initial_login(self) -> None:
        if not self.client.is_connected or self.command_queue.is_busy:
            return
        try:
            self._append_log("INFO", "Login inicial PWR666666…")
            self._auto_login_pending = True
            self._send_raw(CMD_LOGIN)
        except ConnectionError:
            self._auto_login_pending = False

    def _auto_login_on_lock(self) -> None:
        if not self.client.is_connected or self._auto_login_pending or self.command_queue.is_busy:
            return
        try:
            self._append_log("INFO", "Login automático PWR666666…")
            self._auto_login_pending = True
            self._send_raw(CMD_LOGIN)
        except ConnectionError:
            self._auto_login_pending = False

    def _send_raw(self, command: str, write_timeout_s: float = 0.0) -> bool:
        if not self.client.is_connected:
            self._append_log("ERR", "No hay conexión activa")
            return False
        ending = self._current_line_ending()
        try:
            timeout = write_timeout_s if write_timeout_s > 0 else None
            self.client.send(format_command(command, ending), write_timeout=timeout)
            shown = command.replace("\r", "\\r").replace("\n", "\\n")
            self._append_log("TX", shown)
            return True
        except ConnectionError as exc:
            self._append_log("ERR", str(exc))
            self.command_queue.on_send_failed()
            self._on_connection_lost(str(exc))
            return False

    def _on_connection_lost(self, reason: str) -> None:
        if reason == self._last_error_msg:
            return
        self._last_error_msg = reason
        if self.command_queue.is_busy:
            self.command_queue.cancel()
        self._append_log(
            "INFO",
            "Puerto COM perdido o bloqueado. Desenchufe el USB, espere 5 s, "
            "pulse Refrescar y vuelva a Conectar. Cierre RemoteCOM si está abierto.",
        )

    def _send_one(self, command: str) -> None:
        if self.command_queue.is_busy:
            self._append_log("ERR", "Hay una secuencia en curso. Espere o pulse Desconectar.")
            return
        self.cmd_var.set(command)
        self._send_command()

    def _send_many(
        self,
        commands: Iterable[str],
        pause_ms: int = 500,
        wait_rx: bool = False,
        on_done: Optional[Any] = None,
    ) -> None:
        cmds = [c for c in commands if c]
        if not cmds:
            return
        if not self.client.is_connected:
            self._append_log("ERR", "No hay conexión activa")
            return
        if self.command_queue.is_busy:
            self._append_log("ERR", "Hay una secuencia en curso. Espere o pulse Desconectar.")
            return
        try:
            self.command_queue.start(cmds, wait_rx=wait_rx, on_done=on_done)
        except RuntimeError as exc:
            self._append_log("ERR", str(exc))

    def _send_command(self) -> None:
        text = self.cmd_var.get().strip()
        if not text:
            return
        if not self.client.is_connected:
            self._append_log("ERR", "No hay conexión activa")
            return
        ending = self._current_line_ending()
        try:
            raw = format_command(text, ending)
            self.client.send(raw)
            shown = text.replace("\r", "\\r").replace("\n", "\\n")
            self._append_log("TX", shown)
        except ConnectionError as exc:
            self._append_log("ERR", str(exc))
            self.command_queue.on_send_failed()
            self._on_connection_lost(str(exc))

    def _do_direct_read(self) -> None:
        meter = self._require_meter()
        if not meter:
            return
        try:
            self._send_one(cmd_direct_read(meter))
        except ValueError as exc:
            self._append_log("ERR", str(exc))

    def _do_refresh(self) -> None:
        meter = self._require_meter()
        if not meter:
            return
        try:
            self._send_many([CMD_QUIT, cmd_forced_refresh(meter), CMD_WAKE, CMD_ZOOM])
        except ValueError as exc:
            self._append_log("ERR", str(exc))

    def _do_display(self, on: bool) -> None:
        meter = self._require_meter()
        if not meter:
            return
        try:
            self._send_many([CMD_QUIT, cmd_display(meter, on), CMD_WAKE, CMD_ZOOM])
        except ValueError as exc:
            self._append_log("ERR", str(exc))

    def _do_add_meter(self) -> None:
        meter = self._require_meter()
        if not meter:
            return
        header = self.header_var.get().strip()
        if not header:
            self._append_log("ERR", "Para agregar con cabecera indique cabecera, o use Agregar CP4")
            return
        try:
            self._send_many([CMD_QUIT, cmd_add_meter(meter, header), CMD_WAKE, CMD_ZOOM])
        except ValueError as exc:
            self._append_log("ERR", str(exc))

    def _do_add_individual(self) -> None:
        meter = self._require_meter()
        if not meter:
            return
        try:
            self._send_many([CMD_QUIT, cmd_add_meter(meter, individual_tariff=True), CMD_WAKE, CMD_ZOOM])
        except ValueError as exc:
            self._append_log("ERR", str(exc))

    def _do_delete_meter(self) -> None:
        meter = self._require_meter()
        if not meter:
            return
        try:
            self._send_many([CMD_QUIT, cmd_delete_meter(meter), CMD_WAKE, CMD_ZOOM])
        except ValueError as exc:
            self._append_log("ERR", str(exc))

    def _do_multitariff_read(self) -> None:
        meter = self._require_meter()
        if not meter:
            return
        try:
            self._send_one(cmd_multitariff_read(meter))
        except ValueError as exc:
            self._append_log("ERR", str(exc))

    def _do_quit_wake_zoom(self) -> None:
        """§9 Comados.doc: forzar actualización de lecturas."""
        from ui.extra_tabs import WAKE_SLOW, ZOOM_CMD

        self._append_log("INFO", "Forzar lecturas: QUIT → WAKE → ZOOM")
        self._send_many([CMD_QUIT, WAKE_SLOW, ZOOM_CMD], wait_rx=True)

    def _do_count_meters(self) -> None:
        """§7 Comados.doc: QUIT → O."""
        self._send_many(
            [CMD_QUIT, QueuedCommand(CMD_COUNT_METERS, multiline_ms=2000, rx_timeout_ms=20000)],
            wait_rx=True,
        )

    def _do_delete_base(self) -> None:
        """§13 Comados.doc: QUIT (OK) → DEL."""
        self._append_log("WARN", "Borrar base: QUIT → DEL (elimina todos los medidores)")
        self._send_many([CMD_QUIT, CMD_DELETE_BASE], wait_rx=True)

    def _do_quick_diag(self) -> None:
        """Versión + estado del colector (comandos seguros de consulta)."""
        if not self.client.is_connected:
            self._append_log("ERR", "Conecte primero al colector")
            return
        self._send_many([CMD_VERSION, CMD_COUNT_METERS], wait_rx=True)

    def _open_logs_folder(self) -> None:
        import os

        folder = str(logs_dir())
        os.startfile(folder)  # noqa: S606 — Windows only
        self._append_log("INFO", f"Carpeta de logs: {folder}")

    def _clear_log(self) -> None:
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _append_log(self, kind: str, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {kind:<3} {message}\n"
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        self.session_log.write(kind, message)

    def _on_serial_data(self, text: str) -> None:
        self._ui_queue.put(("data", text))

    def _on_serial_error(self, message: str) -> None:
        self._ui_queue.put(("error", message))

    def _on_serial_status(self, connected: bool) -> None:
        self._ui_queue.put(("status", connected))

    def _process_ui_queue(self) -> None:
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if kind == "data":
                    text = payload.rstrip("\r\n")
                    self._append_log("RX", text)
                    if is_lock_response(text):
                        tip = describe_lock_state(text)
                        if tip:
                            self._append_log("INFO", tip)
                        self._auto_login_on_lock()
                        continue
                    if is_unlock_response(text):
                        self._auto_login_pending = False
                        tip = describe_lock_state(text)
                        if tip:
                            self._append_log("INFO", tip)
                        self.command_queue.on_unlock()
                        continue
                    self.command_queue.on_rx(text)
                    tip = describe_status_response(text, self.command_queue.last_command)
                    if tip:
                        self._append_log("WARN", tip)
                    if parse_collector_info(text):
                        self.meters_tab.on_collector_info(text)
                    self.clock_tab.on_rx(text)
                    meter = parse_meter_reading(text)
                    if meter and not self.meters_tab._scan_active and not is_clock_only_reading(meter):
                        self.meters_tab.on_meter_rx(text)
                    parsed = parse_direct_read_response(text)
                    if parsed and parsed.reading_value is not None:
                        parts = [f"Lectura={parsed.reading_value}"]
                        if parsed.datetime_text:
                            parts.append(f"Fecha={parsed.datetime_text}")
                        if parsed.header:
                            parts.append(f"Cabecera={parsed.header}")
                        self._append_log("INFO", " | ".join(parts))
                elif kind == "error":
                    self._append_log("ERR", str(payload))
                elif kind == "status":
                    connected = bool(payload)
                    if not connected:
                        if self.command_queue.is_busy:
                            self.command_queue.cancel()
                        self._last_error_msg = ""
                    self._set_connected_ui(connected)
        except queue.Empty:
            pass
        self.after(100, self._process_ui_queue)

    def _set_connected_ui(self, connected: bool) -> None:
        if connected:
            self.status_var.set("Conectado — canal abierto (recuerde desconectar al terminar)")
            self.btn_connect.configure(text="Desconectar")
            self.port_menu.configure(state="disabled")
            self.baud_menu.configure(state="disabled")
        else:
            self.status_var.set("Desconectado — enchufe USB y pulse Conectar")
            self.btn_connect.configure(text="Conectar")
            self.port_menu.configure(state="normal")
            self.baud_menu.configure(state="normal")

    def _show_help(self) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title("Qué hace cada cosa")
        dialog.geometry("620x580")
        dialog.transient(self)
        dialog.grab_set()

        text = ctk.CTkTextbox(dialog, wrap="word", font=ctk.CTkFont(size=13))
        text.pack(fill="both", expand=True, padx=12, pady=12)
        text.insert(
            "1.0",
            "Según Comados.doc (manual de comandos del colector)\n\n"
            "Flujo habitual: QUIT → comando → WAKE → ZOOM.\n"
            "Si responde lock → Login PWR666666 (unlock).\n\n"
            "§1 Lectura R…F031812 — lectura (F18) + fecha (F12). Cabecera/display obsoletos.\n"
            "§2 Refresco SGXF — QUIT → SGXF+medidor → WAKE → ZOOM.\n"
            "§3 Borrar E… / §4 Agregar A… / CP4 A…00.\n"
            "§5 Login PWR666666.\n"
            "§6 VER — CCE16=v1, SLD16=v2.\n"
            "§7 O — cantidad de medidores (IDnum, AMRsw…).\n"
            "§8 K k=10100000 — AMRsw debe quedar 10100000.\n"
            "§9 QUIT→WAKE→ZOOM — forzar lecturas.\n"
            "§10–§11 Horarios — pestaña Reloj (comando i / Upload).\n"
            "§12 Reloj C… — Sync System Time en pestaña Reloj.\n"
            "§13 DEL — borrar toda la base (cuidado).\n\n"
            "Medidores — T1–T4 (extensión RemoteCOM).\n"
            "Carga masiva — varios A… en lote.\n"
            "Al terminar: Desconectar y retirar USB.\n",
        )
        text.configure(state="disabled")
        ctk.CTkButton(dialog, text="Cerrar", width=100, command=dialog.destroy).pack(pady=(0, 12))

    def _on_close(self) -> None:
        try:
            self._persist_meter_fields()
            if self.client.is_connected:
                self.client.disconnect()
            self.session_log.close()
        finally:
            self.destroy()


def run_app() -> None:
    app = AppWindow()
    app.mainloop()


if __name__ == "__main__":
    src = Path(__file__).resolve().parents[1]
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    run_app()
