"""Cola de comandos serial: envía y espera respuesta antes del siguiente."""

from __future__ import annotations

from typing import Callable, List, Optional


OnLog = Callable[[str, str], None]
OnStep = Callable[[str, str], None]
OnDone = Callable[[], None]


class SerialCommandQueue:
    """Ejecuta comandos uno a uno, esperando RX entre cada envío."""

    def __init__(
        self,
        send_command: Callable[[str], None],
        on_log: OnLog,
        pause_ms: int = 350,
        schedule: Optional[Callable[[int, Callable[[], None]], None]] = None,
    ) -> None:
        self._send = send_command
        self._on_log = on_log
        self._pause_ms = pause_ms
        self._schedule = schedule or (lambda _ms, fn: fn())
        self._items: List[str] = []
        self._wait_rx = False
        self._busy = False
        self._on_step: Optional[OnStep] = None
        self._on_done: Optional[OnDone] = None
        self._last_rx = ""
        self._last_cmd = ""

    @property
    def is_busy(self) -> bool:
        return self._busy

    @property
    def last_response(self) -> str:
        return self._last_rx

    def start(
        self,
        commands: List[str],
        *,
        on_step: Optional[OnStep] = None,
        on_done: Optional[OnDone] = None,
        wait_rx: bool = True,
    ) -> None:
        if self._busy:
            raise RuntimeError("Ya hay una secuencia en curso.")
        self._items = [c for c in commands if c.strip()]
        self._wait_rx = wait_rx
        self._on_step = on_step
        self._on_done = on_done
        self._busy = bool(self._items)
        self._last_rx = ""
        self._last_cmd = ""
        if self._busy:
            self._on_log("INFO", f"Secuencia iniciada ({len(self._items)} comandos)")
            self._send_next()
        elif on_done:
            on_done()

    def cancel(self) -> None:
        self._items.clear()
        self._busy = False
        self._wait_rx = False
        self._on_log("INFO", "Secuencia cancelada")

    def on_rx(self, text: str) -> None:
        if not self._busy or not self._wait_rx:
            return
        self._last_rx = text.strip()
        if self._on_step:
            self._on_step(self._last_cmd, self._last_rx)
        self._wait_rx = False
        self._schedule(self._pause_ms, self._send_next)

    def _send_next(self) -> None:
        if not self._items:
            self._finish()
            return
        cmd = self._items.pop(0)
        self._last_cmd = cmd
        self._send(cmd)
        if self._wait_rx:
            self._wait_rx = True
        else:
            self._schedule(self._pause_ms, self._send_next)

    def _finish(self) -> None:
        self._busy = False
        self._wait_rx = False
        self._on_log("INFO", "Secuencia finalizada")
        if self._on_done:
            self._on_done()
