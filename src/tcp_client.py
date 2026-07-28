"""Cliente TCP thread-safe: mismos comandos ASCII del colector por IP:puerto."""

from __future__ import annotations

import socket
import threading
from typing import Callable, Optional

OnDataCallback = Callable[[str], None]
OnErrorCallback = Callable[[str], None]
OnStatusCallback = Callable[[bool], None]

DEFAULT_TCP_PORT = 4001
CONNECT_TIMEOUT_S = 5.0
RECV_TIMEOUT_S = 0.2


class TcpClient:
    """Abre un socket TCP y lee en un hilo aparte (misma API que SerialClient)."""

    def __init__(
        self,
        on_data: Optional[OnDataCallback] = None,
        on_error: Optional[OnErrorCallback] = None,
        on_status: Optional[OnStatusCallback] = None,
    ) -> None:
        self._on_data = on_data
        self._on_error = on_error
        self._on_status = on_status

        self._sock: Optional[socket.socket] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._write_lock = threading.Lock()
        self._link_lost = False
        self.host: str = ""
        self.port: int = DEFAULT_TCP_PORT

    @property
    def is_connected(self) -> bool:
        return self._sock is not None and not self._link_lost

    def connect(self, host: str, port: int = DEFAULT_TCP_PORT, timeout: float = CONNECT_TIMEOUT_S) -> None:
        host = (host or "").strip()
        if not host:
            raise ConnectionError("Indique una dirección IP o host.")
        try:
            port_i = int(port)
        except (TypeError, ValueError) as exc:
            raise ConnectionError(f"Puerto TCP inválido: {port}") from exc
        if not (1 <= port_i <= 65535):
            raise ConnectionError(f"Puerto TCP fuera de rango: {port_i}")

        if self.is_connected:
            self.disconnect()

        try:
            self._link_lost = False
            sock = socket.create_connection((host, port_i), timeout=timeout)
            sock.settimeout(RECV_TIMEOUT_S)
            # Evitar Nagle + buffering largo en comandos cortos
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._sock = sock
            self.host = host
            self.port = port_i
        except OSError as exc:
            self._sock = None
            msg = f"No se pudo conectar a {host}:{port_i}: {exc}"
            if self._on_error:
                self._on_error(msg)
            raise ConnectionError(msg) from exc

        self._stop_event.clear()
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            name="TcpReader",
            daemon=True,
        )
        self._reader_thread.start()

        if self._on_status:
            self._on_status(True)

    def disconnect(self) -> None:
        self._stop_event.set()
        self._link_lost = True

        with self._write_lock:
            sock = self._sock
            self._sock = None
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError:
                    pass

        thread = self._reader_thread
        self._reader_thread = None
        if thread and thread.is_alive():
            thread.join(timeout=0.4)

        self._link_lost = False
        if self._on_status:
            try:
                self._on_status(False)
            except Exception:
                pass

    def _notify_link_lost(self, reason: str) -> None:
        if self._link_lost:
            return
        self._link_lost = True
        self._stop_event.set()
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if self._on_status:
            self._on_status(False)

    def send(self, data: bytes, write_timeout: Optional[float] = None) -> None:
        if not self.is_connected or self._sock is None:
            raise ConnectionError("No hay conexión TCP activa.")
        with self._write_lock:
            sock = self._sock
            if sock is None:
                raise ConnectionError("No hay conexión TCP activa.")
            effective = 2.0 if write_timeout is None else min(float(write_timeout), 2.5)
            old_timeout = sock.gettimeout()
            try:
                sock.settimeout(effective)
                sock.sendall(data)
            except OSError as exc:
                msg = f"Error al escribir TCP: {exc}"
                self._notify_link_lost(msg)
                raise ConnectionError(msg) from exc
            finally:
                try:
                    if self._sock is not None:
                        self._sock.settimeout(old_timeout if old_timeout is not None else RECV_TIMEOUT_S)
                except OSError:
                    pass

    def send_text(self, text: str, line_ending: str = "\r\n") -> None:
        payload = text.rstrip("\r\n") + line_ending
        self.send(payload.encode("ascii", errors="replace"))

    def _read_loop(self) -> None:
        buffer = bytearray()
        idle_empty = 0
        while not self._stop_event.is_set():
            sock = self._sock
            if sock is None:
                break
            try:
                chunk = sock.recv(256)
            except socket.timeout:
                idle_empty += 1
                if buffer and idle_empty >= 2:
                    self._emit_data(bytes(buffer))
                    buffer.clear()
                    idle_empty = 0
                continue
            except OSError as exc:
                if not self._stop_event.is_set():
                    self._notify_link_lost(f"Error de lectura TCP: {exc}")
                break

            if not chunk:
                # peer closed
                if not self._stop_event.is_set():
                    self._notify_link_lost("Conexión TCP cerrada por el remoto")
                break

            idle_empty = 0
            buffer.extend(chunk)
            while True:
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

        if buffer:
            self._emit_data(bytes(buffer))
            buffer.clear()

        if self._on_status and not self._stop_event.is_set() and not self._link_lost:
            self._notify_link_lost("Conexión TCP interrumpida")

    def _emit_data(self, raw: bytes) -> None:
        if not self._on_data:
            return
        try:
            text = raw.decode("ascii", errors="replace")
        except Exception:
            text = repr(raw)
        if text:
            self._on_data(text)
