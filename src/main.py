"""Punto de entrada — Proyecto Colectores."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    src_dir = Path(__file__).resolve().parent
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    from ui.app_window import run_app

    run_app()


if __name__ == "__main__":
    main()
