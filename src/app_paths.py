"""Rutas del proyecto (desarrollo y ejecutable PyInstaller)."""

from __future__ import annotations

import sys
from pathlib import Path


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[1]


def config_dir() -> Path:
    path = project_root() / "config"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = project_root() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return config_dir() / "settings.json"
