"""Cola de comandos serial: envía y espera respuesta antes del siguiente."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, List, Optional, Union


OnLog = Callable[[str, str], None]
OnStep = Callable[[str, str], None]
OnDone = Callable[[], None]
OnBusy = Callable[[bool], None]

CommandInput = Union[str, "QueuedCommand"]


@dataclass
class QueuedCommand:
    text: str
    multiline_ms: int = 0
    rx_timeout_ms: int = 0
    write_timeout_s: float = 3.0
    wait_rx: Optional[bool] = None  # None = usar el default de la secuencia


class SerialCommandQueue:
    """Ejecuta comandos uno a uno. El envío serial corre en hilo aparte (no congela la UI)."""

    def __init__(
        self,
        send_command: Callable[..., bool],
        on_log: OnLog,
        pause_ms: int = 400,
        rx_timeout_ms: int = 10000,
        schedule: Optional[Callable[[int, Callable[[], None]], None]] = None,
        on_busy: Optional[OnBusy] = None,
    ) -> None:
        self._send = send_command
        self._on_log = on_log
        self._on_busy = on_busy
        self._pause_ms = pause_ms
        self._rx_timeout_ms = rx_timeout_ms
        self._schedule = schedule or (lambda _ms, fn: fn())
        self._items: List[QueuedCommand] = []
        self._wait_rx_default = False
        self._waiting_rx = False
        self._busy = False
        self._on_step: Optional[OnStep] = None
        self._on_done: Optional[OnDone] = None
        self._last_rx = ""
        self._last_cmd = ""
        self._rx_timer_gen = 0
        self._multiline_ms = 0
        self._rx_lines: List[str] = []
        self._active_rx_timeout_ms = rx_timeout_ms
        self._send_gen = 0
        self._pending_wait_rx = False

    @property
    def is_busy(self) -> bool:
        return self._busy

    @property
    def last_command(self) -> str:
        return self._last_cmd

    @property
    def last_response(self) -> str:
        return self._last_rx

    def _set_busy(self, value: bool) -> None:
        self._busy = value
        if self._on_busy:
            try:
                self._on_busy(value)
            except Exception:
                pass

    @staticmethod
    def _normalize(commands: List[CommandInput]) -> List[QueuedCommand]:
        items: List[QueuedCommand] = []
        for cmd in commands:
            if isinstance(cmd, QueuedCommand):
                if cmd.text.strip():
                    items.append(cmd)
            elif str(cmd).strip():
                items.append(QueuedCommand(str(cmd)))
        return items

    def start(
        self,
        commands: List[CommandInput],
        *,
        on_step: Optional[OnStep] = None,
        on_done: Optional[OnDone] = None,
        wait_rx: bool = True,
    ) -> None:
        if self._busy:
            raise RuntimeError("Ya hay una secuencia en curso.")
        self._items = self._normalize(commands)
        self._wait_rx_default = wait_rx
        self._on_step = on_step
        self._on_done = on_done
        self._last_rx = ""
        self._last_cmd = ""
        self._multiline_ms = 0
        self._rx_lines = []
        self._waiting_rx = False
        self._active_rx_timeout_ms = self._rx_timeout_ms
        if self._items:
            self._set_busy(True)
            self._on_log("INFO", f"Secuencia iniciada ({len(self._items)} comandos)")
            self._send_next()
        elif on_done:
            on_done()

    def cancel(self) -> None:
        was_busy = self._busy
        self._rx_timer_gen += 1
        self._send_gen += 1
        self._items.clear()
        self._waiting_rx = False
        self._multiline_ms = 0
        self._rx_lines = []
        self._active_rx_timeout_ms = self._rx_timeout_ms
        if was_busy:
            self._set_busy(False)
            self._on_log("INFO", "Secuencia cancelada")

    def on_rx(self, text: str) -> None:
        if not self._busy or not self._waiting_rx:
            return
        line = text.strip()
        if not line or self._should_ignore_rx(self._last_cmd, line):
            return

        # Download i: "NO" = sin horarios → cerrar ya (no esperar multilinea).
        if self._last_cmd.strip().lower() == "i" and line.upper() == "NO":
            self._rx_timer_gen += 1
            self._last_rx = line
            if self._on_step:
                self._on_step(self._last_cmd, self._last_rx)
            self._waiting_rx = False
            self._multiline_ms = 0
            self._rx_lines = []
            self._schedule(self._pause_ms, self._send_next)
            return

        if self._multiline_ms > 0:
            self._rx_lines.append(line)
            self._last_rx = "\n".join(self._rx_lines)
            self._rx_timer_gen += 1
            gen = self._rx_timer_gen
            self._schedule(self._multiline_ms, lambda: self._complete_multiline_rx(gen))
            return

        self._rx_timer_gen += 1
        self._last_rx = line
        if self._on_step:
            self._on_step(self._last_cmd, self._last_rx)
        self._waiting_rx = False
        self._schedule(self._pause_ms, self._send_next)

    @staticmethod
    def _should_ignore_rx(cmd: str, line: str) -> bool:
        if cmd.strip().lower() == "i" and line.upper() == "OK":
            return True
        return False

    def _complete_multiline_rx(self, gen: int) -> None:
        if not self._busy or not self._waiting_rx or gen != self._rx_timer_gen:
            return
        if self._on_step:
            self._on_step(self._last_cmd, self._last_rx)
        self._waiting_rx = False
        self._multiline_ms = 0
        self._rx_lines = []
        self._schedule(self._pause_ms, self._send_next)

    def on_unlock(self) -> None:
        """Tras unlock: si el comando pendiente era login, lo da por OK; si no, reintenta el comando."""
        if not self._busy or not self._last_cmd or not self._waiting_rx:
            return
        # Login exitoso: UnLock es la respuesta esperada, no hay que reenviar PWR666666.
        if self._last_cmd.strip().upper() == "PWR666666":
            self.on_rx("UnLock")
            return
        self._on_log("INFO", f"Reintentando {self._last_cmd} tras unlock…")
        self._rx_timer_gen += 1
        self._rx_lines = []
        self._pending_wait_rx = True
        self._dispatch_send(self._last_cmd, 3.0, after_retry=True)

    def on_send_failed(self) -> None:
        """Llamar si falló el envío: libera la cola para no quedar bloqueada."""
        if not self._busy:
            return
        self._rx_timer_gen += 1
        self._send_gen += 1
        self._items.clear()
        self._waiting_rx = False
        self._multiline_ms = 0
        self._rx_lines = []
        self._active_rx_timeout_ms = self._rx_timeout_ms
        self._set_busy(False)
        self._on_log("WARN", "Secuencia interrumpida por error de comunicación")

    def _arm_rx_timeout(self) -> None:
        if not self._waiting_rx:
            return
        gen = self._rx_timer_gen
        self._schedule(self._active_rx_timeout_ms, lambda: self._rx_timeout(gen))

    def _rx_timeout(self, gen: int) -> None:
        if not self._busy or not self._waiting_rx or gen != self._rx_timer_gen:
            return
        if self._multiline_ms > 0 and self._rx_lines:
            self._complete_multiline_rx(gen)
            return
        self._on_log("WARN", f"Sin respuesta a {self._last_cmd!r} (timeout). Siguiente…")
        self._waiting_rx = False
        self._multiline_ms = 0
        self._rx_lines = []
        self._schedule(self._pause_ms, self._send_next)

    def _send_next(self) -> None:
        if not self._busy:
            return
        if not self._items:
            self._finish()
            return
        item = self._items.pop(0)
        self._last_cmd = item.text
        self._multiline_ms = item.multiline_ms
        self._active_rx_timeout_ms = item.rx_timeout_ms or self._rx_timeout_ms
        self._rx_lines = []
        wait = self._wait_rx_default if item.wait_rx is None else item.wait_rx
        self._pending_wait_rx = wait
        write_to = item.write_timeout_s if item.write_timeout_s > 0 else 3.0
        self._dispatch_send(item.text, write_to, after_retry=False)

    def _dispatch_send(self, text: str, write_timeout_s: float, *, after_retry: bool) -> None:
        gen = self._send_gen

        def worker() -> None:
            try:
                ok = bool(self._send(text, write_timeout_s))
            except Exception:
                ok = False
            # Volver al hilo de UI vía schedule (Tk after).
            self._schedule(0, lambda: self._after_send(gen, ok, text, after_retry=after_retry))

        threading.Thread(target=worker, name="SerialSend", daemon=True).start()

    def _after_send(self, gen: int, ok: bool, text: str, *, after_retry: bool) -> None:
        if gen != self._send_gen or not self._busy:
            return
        if not ok:
            self.on_send_failed()
            return
        # TX log solo en hilo UI (nunca desde el worker).
        shown = text.replace("\r", "\\r").replace("\n", "\\n")
        self._on_log("TX", shown)
        if after_retry:
            self._waiting_rx = True
            self._arm_rx_timeout()
            return
        wait = self._pending_wait_rx
        if wait:
            self._waiting_rx = True
            self._arm_rx_timeout()
        else:
            self._waiting_rx = False
            self._schedule(self._pause_ms, self._send_next)

    def _finish(self) -> None:
        self._rx_timer_gen += 1
        self._waiting_rx = False
        self._multiline_ms = 0
        self._rx_lines = []
        self._set_busy(False)
        self._on_log("INFO", "Secuencia finalizada")
        if self._on_done:
            self._on_done()
