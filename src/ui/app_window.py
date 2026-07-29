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
from tcp_client import DEFAULT_TCP_PORT, TcpClient
from ui.extra_tabs import BulkLoadTab, ClockTab, MetersTab
from protocol import (
    AMRSW_OK,
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
    amrsw_needs_k,
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
        "connection_mode": "com",
        "tcp_host": "",
        "tcp_port": DEFAULT_TCP_PORT,
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
        "CR+LF": "\r\n",
        "CR": "\r",
        "LF": "\n",
        "Ninguno": "",
    }

    def __init__(self) -> None:
        super().__init__()
        self.title("Proyecto Colectores")
        self.geometry("1180x780")
        self.minsize(960, 620)

        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self.settings = load_settings()
        self._ui_queue: queue.Queue = queue.Queue()
        self._line_ending_label = self._ending_label_for(self.settings.get("line_ending", "\r\n"))

        self.serial_client = SerialClient(
            on_data=self._on_serial_data,
            on_error=self._on_serial_error,
            on_status=self._on_serial_status,
        )
        self.tcp_client = TcpClient(
            on_data=self._on_serial_data,
            on_error=self._on_serial_error,
            on_status=self._on_serial_status,
        )
        self.session_log = SessionLog()
        self.command_queue = SerialCommandQueue(
            send_command=lambda cmd, wt=0.0: self._serial_write_only(cmd, write_timeout_s=wt),
            on_log=self._append_log,
            pause_ms=400,
            rx_timeout_ms=10000,
            schedule=lambda ms, fn: self.after(ms, fn),
            on_busy=self._on_queue_busy,
        )
        self._last_error_msg = ""
        self._auto_login_pending = False
        self._login_lock_retries = 0
        self._login_lock_blocked = False
        self._login_ok = False
        self._login_retry_after_id: Optional[str] = None
        self._status_idle = "Desconectado — elija COM o IP y pulse Conectar"

        self._build_ui()
        self._refresh_ports()
        self._apply_settings_to_ui()
        self._set_connected_ui(False)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._process_ui_queue)

    @property
    def client(self):
        """Transporte activo: SerialClient (COM) o TcpClient (IP)."""
        if getattr(self, "mode_var", None) is not None and self.mode_var.get() == "IP":
            return self.tcp_client
        mode = str(self.settings.get("connection_mode", "com")).lower()
        if mode == "tcp":
            return self.tcp_client
        return self.serial_client

    def _ending_label_for(self, ending: str) -> str:
        for label, value in self.ENDING_OPTIONS.items():
            if value == ending:
                return label
        return "CR+LF"

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

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
            text="COM o IP · protocolo Delco",
            font=ctk.CTkFont(size=13),
            text_color=("gray30", "gray70"),
            anchor="w",
        )
        subtitle.grid(row=0, column=0, sticky="w")

        ctk.CTkButton(title_row, text="Ayuda", width=80, command=self._show_help).grid(
            row=0, column=1, padx=(8, 0)
        )

        # --- Conexión (arriba, ancho completo) ---
        conn = ctk.CTkFrame(self)
        conn.grid(row=2, column=0, padx=16, pady=8, sticky="ew")
        self._conn_frame = conn
        conn.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(conn, fg_color="transparent")
        top.grid(row=0, column=0, padx=10, pady=(10, 4), sticky="ew")

        ctk.CTkLabel(top, text="Modo").pack(side="left", padx=(0, 6))
        saved_mode = str(self.settings.get("connection_mode", "com")).lower()
        self.mode_var = ctk.StringVar(value="IP" if saved_mode == "tcp" else "COM")
        self.mode_seg = ctk.CTkSegmentedButton(
            top,
            values=["COM", "IP"],
            variable=self.mode_var,
            command=self._on_mode_change,
            width=120,
        )
        self.mode_seg.pack(side="left", padx=(0, 16))

        ctk.CTkLabel(top, text="Fin de línea").pack(side="left", padx=(0, 6))
        self.ending_var = ctk.StringVar(value=self._line_ending_label)
        self.ending_menu = ctk.CTkOptionMenu(
            top,
            variable=self.ending_var,
            values=list(self.ENDING_OPTIONS.keys()),
            width=90,
        )
        self.ending_menu.pack(side="left", padx=(0, 12))

        self.btn_connect = ctk.CTkButton(top, text="Conectar", width=100, command=self._toggle_connect)
        self.btn_connect.pack(side="right", padx=(8, 0))
        self.btn_cancel_seq = ctk.CTkButton(
            top,
            text="Cancelar",
            width=100,
            fg_color=("gray70", "gray35"),
            command=self._cancel_sequence,
        )
        self.btn_cancel_seq.pack(side="right", padx=4)

        self.com_row = ctk.CTkFrame(conn, fg_color="transparent")
        self.com_row.grid(row=1, column=0, padx=10, pady=4, sticky="ew")
        self.com_row.grid_columnconfigure(1, weight=1)

        self.lbl_port = ctk.CTkLabel(self.com_row, text="Puerto COM")
        self.lbl_port.grid(row=0, column=0, padx=(0, 6), sticky="w")
        self.port_var = ctk.StringVar(value="")
        self.port_menu = ctk.CTkOptionMenu(self.com_row, variable=self.port_var, values=["(sin puertos)"])
        self.port_menu.grid(row=0, column=1, padx=4, sticky="ew")
        self.btn_refresh = ctk.CTkButton(self.com_row, text="Refrescar", width=90, command=self._refresh_ports)
        self.btn_refresh.grid(row=0, column=2, padx=4)
        self.lbl_baud = ctk.CTkLabel(self.com_row, text="Baud")
        self.lbl_baud.grid(row=0, column=3, padx=(12, 6))
        self.baud_var = ctk.StringVar(value="9600")
        self.baud_menu = ctk.CTkOptionMenu(
            self.com_row, variable=self.baud_var, values=self.BAUD_OPTIONS, width=100
        )
        self.baud_menu.grid(row=0, column=4, padx=4)

        self.ip_row = ctk.CTkFrame(conn, fg_color="transparent")
        self.ip_row.grid(row=1, column=0, padx=10, pady=4, sticky="ew")
        self.ip_row.grid_columnconfigure(1, weight=1)

        self.lbl_host = ctk.CTkLabel(self.ip_row, text="Host / IP")
        self.lbl_host.grid(row=0, column=0, padx=(0, 6), sticky="w")
        self.host_var = ctk.StringVar(value=str(self.settings.get("tcp_host") or ""))
        self.host_entry = ctk.CTkEntry(
            self.ip_row, textvariable=self.host_var, placeholder_text="ej. 192.168.1.50"
        )
        self.host_entry.grid(row=0, column=1, padx=4, sticky="ew")
        self.lbl_tcp_port = ctk.CTkLabel(self.ip_row, text="Puerto TCP")
        self.lbl_tcp_port.grid(row=0, column=2, padx=(12, 6))
        self.tcp_port_var = ctk.StringVar(value=str(self.settings.get("tcp_port") or DEFAULT_TCP_PORT))
        self.tcp_port_entry = ctk.CTkEntry(self.ip_row, textvariable=self.tcp_port_var, width=80)
        self.tcp_port_entry.grid(row=0, column=3, padx=4, sticky="w")

        self._com_widgets = [self.com_row]
        self._ip_widgets = [self.ip_row]

        self.status_var = ctk.StringVar(value=self._status_idle)
        ctk.CTkLabel(conn, textvariable=self.status_var, anchor="w").grid(
            row=2, column=0, padx=12, pady=(4, 10), sticky="ew"
        )
        self._apply_mode_visibility()

        # --- Cuerpo: historial (izq) | trabajo (der) ---
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=3, column=0, padx=16, pady=(0, 14), sticky="nsew")
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body)
        left.grid(row=0, column=0, padx=(0, 8), pady=0, sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)

        log_header = ctk.CTkFrame(left, fg_color="transparent")
        log_header.grid(row=0, column=0, padx=10, pady=(10, 4), sticky="ew")
        log_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            log_header,
            text="Historial (TX / RX)",
            font=ctk.CTkFont(weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.btn_clear = ctk.CTkButton(
            log_header, text="Limpiar", width=80, command=self._clear_log
        )
        self.btn_clear.grid(row=0, column=1, padx=(8, 0))

        self.log_box = ctk.CTkTextbox(left, font=ctk.CTkFont(family="Consolas", size=12))
        self.log_box.grid(row=1, column=0, padx=10, pady=(0, 8), sticky="nsew")
        self.log_box.configure(state="disabled")

        send_frame = ctk.CTkFrame(left, fg_color="transparent")
        send_frame.grid(row=2, column=0, padx=10, pady=(0, 10), sticky="ew")
        send_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(send_frame, text="Comando").grid(row=0, column=0, padx=(0, 6), pady=4)
        self.cmd_var = ctk.StringVar(value="")
        self.cmd_entry = ctk.CTkEntry(send_frame, textvariable=self.cmd_var)
        self.cmd_entry.grid(row=0, column=1, padx=4, pady=4, sticky="ew")
        self.cmd_entry.bind("<Return>", lambda _e: self._send_command())
        self.btn_send = ctk.CTkButton(send_frame, text="Enviar", width=90, command=self._send_command)
        self.btn_send.grid(row=0, column=2, padx=(4, 0), pady=4)

        right = ctk.CTkFrame(body)
        right.grid(row=0, column=1, padx=(8, 0), pady=0, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)

        self.tabview = ctk.CTkTabview(right)
        self.tabview.grid(row=0, column=0, padx=8, pady=8, sticky="nsew")

        tab_cmds = self.tabview.add("Comandos")
        tab_clock = self.tabview.add("Reloj")
        tab_meters = self.tabview.add("Medidores")
        tab_bulk = self.tabview.add("Carga masiva")

        tab_cmds.grid_columnconfigure(0, weight=1)
        tab_cmds.grid_rowconfigure(0, weight=1)

        cmds = ctk.CTkScrollableFrame(tab_cmds, label_text="Opciones de trabajo")
        cmds.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        cmds.grid_columnconfigure(0, weight=1)
        cmds.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(cmds, text="Medidor (hasta 12 digitos)").grid(
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

        ctk.CTkLabel(cmds, text="1. Flujo base", font=ctk.CTkFont(weight="bold")).grid(
            row=4, column=0, columnspan=2, padx=8, pady=(8, 4), sticky="w"
        )
        self._cmd_btn(cmds, 5, 0, "QUIT", lambda: self._send_one(CMD_QUIT))
        self._cmd_btn(cmds, 5, 1, "Login", lambda: self._send_one(CMD_LOGIN))
        self._cmd_btn(cmds, 6, 0, "Forzar lecturas", self._do_quit_wake_zoom)
        self._cmd_btn(cmds, 6, 1, "Diagnóstico", self._do_quick_diag)

        ctk.CTkLabel(cmds, text="2. Lectura y refresco", font=ctk.CTkFont(weight="bold")).grid(
            row=7, column=0, columnspan=2, padx=8, pady=(12, 4), sticky="w"
        )
        self._cmd_btn(cmds, 8, 0, "Leer medidor", self._do_direct_read)
        self._cmd_btn(cmds, 8, 1, "Refresco", self._do_refresh)
        self._cmd_btn(cmds, 9, 0, "Leer T1–T4", self._do_multitariff_read)

        ctk.CTkLabel(cmds, text="3. Medidores en base", font=ctk.CTkFont(weight="bold")).grid(
            row=10, column=0, columnspan=2, padx=8, pady=(12, 4), sticky="w"
        )
        self._cmd_btn(cmds, 11, 0, "Agregar", self._do_add_meter)
        self._cmd_btn(cmds, 11, 1, "Borrar", self._do_delete_meter)
        self._cmd_btn(cmds, 12, 0, "Agregar CP4", self._do_add_individual)
        self._cmd_btn(cmds, 12, 1, "Borrar base", self._do_delete_base)

        ctk.CTkLabel(cmds, text="4. Estado del colector", font=ctk.CTkFont(weight="bold")).grid(
            row=13, column=0, columnspan=2, padx=8, pady=(12, 4), sticky="w"
        )
        self._cmd_btn(cmds, 14, 0, "Versión", lambda: self._send_one(CMD_VERSION))
        self._cmd_btn(cmds, 14, 1, "Cantidad", self._do_count_meters)
        self._cmd_btn(cmds, 15, 0, "Reiniciar K", self._do_restart_k)
        self._cmd_btn(cmds, 15, 1, "Ir a Reloj", lambda: self.tabview.set("Reloj"))

        note = ctk.CTkLabel(
            cmds,
            text="Flujo típico: QUIT → comando → Forzar lecturas (WAKE+ZOOM).\n"
            "Horarios en pestaña Reloj. Use Cancelar si una secuencia se atasca.",
            wraplength=340,
            justify="left",
            text_color=("gray40", "gray60"),
        )
        note.grid(row=16, column=0, columnspan=2, padx=8, pady=(12, 8), sticky="ew")

        ctk.CTkButton(
            cmds,
            text="Abrir logs",
            height=30,
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

    def _cmd_btn(self, parent: Any, row: int, col: int, text: str, command: Any) -> None:
        ctk.CTkButton(parent, text=text, height=32, command=command).grid(
            row=row, column=col, padx=5, pady=3, sticky="ew"
        )

    def _is_tcp_mode(self) -> bool:
        return self.mode_var.get() == "IP"

    def _on_mode_change(self, _value: str = "") -> None:
        if self.serial_client.is_connected or self.tcp_client.is_connected:
            self._append_log("INFO", "Desconecte antes de cambiar COM/IP")
            # Revert segmented button to the connected mode
            if self.serial_client.is_connected:
                self.mode_var.set("COM")
            else:
                self.mode_var.set("IP")
            return
        self._apply_mode_visibility()

    def _apply_mode_visibility(self) -> None:
        tcp = self._is_tcp_mode()
        for w in self._com_widgets:
            if tcp:
                w.grid_remove()
            else:
                w.grid()
        for w in self._ip_widgets:
            if tcp:
                w.grid()
            else:
                w.grid_remove()

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
        self.host_var.set(str(self.settings.get("tcp_host") or ""))
        self.tcp_port_var.set(str(self.settings.get("tcp_port") or DEFAULT_TCP_PORT))
        mode = str(self.settings.get("connection_mode", "com")).lower()
        self.mode_var.set("IP" if mode == "tcp" else "COM")
        self._apply_mode_visibility()

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
        active = self.client
        if active.is_connected:
            if self.command_queue.is_busy:
                self.command_queue.cancel()
            active.disconnect()
            self._append_log("INFO", "Desconectado")
            return

        self.settings["line_ending"] = self._current_line_ending()

        if self._is_tcp_mode():
            host = self.host_var.get().strip()
            port_txt = self.tcp_port_var.get().strip() or str(DEFAULT_TCP_PORT)
            try:
                port = int(port_txt)
            except ValueError:
                self._append_log("ERR", f"Puerto TCP inválido: {port_txt}")
                return
            try:
                self.tcp_client.connect(host=host, port=port)
            except ConnectionError as exc:
                self._append_log("ERR", str(exc))
                return
            self.settings["connection_mode"] = "tcp"
            self.settings["tcp_host"] = host
            self.settings["tcp_port"] = port
            save_settings(self.settings)
            self._last_error_msg = ""
            self._append_log("INFO", f"Conectado a {host}:{port} (TCP)")
            self.after(400, self._send_initial_login)
            return

        port = self._selected_port()
        if not port:
            self._append_log("ERR", "Seleccione un puerto COM válido")
            return

        baud = int(self.baud_var.get())
        try:
            self.serial_client.connect(
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

        self.settings["connection_mode"] = "com"
        self.settings["port"] = port
        self.settings["baudrate"] = baud
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

    def _connection_status_text(self) -> str:
        if self.tcp_client.is_connected:
            return f"Conectado a {self.tcp_client.host}:{self.tcp_client.port} (TCP)"
        if self.serial_client.is_connected:
            port = self.settings.get("port") or "COM"
            baud = self.settings.get("baudrate") or self.baud_var.get()
            return f"Conectado a {port} @ {baud} baud"
        return self._status_idle

    def _on_queue_busy(self, busy: bool) -> None:
        if busy:
            cmd = self.command_queue.last_command or "…"
            self.status_var.set(f"Secuencia en curso ({cmd}) — espere o pulse Cancelar")
        elif self.client.is_connected:
            self.status_var.set(self._connection_status_text())
        else:
            self.status_var.set(self._status_idle)

    def _cancel_sequence(self) -> None:
        was_blocked = self._login_lock_blocked
        self._reset_login_state()
        if self.command_queue.is_busy:
            self.command_queue.cancel()
            self._append_log("INFO", "Secuencia cancelada por el usuario")
        elif was_blocked:
            self._append_log("INFO", "Auto-login reactivado — puede pulsar Login.")
        else:
            self._append_log("INFO", "No hay secuencia en curso")

    def _reset_login_state(self) -> None:
        self._auto_login_pending = False
        self._login_lock_retries = 0
        self._login_lock_blocked = False
        self._login_ok = False
        if self._login_retry_after_id is not None:
            try:
                self.after_cancel(self._login_retry_after_id)
            except Exception:
                pass
            self._login_retry_after_id = None

    def _send_initial_login(self) -> None:
        if not self.client.is_connected or self.command_queue.is_busy:
            return
        self._reset_login_state()
        self._append_log("INFO", "Login inicial PWR666666…")
        self._auto_login_pending = True

        def after_login() -> None:
            self._auto_login_pending = False
            if not self.client.is_connected or self.command_queue.is_busy:
                return
            last = (self.command_queue.last_response or "").strip().lower()
            unlocked = self._login_ok or ("unlock" in last)
            if not unlocked:
                self._append_log(
                    "WARN",
                    "Login no completo (no hubo UnLock). "
                    f"Ultima respuesta: {self.command_queue.last_response!r}. "
                    "No se envia QUIT automatico. "
                    "Espere 2 s y pulse Login una vez, o Desconectar/Conectar. "
                    "Cierre RemoteCOM u otra app que use el COM.",
                )
                return
            self._login_ok = True
            self._login_lock_blocked = False
            self._append_log(
                "INFO",
                "Tras login OK: enviando QUIT para modo mantenimiento…",
            )
            try:
                self.command_queue.start(
                    [QueuedCommand(CMD_QUIT, wait_rx=True, rx_timeout_ms=8000, write_timeout_s=2.0)],
                    wait_rx=True,
                )
            except RuntimeError:
                pass

        try:
            self.command_queue.start(
                [QueuedCommand(CMD_LOGIN, wait_rx=True, rx_timeout_ms=10000, write_timeout_s=2.0)],
                wait_rx=True,
                on_done=after_login,
            )
        except RuntimeError:
            self._auto_login_pending = False

    def _fail_login_lock_loop(self) -> None:
        """Corta el spam de PWR cuando el colector solo responde Lock."""
        self._login_lock_blocked = True
        self._login_lock_retries = 0
        self._auto_login_pending = False
        if self._login_retry_after_id is not None:
            try:
                self.after_cancel(self._login_retry_after_id)
            except Exception:
                pass
            self._login_retry_after_id = None
        self._append_log(
            "WARN",
            "Lock persistente: PWR666666 no produjo UnLock (3 reintentos). "
            "Auto-login pausado. Espere 3 s y pulse Login, o Desconectar/Conectar. "
            "Cierre RemoteCOM si esta abierto. No pulse VER/O/lectura hasta tener UnLock.",
        )
        if self.command_queue.is_busy:
            self.command_queue.cancel()

    def _auto_login_on_lock(self) -> None:
        """
        Ante Lock: reenviar PWR666666 con pausa (no en el mismo instante).
        Si el comando en curso ya es login, esperar UnLock; reintentar con delay.
        """
        if not self.client.is_connected:
            return

        # Tras agotar reintentos: no cancelar en bucle ni reenviar PWR solo.
        if self._login_lock_blocked:
            if self.command_queue.is_busy:
                self.command_queue.cancel()
            self._append_log(
                "WARN",
                "Sigue Lock — auto-login pausado. Pulse Login o Desconectar/Conectar.",
            )
            return

        # Ya hay un reintento programado: no spamear.
        if self._login_retry_after_id is not None:
            return

        if self._login_lock_retries >= 3:
            self._fail_login_lock_loop()
            return

        self._login_lock_retries += 1
        self._auto_login_pending = True
        attempt = self._login_lock_retries
        delay_ms = 2000 * attempt  # 2s, 4s, 6s

        waiting_login = (
            self.command_queue.is_waiting_rx
            and self.command_queue.last_command.strip().upper() == CMD_LOGIN.upper()
        )
        waiting_other = self.command_queue.is_waiting_rx and not waiting_login

        def _do_retry() -> None:
            self._login_retry_after_id = None
            if not self.client.is_connected or self._login_lock_blocked:
                return
            if waiting_login or (
                self.command_queue.is_waiting_rx
                and self.command_queue.last_command.strip().upper() == CMD_LOGIN.upper()
            ):
                self._append_log(
                    "INFO",
                    f"Reintento login {attempt}/3 tras Lock (espera {delay_ms} ms)…",
                )
                self.command_queue.send_login_for_lock(CMD_LOGIN)
                return
            if self.command_queue.is_waiting_rx:
                self._append_log(
                    "INFO",
                    f"Lock: enviando PWR666666 (intento {attempt}/3, tras {delay_ms} ms)…",
                )
                self.command_queue.send_login_for_lock(CMD_LOGIN)
                return
            if self.command_queue.is_busy:
                self._append_log("WARN", "Cola ocupada: no se pudo reenviar login")
                self._auto_login_pending = False
                return
            self._append_log("INFO", f"Login automatico PWR666666 (intento {attempt}/3)…")
            try:
                self.command_queue.start(
                    [QueuedCommand(CMD_LOGIN, wait_rx=True, rx_timeout_ms=10000, write_timeout_s=2.0)],
                    wait_rx=True,
                )
            except RuntimeError:
                self._auto_login_pending = False

        if waiting_login:
            self._append_log(
                "INFO",
                f"PWR666666 respondio Lock — esperando {delay_ms} ms antes de reintentar "
                f"({attempt}/3)…",
            )
        elif waiting_other:
            self._append_log(
                "INFO",
                f"Lock durante {self.command_queue.last_command!r} — "
                f"login en {delay_ms} ms ({attempt}/3)…",
            )
        else:
            self._append_log("INFO", f"Lock recibido — login en {delay_ms} ms ({attempt}/3)…")

        self._login_retry_after_id = self.after(delay_ms, _do_retry)

    def _serial_write_only(self, command: str, write_timeout_s: float = 0.0) -> bool:
        """Solo I/O del transporte activo. NO tocar la UI (se llama desde hilo worker)."""
        transport = self.client
        if not transport.is_connected:
            return False
        ending = self._current_line_ending()
        try:
            timeout = write_timeout_s if write_timeout_s > 0 else 2.0
            transport.send(format_command(command, ending), write_timeout=timeout)
            return True
        except ConnectionError as exc:
            # Notificar a la UI sin tocar Tk desde este hilo.
            self._ui_queue.put(("write_fail", str(exc)))
            return False

    def _on_connection_lost(self, reason: str) -> None:
        if reason == self._last_error_msg:
            return
        self._last_error_msg = reason
        if self.command_queue.is_busy:
            self.command_queue.cancel()
        if self._is_tcp_mode():
            self._append_log(
                "INFO",
                "Conexión TCP perdida. Verifique IP/puerto, red y que el colector "
                "(o convertidor RS232↔Ethernet) escuche TCP. Vuelva a Conectar.",
            )
        else:
            self._append_log(
                "INFO",
                "Puerto COM perdido o bloqueado. Desenchufe el USB, espere 5 s, "
                "pulse Refrescar y vuelva a Conectar. Cierre RemoteCOM si está abierto.",
            )

    def _send_one(self, command: str) -> None:
        if self.command_queue.is_busy:
            self._append_log("ERR", "Hay una secuencia en curso. Espere o pulse Cancelar.")
            return
        if command.strip().upper() == CMD_LOGIN.upper():
            self._reset_login_state()
            self._auto_login_pending = True
            self._append_log("INFO", "Login manual PWR666666…")
        self.cmd_var.set(command)
        try:
            self.command_queue.start(
                [QueuedCommand(command, wait_rx=True, rx_timeout_ms=10000, write_timeout_s=2.0)],
                wait_rx=True,
            )
        except RuntimeError as exc:
            self._append_log("ERR", str(exc))
            if command.strip().upper() == CMD_LOGIN.upper():
                self._auto_login_pending = False

    def _send_many(
        self,
        commands: Iterable[str],
        pause_ms: int = 500,
        wait_rx: bool = True,
        on_done: Optional[Any] = None,
    ) -> None:
        cmds = [c for c in commands if c]
        if not cmds:
            return
        if not self.client.is_connected:
            self._append_log("ERR", "No hay conexión activa")
            return
        if self.command_queue.is_busy:
            self._append_log("ERR", "Hay una secuencia en curso. Espere o pulse Cancelar.")
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
        if self.command_queue.is_busy:
            self._append_log("ERR", "Hay una secuencia en curso. Espere o pulse Cancelar.")
            return
        try:
            self.command_queue.start(
                [QueuedCommand(text, wait_rx=True, rx_timeout_ms=8000, write_timeout_s=2.0)],
                wait_rx=True,
            )
        except RuntimeError as exc:
            self._append_log("ERR", str(exc))

    def _maintenance_sequence(self, *mid_commands: str) -> None:
        """QUIT → comando(s) → WAKE → ZOOM, esperando respuesta en cada paso (Comados.doc)."""
        from ui.extra_tabs import WAKE_SLOW, ZOOM_CMD

        cmds: list = [
            QueuedCommand(CMD_QUIT, wait_rx=True, rx_timeout_ms=10000, write_timeout_s=2.0),
        ]
        for mid in mid_commands:
            if mid:
                cmds.append(
                    QueuedCommand(mid, wait_rx=True, rx_timeout_ms=10000, write_timeout_s=2.0)
                )
        cmds.extend([WAKE_SLOW, ZOOM_CMD])
        self._send_many(cmds, wait_rx=True)

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
            self._append_log("INFO", "Refresco (§2): QUIT → SGXF → WAKE → ZOOM")
            self._maintenance_sequence(cmd_forced_refresh(meter))
        except ValueError as exc:
            self._append_log("ERR", str(exc))

    def _do_display(self, on: bool) -> None:
        meter = self._require_meter()
        if not meter:
            return
        try:
            self._maintenance_sequence(cmd_display(meter, on))
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
            self._append_log("INFO", "Agregar (§4): QUIT → A… → WAKE → ZOOM")
            self._maintenance_sequence(cmd_add_meter(meter, header))
        except ValueError as exc:
            self._append_log("ERR", str(exc))

    def _do_add_individual(self) -> None:
        meter = self._require_meter()
        if not meter:
            return
        try:
            self._append_log("INFO", "Agregar CP4 (§4): QUIT → A…00 → WAKE → ZOOM")
            self._maintenance_sequence(cmd_add_meter(meter, individual_tariff=True))
        except ValueError as exc:
            self._append_log("ERR", str(exc))

    def _do_delete_meter(self) -> None:
        meter = self._require_meter()
        if not meter:
            return
        try:
            self._append_log("INFO", "Borrar (§3): QUIT → E… → WAKE → ZOOM")
            self._maintenance_sequence(cmd_delete_meter(meter))
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

    def _do_restart_k(self) -> None:
        """
        §8 Comados.doc — leer AMRsw con O e *inferir*:
          - Si ya es 10100000 → no hace falta k=; QUIT → WAKE → ZOOM
          - Cualquier otro valor (10000000 es solo ejemplo del manual) →
            QUIT → k=10100000 → O → QUIT → WAKE → ZOOM
        """
        from ui.extra_tabs import WAKE_SLOW, ZOOM_CMD

        if not self.client.is_connected:
            self._append_log("ERR", "Conecte primero al colector")
            return
        if self.command_queue.is_busy:
            self._append_log("ERR", "Hay una secuencia en curso. Espere o pulse Cancelar.")
            return

        self._append_log("INFO", "K (§8): QUIT + O para leer AMRsw e inferir si hace falta k=…")
        self._k_o_retry = False

        def _extract_amr(raw: str) -> Optional[str]:
            info = parse_collector_info(raw)
            if info and info.amrsw:
                return info.amrsw
            import re

            m = re.search(r"AMRsw=(\d+)", raw or "", re.IGNORECASE)
            return m.group(1) if m else None

        def _restart_readings(note: str) -> None:
            self._append_log("INFO", note)
            try:
                self.command_queue.start(
                    [
                        QueuedCommand(CMD_QUIT, wait_rx=True, rx_timeout_ms=10000, write_timeout_s=2.0),
                        WAKE_SLOW,
                        ZOOM_CMD,
                    ],
                    wait_rx=True,
                )
            except RuntimeError as exc:
                self._append_log("ERR", str(exc))

        def _start_k_apply() -> None:
            k_reply: dict = {"text": ""}

            def on_step(cmd: str, rx: str) -> None:
                if cmd.strip().lower().startswith("k="):
                    k_reply["text"] = (rx or "").strip()

            def after_k_verify() -> None:
                if self.command_queue.was_aborted:
                    self._append_log(
                        "WARN",
                        "Secuencia K interrumpida; no se reinicia lectura (WAKE/ZOOM).",
                    )
                    return
                amr_after = _extract_amr(self.command_queue.last_response)
                k_rx = k_reply["text"].upper()

                if k_rx == "NO":
                    self._append_log(
                        "WARN",
                        f"k=10100000 respondio NO. AMRsw tras O: {amr_after or '?'}. "
                        "En este colector K fue rechazado. "
                        "Use boton QUIT→WAKE→ZOOM para forzar lecturas (ZOOM ya funciona en COM).",
                    )
                    return

                if amr_after and not amrsw_needs_k(amr_after):
                    _restart_readings(
                        f"Tras k=: AMRsw={amr_after} (meta OK). "
                        "Reiniciando: QUIT → WAKE → ZOOM…"
                    )
                    return

                _restart_readings(
                    f"Tras k=: respuesta={k_reply['text'] or '?'} | "
                    f"AMRsw={amr_after or '?'}. "
                    "Continuando ciclo §8/§9: QUIT → WAKE → ZOOM…"
                )

            try:
                self.command_queue.start(
                    [
                        QueuedCommand(CMD_QUIT, wait_rx=True, rx_timeout_ms=10000, write_timeout_s=2.0),
                        QueuedCommand(
                            cmd_restart_reading(),
                            wait_rx=True,
                            rx_timeout_ms=10000,
                            write_timeout_s=2.0,
                        ),
                        QueuedCommand(
                            CMD_COUNT_METERS,
                            multiline_ms=2500,
                            wait_rx=True,
                            rx_timeout_ms=15000,
                            write_timeout_s=2.0,
                        ),
                    ],
                    wait_rx=True,
                    on_step=on_step,
                    on_done=after_k_verify,
                )
            except RuntimeError as exc:
                self._append_log("ERR", str(exc))

        def after_amr_check() -> None:
            if self.command_queue.was_aborted:
                raw = (self.command_queue.last_response or "").strip()
                self._append_log(
                    "WARN",
                    f"Secuencia K interrumpida en O ({raw!r}). "
                    "Login → QUIT → O y vuelva a pulsar K.",
                )
                self._k_o_retry = False
                return
            raw = (self.command_queue.last_response or "").strip()
            amr = _extract_amr(raw)

            if amr and not amrsw_needs_k(amr):
                _restart_readings(
                    f"Inferencia §8: AMRsw={amr} ya es la meta ({AMRSW_OK}). "
                    "No hace falta k=. Reiniciando: QUIT → WAKE → ZOOM…"
                )
                return

            if amr:
                self._append_log(
                    "INFO",
                    f"Inferencia §8: AMRsw={amr} (distinto de {AMRSW_OK}). "
                    "Hay que enviar k=10100000. "
                    "(En el manual el ejemplo es 10000000; cualquier otro valor tambien vale.)",
                )
                _start_k_apply()
                return

            # Sin AMRsw: respuesta vacía o no parseable — no inventar k= a ciegas.
            if raw.upper() == "NO":
                self._append_log(
                    "WARN",
                    "Abortando K: O respondio NO (sin AMRsw). "
                    "Login → QUIT → O hasta ver AMRsw=…; luego K una sola vez.",
                )
                self._k_o_retry = False
                return

            if not self._k_o_retry:
                self._k_o_retry = True
                self._append_log(
                    "WARN",
                    f"O no entrego AMRsw (respuesta {raw!r}). "
                    "Reintentando una vez: Login → QUIT → O…",
                )
                try:
                    self.command_queue.start(
                        [
                            QueuedCommand(CMD_LOGIN, wait_rx=True, rx_timeout_ms=8000, write_timeout_s=2.0),
                            QueuedCommand(CMD_QUIT, wait_rx=True, rx_timeout_ms=10000, write_timeout_s=2.0),
                            QueuedCommand(
                                CMD_COUNT_METERS,
                                multiline_ms=2500,
                                wait_rx=True,
                                rx_timeout_ms=15000,
                                write_timeout_s=2.0,
                            ),
                        ],
                        wait_rx=True,
                        on_done=after_amr_check,
                    )
                except RuntimeError as exc:
                    self._append_log("ERR", str(exc))
                return

            self._k_o_retry = False
            self._append_log(
                "WARN",
                "Abortando K: sin AMRsw para inferir (§8). "
                "Si O responde NO, el colector no esta en modo mantenimiento. "
                "Conecte → Login (UnLock) → espere el QUIT automatico → boton O. "
                "Cuando vea AMRsw=… vuelva a pulsar K.",
            )

        try:
            self.command_queue.start(
                [
                    QueuedCommand(CMD_QUIT, wait_rx=True, rx_timeout_ms=10000, write_timeout_s=2.0),
                    QueuedCommand(
                        CMD_COUNT_METERS,
                        multiline_ms=2500,
                        wait_rx=True,
                        rx_timeout_ms=15000,
                        write_timeout_s=2.0,
                    ),
                ],
                wait_rx=True,
                on_done=after_amr_check,
            )
        except RuntimeError as exc:
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
        """VER + QUIT + O (version y estado AMRsw / IDnum)."""
        if not self.client.is_connected:
            self._append_log("ERR", "Conecte primero al colector")
            return
        self._append_log("INFO", "Diagnostico: QUIT → VER → O")
        self._send_many(
            [
                QueuedCommand(CMD_QUIT, wait_rx=True, rx_timeout_ms=10000, write_timeout_s=2.0),
                QueuedCommand(CMD_VERSION, wait_rx=True, rx_timeout_ms=8000, write_timeout_s=2.0),
                QueuedCommand(
                    CMD_COUNT_METERS,
                    multiline_ms=2500,
                    wait_rx=True,
                    rx_timeout_ms=15000,
                    write_timeout_s=2.0,
                ),
            ],
            wait_rx=True,
        )

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
                        if not self._login_lock_blocked:
                            tip = describe_lock_state(text)
                            if tip:
                                self._append_log("INFO", tip)
                        self._auto_login_on_lock()
                        continue
                    if is_unlock_response(text):
                        self._login_ok = True
                        self._login_lock_blocked = False
                        self._auto_login_pending = False
                        self._login_lock_retries = 0
                        if self._login_retry_after_id is not None:
                            try:
                                self.after_cancel(self._login_retry_after_id)
                            except Exception:
                                pass
                            self._login_retry_after_id = None
                        tip = describe_lock_state(text)
                        if tip:
                            self._append_log("INFO", tip)
                        # Si el comando en curso era login, UnLock cierra la secuencia.
                        # Si era otro comando, on_unlock reintenta ese comando (no el login).
                        last = self.command_queue.last_command.strip().upper()
                        if last == CMD_LOGIN.upper():
                            self.command_queue.on_rx(text)
                        else:
                            self.command_queue.on_unlock()
                        continue
                    # Solo interpretar tip si este RX cierra/avanza un comando en curso.
                    # Evita spam/loop de tips con last_command viejo (ej. QUIT) en RX sueltos.
                    awaiting_cmd = (
                        self.command_queue.last_command
                        if self.command_queue.is_waiting_rx
                        else ""
                    )
                    self.command_queue.on_rx(text)
                    if awaiting_cmd:
                        tip = describe_status_response(text, awaiting_cmd)
                        if tip:
                            last = awaiting_cmd.strip().lower()
                            # i / k= / QUIT con NO suelen ser informativos, no fallos graves
                            kind = (
                                "INFO"
                                if text.strip().upper() == "NO"
                                and (
                                    last == "i"
                                    or last == "quit"
                                    or last.startswith("k=")
                                )
                                else "WARN"
                            )
                            self._append_log(kind, tip)
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
                elif kind == "write_fail":
                    self._append_log("ERR", str(payload))
                    self.command_queue.on_send_failed()
                    self._on_connection_lost(str(payload))
                elif kind == "status":
                    connected = bool(payload)
                    if not connected:
                        if self.command_queue.is_busy:
                            self.command_queue.cancel()
                        self._last_error_msg = ""
                        self._reset_login_state()
                    self._set_connected_ui(connected)
        except queue.Empty:
            pass
        self.after(100, self._process_ui_queue)

    def _set_connected_ui(self, connected: bool) -> None:
        if connected:
            self.status_var.set(self._connection_status_text())
            self.btn_connect.configure(text="Desconectar")
            self.mode_seg.configure(state="disabled")
            self.port_menu.configure(state="disabled")
            self.baud_menu.configure(state="disabled")
            self.host_entry.configure(state="disabled")
            self.tcp_port_entry.configure(state="disabled")
            self.ending_menu.configure(state="disabled")
        else:
            self.status_var.set(self._status_idle)
            self.btn_connect.configure(text="Conectar")
            self.mode_seg.configure(state="normal")
            self.port_menu.configure(state="normal")
            self.baud_menu.configure(state="normal")
            self.host_entry.configure(state="normal")
            self.tcp_port_entry.configure(state="normal")
            self.ending_menu.configure(state="normal")

    def _show_help(self) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title("Qué hace cada cosa")
        dialog.geometry("620x620")
        dialog.transient(self)
        dialog.grab_set()

        text = ctk.CTkTextbox(dialog, wrap="word", font=ctk.CTkFont(size=13))
        text.pack(fill="both", expand=True, padx=12, pady=12)
        text.insert(
            "1.0",
            "Según Comados.doc (manual de comandos del colector)\n\n"
            "Conexión: modo COM (cable USB) o IP (TCP experimental).\n"
            "En IP use host + puerto (default 4001). Mismos comandos ASCII.\n"
            "Requiere colector/gateway que escuche TCP.\n\n"
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
            "Al terminar: Desconectar.\n",
        )
        text.configure(state="disabled")
        ctk.CTkButton(dialog, text="Cerrar", width=100, command=dialog.destroy).pack(pady=(0, 12))

    def _on_close(self) -> None:
        try:
            if self.command_queue.is_busy:
                self.command_queue.cancel()
            self._persist_meter_fields()
            if self.serial_client.is_connected or getattr(self.serial_client, "_ser", None) is not None:
                self.serial_client.disconnect()
            if self.tcp_client.is_connected or getattr(self.tcp_client, "_sock", None) is not None:
                self.tcp_client.disconnect()
            self.session_log.close()
        except Exception:
            pass
        finally:
            try:
                self.destroy()
            except Exception:
                pass


def run_app() -> None:
    app = AppWindow()
    try:
        app.mainloop()
    except KeyboardInterrupt:
        try:
            app._on_close()
        except Exception:
            pass


if __name__ == "__main__":
    src = Path(__file__).resolve().parents[1]
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    run_app()
