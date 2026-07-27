"""Ventana principal: puertos COM, conectar, log y comandos del colector."""

from __future__ import annotations

import json
import queue
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import customtkinter as ctk

from serial_client import SerialClient, describe_ports, list_serial_ports
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
    cmd_restart_reading,
    cmd_set_clock,
    describe_lock_state,
    format_command,
    parse_direct_read_response,
)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _settings_path() -> Path:
    return _project_root() / "config" / "settings.json"


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
        self.geometry("1100x780")
        self.minsize(960, 680)

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
        self.btn_connect.grid(row=0, column=7, padx=(8, 12), pady=12)

        self.status_var = ctk.StringVar(value="Desconectado — el colector no está en uso")
        ctk.CTkLabel(conn, textvariable=self.status_var, anchor="w").grid(
            row=1, column=0, columnspan=8, padx=12, pady=(0, 10), sticky="ew"
        )

        # --- Centro: log + panel comandos ---
        center = ctk.CTkFrame(self, fg_color="transparent")
        center.grid(row=3, column=0, padx=16, pady=8, sticky="nsew")
        center.grid_columnconfigure(0, weight=3)
        center.grid_columnconfigure(1, weight=2)
        center.grid_rowconfigure(0, weight=1)

        log_frame = ctk.CTkFrame(center)
        log_frame.grid(row=0, column=0, padx=(0, 8), sticky="nsew")
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

        cmds = ctk.CTkScrollableFrame(center, label_text="Comandos del colector")
        cmds.grid(row=0, column=1, sticky="nsew")
        cmds.grid_columnconfigure(0, weight=1)
        cmds.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(cmds, text="Medidor (hasta 12 dígitos)").grid(
            row=0, column=0, columnspan=2, padx=8, pady=(8, 2), sticky="w"
        )
        self.meter_var = ctk.StringVar(value=str(self.settings.get("last_meter") or ""))
        ctk.CTkEntry(cmds, textvariable=self.meter_var, placeholder_text="ej. 23388410").grid(
            row=1, column=0, columnspan=2, padx=8, pady=(0, 8), sticky="ew"
        )

        ctk.CTkLabel(cmds, text="Cabecera (12 dígitos, si aplica)").grid(
            row=2, column=0, columnspan=2, padx=8, pady=(4, 2), sticky="w"
        )
        self.header_var = ctk.StringVar(value=str(self.settings.get("last_header") or ""))
        ctk.CTkEntry(cmds, textvariable=self.header_var, placeholder_text="ej. 90059613").grid(
            row=3, column=0, columnspan=2, padx=8, pady=(0, 8), sticky="ew"
        )

        # Flujo base
        ctk.CTkLabel(cmds, text="Flujo base", font=ctk.CTkFont(weight="bold")).grid(
            row=4, column=0, columnspan=2, padx=8, pady=(8, 4), sticky="w"
        )
        self._cmd_btn(cmds, 5, 0, "QUIT (detener)", lambda: self._send_one(CMD_QUIT))
        self._cmd_btn(cmds, 5, 1, "Login PWR666666", lambda: self._send_one(CMD_LOGIN))
        self._cmd_btn(cmds, 6, 0, "WAKE", lambda: self._send_one(CMD_WAKE))
        self._cmd_btn(cmds, 6, 1, "ZOOM (start read)", lambda: self._send_one(CMD_ZOOM))
        self._cmd_btn(cmds, 7, 0, "VER (versión)", lambda: self._send_one(CMD_VERSION))
        self._cmd_btn(cmds, 7, 1, "O (cant. medidores)", lambda: self._send_one(CMD_COUNT_METERS))
        self._cmd_btn(cmds, 8, 0, "i (horarios polling)", lambda: self._send_one(CMD_POLLING_SCHEDULE))
        self._cmd_btn(cmds, 8, 1, "K k=10100000", lambda: self._send_one(cmd_restart_reading()))

        # Lectura / medidor
        ctk.CTkLabel(cmds, text="Lectura y medidor", font=ctk.CTkFont(weight="bold")).grid(
            row=9, column=0, columnspan=2, padx=8, pady=(12, 4), sticky="w"
        )
        self._cmd_btn(cmds, 10, 0, "Lectura directa R…", self._do_direct_read)
        self._cmd_btn(cmds, 10, 1, "Refresco SGXF…", self._do_refresh)
        self._cmd_btn(cmds, 11, 0, "Display ON", lambda: self._do_display(True))
        self._cmd_btn(cmds, 11, 1, "Display OFF", lambda: self._do_display(False))
        self._cmd_btn(cmds, 12, 0, "Agregar medidor A…", self._do_add_meter)
        self._cmd_btn(cmds, 12, 1, "Borrar medidor E…", self._do_delete_meter)
        self._cmd_btn(cmds, 13, 0, "Agregar CP4 (A…00)", self._do_add_individual)
        self._cmd_btn(cmds, 13, 1, "DEL (borrar base)", lambda: self._send_one(CMD_DELETE_BASE))

        ctk.CTkLabel(cmds, text="Fecha/hora colector (AAMMDDHHMMSS)").grid(
            row=14, column=0, columnspan=2, padx=8, pady=(12, 2), sticky="w"
        )
        self.clock_var = ctk.StringVar(value="")
        ctk.CTkEntry(cmds, textvariable=self.clock_var, placeholder_text="ej. 091012104216").grid(
            row=15, column=0, columnspan=2, padx=8, pady=(0, 4), sticky="ew"
        )
        self._cmd_btn(cmds, 16, 0, "Set reloj C…", self._do_set_clock)
        self._cmd_btn(cmds, 16, 1, "QUIT+WAKE+ZOOM", self._do_quit_wake_zoom)

        note = ctk.CTkLabel(
            cmds,
            text="Muchas operaciones piden QUIT antes y WAKE+ZOOM después. "
            "Si responde lock, use Login.",
            wraplength=320,
            justify="left",
            text_color=("gray40", "gray60"),
        )
        note.grid(row=17, column=0, columnspan=2, padx=8, pady=(12, 8), sticky="ew")

        # --- Envío libre ---
        send_frame = ctk.CTkFrame(self)
        send_frame.grid(row=4, column=0, padx=16, pady=(0, 14), sticky="ew")
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
        self._append_log("INFO", f"Conectado a {port} @ {baud} baud")

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

    def _send_one(self, command: str) -> None:
        self.cmd_var.set(command)
        self._send_command()

    def _send_many(self, commands: Iterable[str], pause_ms: int = 250) -> None:
        cmds = [c for c in commands if c]
        if not cmds:
            return

        def _step(index: int) -> None:
            if index >= len(cmds):
                return
            self._send_one(cmds[index])
            if index + 1 < len(cmds):
                self.after(pause_ms, lambda: _step(index + 1))

        _step(0)

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

    def _do_set_clock(self) -> None:
        value = self.clock_var.get().strip()
        try:
            self._send_one(cmd_set_clock(value))
        except ValueError as exc:
            self._append_log("ERR", str(exc))

    def _do_quit_wake_zoom(self) -> None:
        self._send_many([CMD_QUIT, CMD_WAKE, CMD_ZOOM])

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
                    tip = describe_lock_state(text)
                    if tip:
                        self._append_log("INFO", tip)
                    parsed = parse_direct_read_response(text)
                    if parsed and parsed.reading_value is not None:
                        disp = (
                            "ON"
                            if parsed.display_on is True
                            else "OFF"
                            if parsed.display_on is False
                            else "?"
                        )
                        self._append_log(
                            "INFO",
                            f"Lectura={parsed.reading_value} | Fecha={parsed.datetime_text} | "
                            f"Cabecera={parsed.header} | Display={disp}",
                        )
                elif kind == "error":
                    self._append_log("ERR", str(payload))
                elif kind == "status":
                    self._set_connected_ui(bool(payload))
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
            self.status_var.set("Desconectado — el colector no está en uso")
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
            "Según ComandosColectores.doc\n\n"
            "QUIT — Detiene el proceso de lectura del colector. Muchas operaciones lo piden primero.\n"
            "Login PWR666666 — Si responde lock, desbloquea (debe responder unlock).\n"
            "WAKE / ZOOM — Reinician lecturas (ZOOM = start read).\n"
            "VER — Versión/protocolo (ej. CCE16 v3.0.26 o SLD16 v3.1.41).\n"
            "O — Cantidad de medidores y estado AMRsw.\n"
            "K (k=10100000) — Deja AMRsw=10100000 para reiniciar el ciclo de lectura.\n\n"
            "Lectura directa R… — R + medidor(12) + F0318121606 (lectura, fecha, cabecera, display).\n"
            "Display ON/OFF — XFKG + medidor + 01/00 (envía QUIT antes y WAKE+ZOOM después).\n"
            "Refresco SGXF — Fuerza refresco del medidor.\n"
            "Agregar A… / CP4 — Alta de medidor (con cabecera o tarifa 00).\n"
            "Borrar E… — Elimina un medidor.\n"
            "DEL — Borra toda la base del colector (cuidado).\n"
            "Set reloj C… — C + AAMMDDHHMMSS (12 dígitos).\n\n"
            "Puerto / Velocidad / Conectar — Abren el canal USB. Al terminar: Desconectar y retirar USB.\n",
        )
        text.configure(state="disabled")
        ctk.CTkButton(dialog, text="Cerrar", width=100, command=dialog.destroy).pack(pady=(0, 12))

    def _on_close(self) -> None:
        try:
            self._persist_meter_fields()
            if self.client.is_connected:
                self.client.disconnect()
        finally:
            self.destroy()


def run_app() -> None:
    app = AppWindow()
    app.mainloop()


if __name__ == "__main__":
    root = _project_root()
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    run_app()
