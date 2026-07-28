"""Comandos del colector según docs/Comados.doc."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import re


DEFAULT_LINE_ENDING = "\r\n"

# Flujo documentado: QUIT → comando → WAKE → ZOOM
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

# Lectura directa (Comados.doc §1): F18 lectura + F12 fecha. Cabecera/display obsoletos.
READ_FLAGS_BASIC = "1812"
READ_FLAGS_LEGACY_FULL = "18121606"  # ejemplo antiguo del manual

# Flags lectura multi-tarifa (RemoteCOM / Leitura Massiva — no en Comados.doc)
# F18=T1, F20=T2, F21=T3, F22=T4, F19=Total, F12=Fecha
READ_FLAGS_MULTITARIFF = "182021221912"
READ_FLAGS_MULTITARIFF_WITH_HEADER = "18202122191216"


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


FLAG_LABELS = {
    "18": "T1",
    "20": "T2",
    "21": "T3",
    "22": "T4",
    "19": "Total",
    "12": "Fecha",
    "16": "Cabecera",
    "06": "Display",
}


def build_read_flags(
    *,
    t1: bool = True,
    t2: bool = True,
    t3: bool = True,
    t4: bool = True,
    total: bool = True,
    datetime: bool = True,
    header: bool = False,
    display: bool = False,
) -> str:
    flags = ""
    if t1:
        flags += "18"
    if t2:
        flags += "20"
    if t3:
        flags += "21"
    if t4:
        flags += "22"
    if total:
        flags += "19"
    if datetime:
        flags += "12"
    if header:
        flags += "16"
    if display:
        flags += "06"
    if not flags:
        raise ValueError("Seleccione al menos un dato a leer.")
    return flags


def cmd_read_by_index(index: int, flags: str = READ_FLAGS_MULTITARIFF) -> str:
    """Lectura por índice en el colector: R0000F03…, R0001F03…"""
    if index < 0 or index > 9999:
        raise ValueError("Índice fuera de rango (0-9999).")
    return f"R{index:04d}F03{flags}"


def cmd_read_collector_clock() -> str:
    """RemoteCOM usa R0000F0312: fecha/hora de la última lectura del medidor índice 0 (flag F12)."""
    return cmd_read_by_index(0, "12")


def cmd_download_polling() -> str:
    return CMD_POLLING_SCHEDULE


def cmd_multitariff_read(meter_id: str, flags: str = READ_FLAGS_MULTITARIFF) -> str:
    """Lectura directa con T1-T4: R + medidor(12) + F03 + flags."""
    return f"R{pad_meter_id(meter_id)}F03{flags}"


def pad_header(header: str) -> str:
    """Normaliza la cabecera a 12 dígitos."""
    return pad_meter_id(header)


# --- Lectura directa ---------------------------------------------------------

def cmd_direct_read(
    meter_id: str,
    *,
    with_reading: bool = True,
    with_datetime: bool = True,
    with_header: bool = False,
    with_display_status: bool = False,
) -> str:
    """
    Lectura directa (Comados.doc §1): R + medidor(12) + F03 + flags.

    Flags vigentes: F18 lectura, F12 fecha/hora.
    Cabecera (F16) y display (F06) están marcados como obsoletos en el manual.
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


def cmd_set_clock_now(when: Optional[datetime] = None) -> str:
    """Arma C + AAMMDDHHMMSS con la hora actual (o la indicada)."""
    dt = when or datetime.now()
    return cmd_set_clock(dt.strftime("%y%m%d%H%M%S"))


def cmd_restart_reading() -> str:
    """Comando K documentado: k=10100000."""
    return CMD_K_START_READING


def cmd_version() -> str:
    return CMD_VERSION


def cmd_login() -> str:
    return CMD_LOGIN


# --- Parsers -----------------------------------------------------------------

