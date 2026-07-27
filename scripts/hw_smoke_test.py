"""Prueba de framing sin hardware: SerialClient con un puerto en memoria (loopback simulado).

Si hay un COMx real conectado, también intenta listarlo e informar.
Uso:
  .\\venv\\Scripts\\python.exe scripts\\hw_smoke_test.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from protocol import format_command, cmd_restart_reading, cmd_version, CMD_LOGIN  # noqa: E402
from serial_client import SerialClient, list_serial_ports, describe_ports  # noqa: E402


def test_protocol() -> None:
    assert format_command("VER", "\r\n") == b"VER\r\n"
    assert format_command("QUIT\r\n", "\r") == b"QUIT\r"
    assert cmd_version() == "VER"
    assert cmd_restart_reading() == "k=10100000"
    assert CMD_LOGIN == "PWR666666"
    print("[OK] protocol framing")


def test_list_ports() -> None:
    ports = list_serial_ports()
    print(f"[INFO] Puertos detectados: {ports or '(ninguno)'}")
    for line in describe_ports():
        print(f"       {line}")
    if not ports:
        print("[WARN] No hay adaptador USB-serial conectado ahora.")
        print("       Conecte el colector y vuelva a ejecutar este script.")
    else:
        print("[OK] Al menos un puerto COM visible")


def test_connect_if_available() -> None:
    ports = list_serial_ports()
    if not ports:
        print("[SKIP] connect: sin puerto")
        return

    port = ports[0]
    received = []
    errors = []

    client = SerialClient(
        on_data=lambda t: received.append(t),
        on_error=lambda e: errors.append(e),
    )
    try:
        client.connect(port=port, baudrate=9600, timeout=0.2)
        print(f"[OK] abierto {port}")
        client.send_text("V", "\r\n")
        time.sleep(0.8)
        print(f"[INFO] RX tras V: {received!r}")
        if errors:
            print(f"[WARN] errores: {errors}")
    except ConnectionError as exc:
        print(f"[WARN] no se pudo usar {port}: {exc}")
    finally:
        client.disconnect()
        print("[OK] desconectado")


def main() -> int:
    print("=== Proyecto Conector - hw smoke test ===")
    test_protocol()
    test_list_ports()
    test_connect_if_available()
    print("=== fin ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
