"""Cliente serial thread-safe para el colector (RS232 vía USB/COM)."""

from __future__ import annotations

import threading
from typing import Callable, List, Optional

import serial
from serial.tools import list_ports


OnDataCallback = Callable[[str], None]
OnErrorCallback = Callable[[str], None]
OnStatusCallback = Callable[[bool], None]


def list_serial_ports() -> List[str]:
    """Devuelve nombres de puertos COM disponibles (ej. COM3)."""
    ports = list_ports.comports()
    return [p.device for p in sorted(ports, key=lambda x: x.device)]


def describe_ports() -> List[str]:
    """Lista legible: 'COM3 — USB Serial Device'."""
    items = []
    for p in sorted(list_ports.comports(), key=lambda x: x.device):
        desc = p.description or "Sin descripción"
        items.append(f"{p.device} — {desc}")
    return items


class SerialClient:
    """Abre un puerto COM y lee en un hilo aparte para no bloquear la UI."""

    def __init__(
        self,
        on_data: Optional[OnDataCallback] = None,
        on_error: Optional[OnErrorCallback] = None,
        on_status: Optional[OnStatusCallback] = None,
    ) -> None:
        self._on_data = on_data
        self._on_error = on_error
        self._on_status = on_status

        self._ser: Optional[serial.Serial] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._write_lock = threading.Lock()

    @property
    def is_connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def connect(
        self,
        port: str,
        baudrate: int = 9600,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: float = 1,
        timeout: float = 0.2,
    ) -> None:
        if self.is_connected:
            self.disconnect()

        parity_map = {
            "N": serial.PARITY_NONE,
            "E": serial.PARITY_EVEN,
            "O": serial.PARITY_ODD,
            "M": serial.PARITY_MARK,
            "S": serial.PARITY_SPACE,
        }
        stop_map = {
            1: serial.STOPBITS_ONE,
            1.5: serial.STOPBITS_ONE_POINT_FIVE,
            2: serial.STOPBITS_TWO,
        }
        byte_map = {
            5: serial.FIVEBITS,
            6: serial.SIXBITS,
            7: serial.SEVENBITS,
            8: serial.EIGHTBITS,
        }

        try:
            self._ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=byte_map.get(bytesize, serial.EIGHTBITS),
                parity=parity_map.get(parity.upper(), serial.PARITY_NONE),
                stopbits=stop_map.get(stopbits, serial.STOPBITS_ONE),
                timeout=timeout,
                write_timeout=2.0,
            )
        except serial.SerialException as exc:
            self._ser = None
            msg = f"No se pudo abrir {port}: {exc}"
            if self._on_error:
                self._on_error(msg)
            raise ConnectionError(msg) from exc

        self._stop_event.clear()
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            name="SerialReader",
            daemon=True,
        )
        self._reader_thread.start()

        if self._on_status:
            self._on_status(True)

    def disconnect(self) -> None:
        self._stop_event.set()
        thread = self._reader_thread
        if thread and thread.is_alive():
            thread.join(timeout=1.5)
        self._reader_thread = None

        if self._ser is not None:
            try:
                if self._ser.is_open:
                    self._ser.close()
            except serial.SerialException:
                pass
            self._ser = None

        if self._on_status:
            self._on_status(False)

    def send(self, data: bytes) -> None:
        if not self.is_connected or self._ser is None:
            raise ConnectionError("No hay conexión serial activa.")
        with self._write_lock:
            try:
                self._ser.write(data)
                self._ser.flush()
            except serial.SerialException as exc:
                msg = f"Error al escribir: {exc}"
                if self._on_error:
                    self._on_error(msg)
                raise ConnectionError(msg) from exc

    def send_text(self, text: str, line_ending: str = "\r\n") -> None:
        payload = text.rstrip("\r\n") + line_ending
        self.send(payload.encode("ascii", errors="replace"))

    def _read_loop(self) -> None:
        buffer = bytearray()
        while not self._stop_event.is_set():
            ser = self._ser
            if ser is None or not ser.is_open:
                break
            try:
                chunk = ser.read(256)
            except serial.SerialException as exc:
                if not self._stop_event.is_set() and self._on_error:
                    self._on_error(f"Error de lectura: {exc}")
                break

            if not chunk:
                continue

            buffer.extend(chunk)
            while True:
                # Preferir líneas completas; si no hay salto, emitir por trozos grandes.
                for sep in (b"\r\n", b"\n", b"\r"):
                    idx = buffer.find(sep)
                    if idx != -1:
                        line = bytes(buffer[:idx])
                        del buffer[: idx + len(sep)]
                        self._emit_data(line)
                        break
                else:
                    if len(buffer) >= 512:
                        line = bytes(buffer)
                        buffer.clear()
                        self._emit_data(line)
                    break

        # Vaciar resto del buffer al salir
        if buffer:
            self._emit_data(bytes(buffer))
            buffer.clear()

        if self._on_status and not self._stop_event.is_set():
            # Desconexión inesperada
            try:
                if self._ser and self._ser.is_open:
                    self._ser.close()
            except serial.SerialException:
                pass
            self._ser = None
            self._on_status(False)

    def _emit_data(self, raw: bytes) -> None:
        if not self._on_data:
            return
        try:
            text = raw.decode("ascii", errors="replace")
        except Exception:
            text = repr(raw)
        if text:
            self._on_data(text)