@dataclass
class MeterReading:
    meter_id: str
    flags: str
    status: str
    t1: Optional[float]
    t2: Optional[float]
    t3: Optional[float]
    t4: Optional[float]
    total: Optional[float]
    datetime_raw: str
    datetime_text: str
    header: str
    display_on: Optional[bool]
    raw: str


@dataclass
class CollectorInfo:
    meter_count: Optional[int]
    amrsw: Optional[str]
    raw: str


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


def parse_collector_clock(raw: str) -> Optional[str]:
    """Extrae fecha/hora de una respuesta con flag F12."""
    reading = parse_meter_reading(raw)
    if reading and reading.datetime_text:
        return reading.datetime_text
    parts = raw.strip().split()
    for part in parts:
        digits = "".join(ch for ch in part if ch.isdigit())
        if len(digits) == 12:
            text = parse_datetime_raw(digits)
            if "-" in text:
                return text
    return None


def parse_collector_info(raw: str) -> Optional[CollectorInfo]:
    """Parsea respuesta del comando O (IDnum, AMRsw…)."""
    text = raw.strip()
    if not text:
        return None
    count_match = re.search(r"IDnum=N(\d+)", text, re.IGNORECASE)
    amr_match = re.search(r"AMRsw=(\d+)", text, re.IGNORECASE)
    count = int(count_match.group(1)) if count_match else None
    amrsw = amr_match.group(1) if amr_match else None
    if count is None and amrsw is None:
        return None
    return CollectorInfo(meter_count=count, amrsw=amrsw, raw=text)


def _flag_digits(flags_field: str) -> List[str]:
    """F03182021221912 -> ['18','20','21','22','19','12']"""
    digits = "".join(ch for ch in flags_field if ch.isdigit())
    if digits.startswith("03"):
        digits = digits[2:]
    chunks: List[str] = []
    i = 0
    while i < len(digits):
        if i + 1 < len(digits) and digits[i : i + 2] in FLAG_LABELS:
            chunks.append(digits[i : i + 2])
            i += 2
        else:
            i += 1
    return chunks


def parse_meter_reading(raw: str) -> Optional[MeterReading]:
    """
  Parsea lectura simple o multi-tarifa.

  Multi-tarifa (RemoteCOM):
    000023988383 F03182021221912 00 T1 T2 T3 T4 Total Fecha
    """
    parts = raw.strip().split()
    if len(parts) < 4:
        return None

    meter = parts[0]
    flags_field = parts[1]
    status = parts[2]
    flag_list = _flag_digits(flags_field)
    values = parts[3 : 3 + len(flag_list)]

    data: Dict[str, str] = {}
    for key, val in zip(flag_list, values):
        data[key] = val

    display_on: Optional[bool] = None
    if status in ("00", "01"):
        display_on = status == "01"

    return MeterReading(
        meter_id=meter,
        flags=flags_field,
        status=status,
        t1=parse_reading_value(data["18"]) if "18" in data else None,
        t2=parse_reading_value(data["20"]) if "20" in data else None,
        t3=parse_reading_value(data["21"]) if "21" in data else None,
        t4=parse_reading_value(data["22"]) if "22" in data else None,
        total=parse_reading_value(data["19"]) if "19" in data else None,
        datetime_raw=data.get("12", ""),
        datetime_text=parse_datetime_raw(data["12"]) if "12" in data else "",
        header=data.get("16", ""),
        display_on=display_on,
        raw=raw.strip(),
    )


def format_meter_reading_summary(reading: MeterReading) -> str:
    parts = [f"Medidor {reading.meter_id}"]
    if reading.t1 is not None:
        parts.append(f"T1={reading.t1}")
    if reading.t2 is not None:
        parts.append(f"T2={reading.t2}")
    if reading.t3 is not None:
        parts.append(f"T3={reading.t3}")
    if reading.t4 is not None:
        parts.append(f"T4={reading.t4}")
    if reading.total is not None:
        parts.append(f"Total={reading.total}")
    if reading.datetime_text:
        parts.append(f"Fecha={reading.datetime_text}")
    return " | ".join(parts)


