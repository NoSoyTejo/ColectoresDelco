"""Registro de sesión en archivo para soporte."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional, TextIO

from app_paths import logs_dir


class SessionLog:
    """Escribe cada evento de la UI en logs/sesion_YYYYMMDD_HHMMSS.log."""

    def __init__(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path: Path = logs_dir() / f"sesion_{stamp}.log"
        self._fh: Optional[TextIO] = None
        self._open()
        self.write("INFO", f"Log iniciado: {self.path}")

    def _open(self) -> None:
        self._fh = self.path.open("a", encoding="utf-8")

    def write(self, kind: str, message: str) -> None:
        if self._fh is None:
            return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._fh.write(f"[{ts}] {kind:<3} {message}\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self.write("INFO", "Sesión finalizada")
            self._fh.close()
            self._fh = None
