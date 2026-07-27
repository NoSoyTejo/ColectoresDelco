"""Comandos del colector según docs/ComandosColectores.doc."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


DEFAULT_LINE_ENDING = "\r\n"

# Secuencia habitual tras detener el colector (docs págs. 3-9).
CMD_QUIT = "QUIT"
CMD_WAKE = "WAKE"
CMD_ZOOM = "ZOOM"

# Login cuando responde "lock"
CMD_LOGIN = "PWR666666"

# Información
CMD_VERSION = "VER"
CMD_COUNT_METERS = "O"
CMD_POLLING_SCHEDULE = "i"
CMD_DELETE_BASE = "DEL"

# Reinicio de ciclo de lectura (AMRsw debe quedar en 10100000)
CMD_K_START_READING = "k=10100000"
AMRSW_OK = "10100000"
AMRSW_BAD = "10000000"


def format_command(command: str, line_ending: str = DEFAULT_LINE_ENDING) -> bytes:
    """Prepara un comando de texto para enviar por el puerto serial."""
    text = command.rstrip("\r\n")
    return (text + line_ending).encode("ascii", errors="replace")


def pad_meter_id(meter_id: str) -> str:
    """Normaliza el número de medidor a 12 dígitos (ceros a la izquierda)."""
    digits = "".join(ch for ch in meter_id.strip() if ch.isdigit())
    if not digits:
        raise ValueError("El medidor debe contener dígitos.")
    if len(digits) > 12:
        raise ValueError("El medidor no puede tener más de 12 dígitos.")
    return digits.zfill(12)


def pad_header(header: str) -> str:
    """Normaliza la cabecera a 12 dígitos."""
    return pad_meter_id(header)


# --- Lectura directa ---------------------------------------------------------

def cmd_direct_read(
    meter_id: str,
    *,
    with_reading: bool = True,
    with_datetime: bool = True,
    with_header: bool = True,
    with_display_status: bool = True,
) -> str:
    """
    Lectura directa: R + medidor(12) + F03 + flags.

    Flags documentados:
      F18 lectura, F12 fecha/hora, F16 cabecera, F06 status display.
    En la práctica el bloque se arma como F03 + dígitos de flags, p.ej.:
      R000023388410F0318121606
    """
    meter = pad_meter_id(meter_id)
    flags = ""
    if with_reading:
        flags += "18"
    if with_datetime:
        flags += "12"
    if with_header:
        flags += "16"
    if with_display_status:
        flags += "06"
    if not flags:
        raise ValueError("Seleccione al menos un dato a leer.")
    return f"R{meter}F03{flags}"


# --- Display / refresco ------------------------------------------------------

def cmd_display(meter_id: str, on: bool) -> str:
    """Encender (01) o apagar (00) display: XFKG + medidor(12) + 01/00."""
    meter = pad_meter_id(meter_id)
    return f"XFKG{meter}{'01' if on else '00'}"


def cmd_forced_refresh(meter_id: str) -> str:
    """Refresco forzado: SGXF + medidor(12)."""
    return f"SGXF{pad_meter_id(meter_id)}"


# --- Medidores en sistema ----------------------------------------------------

def cmd_delete_meter(meter_id: str) -> str:
    """Borrar medidor: E + medidor(12). Requiere QUIT antes."""
    return f"E{pad_meter_id(meter_id)}"


def cmd_add_meter(meter_id: str, header: Optional[str] = None, individual_tariff: bool = False) -> str:
    """
    Agregar medidor.
      Con cabecera: A + medidor(12) + cabecera(12)
      Individual CP4: A + medidor(12) + 00
    """
    meter = pad_meter_id(meter_id)
    if individual_tariff:
        return f"A{meter}00"
    if header:
        return f"A{meter}{pad_header(header)}"
    return f"A{meter}"


# --- Reloj -------------------------------------------------------------------

def cmd_set_clock(yymmddhhmmss: str) -> str:
    """Configurar fecha/hora: C + 12 dígitos (AAMMDDHHMMSS)."""
    digits = "".join(ch for ch in yymmddhhmmss if ch.isdigit())
    if len(digits) != 12:
        raise ValueError("La fecha/hora debe tener 12 dígitos: AAMMDDHHMMSS.")
    return f"C{digits}"


def cmd_restart_reading() -> str:
    """Comando K documentado: k=10100000."""
    return CMD_K_START_READING


def cmd_version() -> str:
    return CMD_VERSION


def cmd_login() -> str:
    return CMD_LOGIN


# --- Parsers -----------------------------------------------------------------

@dataclass
class DirectReadResult:
    meter_id: str
    flags: str
    unknown: str
    reading_raw: str
    reading_value: Optional[float]
    datetime_raw: str
    datetime_text: str
    header: str
    display_on: Optional[bool]
    raw: str


def parse_reading_value(raw12: str) -> Optional[float]:
    """
    Interpreta lectura de 12 dígitos según el manual:
      000025710040 -> 2571.40
    Formato observado: 8 dígitos enteros + 2 ceros (coma) + 2 decimales.
    """
    digits = "".join(ch for ch in raw12 if ch.isdigit())
    if len(digits) != 12:
        return None
    whole = str(int(digits[0:8]))
    frac = digits[10:12]
    try:
        return float(f"{whole}.{frac}")
    except ValueError:
        return None


def parse_datetime_raw(raw12: str) -> str:
    """090527060924 -> 2009-05-27 06:09:24 (año en 2 dígitos)."""
    digits = "".join(ch for ch in raw12 if ch.isdigit())
    if len(digits) != 12:
        return raw12
    yy, mo, dd, hh, mi, ss = (
        digits[0:2],
        digits[2:4],
        digits[4:6],
        digits[6:8],
        digits[8:10],
        digits[10:12],
    )
    return f"20{yy}-{mo}-{dd} {hh}:{mi}:{ss}"


def parse_direct_read_response(raw: str) -> Optional[DirectReadResult]:
    """
    Ejemplo documentado:
      000023388410 F0318121606 01 000025710040 090527060924 000090059613 01
    """
    parts = raw.strip().split()
    if len(parts) < 6:
        return None
    meter = parts[0]
    flags = parts[1]
    unknown = parts[2]
    reading_raw = parts[3]
    datetime_raw = parts[4]
    header = parts[5]
    display_flag = parts[6] if len(parts) > 6 else ""
    display_on: Optional[bool]
    if display_flag == "01":
        display_on = True
    elif display_flag == "00":
        display_on = False
    else:
        display_on = None
    return DirectReadResult(
        meter_id=meter,
        flags=flags,
        unknown=unknown,
        reading_raw=reading_raw,
        reading_value=parse_reading_value(reading_raw),
        datetime_raw=datetime_raw,
        datetime_text=parse_datetime_raw(datetime_raw),
        header=header,
        display_on=display_on,
        raw=raw.strip(),
    )


def parse_response(raw: str) -> Optional[str]:
    text = raw.strip()
    return text if text else None


def describe_lock_state(raw: str) -> Optional[str]:
    low = raw.strip().lower()
    if "lock" in low and "unlock" not in low:
        return "Colector bloqueado: enviar login PWR666666"
    if "unlock" in low:
        return "Colector desbloqueado (login OK)"
    return None


def sequence_after_maintenance() -> Tuple[str, str]:
    """Secuencia típica al terminar mantenimiento: WAKE + ZOOM (QUIT ya se envió antes)."""
    return (CMD_WAKE, CMD_ZOOM)