def parse_direct_read_response(raw: str) -> Optional[DirectReadResult]:
    """
    Ejemplo documentado (legacy con cabecera/display):
      000023388410 F0318121606 01 000025710040 090527060924 000090059613 01

    Formato vigente (§1, solo F18+F12) vía parse_meter_reading:
      000023388410 F031812 00 000025710040 090527060924
    """
    meter_reading = parse_meter_reading(raw)
    if meter_reading and (meter_reading.t1 is not None or meter_reading.datetime_text):
        return DirectReadResult(
            meter_id=meter_reading.meter_id,
            flags=meter_reading.flags,
            unknown=meter_reading.status,
            reading_raw="",
            reading_value=meter_reading.t1,
            datetime_raw=meter_reading.datetime_raw,
            datetime_text=meter_reading.datetime_text,
            header=meter_reading.header or "",
            display_on=meter_reading.display_on,
            raw=raw.strip(),
        )

    parts = raw.strip().split()
    if len(parts) < 5:
        return None
    meter = parts[0]
    flags = parts[1]
    unknown = parts[2]
    reading_raw = parts[3]
    datetime_raw = parts[4]
    header = parts[5] if len(parts) > 5 else ""
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
    if is_lock_response(raw):
        return "Colector bloqueado: enviar login PWR666666"
    if is_unlock_response(raw):
        return "Colector desbloqueado (login OK)"
    return None


def describe_status_response(raw: str, last_command: str = "") -> Optional[str]:
    """Explica respuestas de estado/error del colector (WAKE, ZOOM, Upload, etc.)."""
    text = raw.strip()
    if not text:
        return None
    low = text.lower().replace(" ", "")
    cmd = last_command.strip()
    cmd_low = cmd.lower()

    if text.upper() == "OK":
        return None
    if "routererror" in low or "lasterror_router" in low:
        return (
            "WAKE rechazado (ROUTERERROR). Use QUIT, espere OK, "
            "luego QUIT → WAKE → ZOOM. Si persiste, reinicie USB."
        )
    if low.startswith("no") and ("error" in low or "last" in low):
        return (
            f"Comando rechazado: {text}. Detenga con QUIT y reintente WAKE + ZOOM."
        )
    if text.upper() == "NO":
        if cmd_low.startswith("i") and len(cmd) > 1:
            return (
                f"Upload rechazado (NO) al comando {cmd}. "
                "El manual (§11) no documenta el formato exacto; "
                "además el colector debe quedar SIN horarios previos. "
                "Pruebe Download (i): si ya hay franjas, no se pueden insertar."
            )
        if cmd_low == "wake":
            return "WAKE rechazado (NO). Envíe QUIT (OK) y luego QUIT → WAKE → ZOOM."
        if cmd_low == "zoom":
            return "ZOOM rechazado (NO). Envíe QUIT y luego QUIT → WAKE → ZOOM."
        if cmd:
            return f"Comando {cmd!r} rechazado (NO). Envíe QUIT y reintente."
        return "Comando rechazado (NO). Envíe QUIT y reintente."
    return None


def is_lock_response(raw: str) -> bool:
    low = raw.strip().lower()
    return low == "lock" or ("lock" in low and "unlock" not in low and len(low) <= 12)


def is_unlock_response(raw: str) -> bool:
    return "unlock" in raw.strip().lower()


def is_clock_only_reading(reading: MeterReading) -> bool:
    """True si la respuesta solo trae flag 12 (fecha/hora), sin tarifas."""
    return _flag_digits(reading.flags) == ["12"]


def sequence_after_maintenance() -> Tuple[str, str]:
    """Secuencia típica al terminar mantenimiento: WAKE + ZOOM (QUIT ya se envió antes)."""
    return (CMD_WAKE, CMD_ZOOM)
