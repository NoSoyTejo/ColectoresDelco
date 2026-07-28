"""Horarios de lectura (polling) del colector — Comados.doc §10–§11."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class PollingSlot:
    index: int
    start: str  # HH:MM
    end: str  # HH:MM

    @property
    def start_hhmm(self) -> str:
        return self.start.replace(":", "")

    @property
    def end_hhmm(self) -> str:
        return self.end.replace(":", "")

    def display(self) -> str:
        """Formato del manual: 1 - De 00:00 até 03:59"""
        return f"{self.index} - De {self.start} até {self.end}"


# Ejemplo documentado en Comados.doc §10 (3 horarios polling típicos).
STANDARD_POLLING_SLOTS: Tuple[Tuple[str, str], ...] = (
    ("00:00", "03:59"),
    ("04:00", "15:59"),
    ("16:00", "23:59"),
)

_SLOT_PATTERN = re.compile(
    r"(\d+)\s*[-–]\s*(?:De\s*)?"
    r"(\d{1,2}:\d{2}|\d{4})\s*"
    r"(?:at[eé]|hasta|ate|to)\s*"
    r"(\d{1,2}:\d{2}|\d{4})",
    re.IGNORECASE,
)

_SIMPLE_RANGE = re.compile(
    r"(\d{1,2}:\d{2}|\d{4})\s*[-–]\s*(\d{1,2}:\d{2}|\d{4})",
    re.IGNORECASE,
)

_TOTAL_PATTERN = re.compile(
    r"Total de pool(?:ing|ings)?s?:\s*(\d+)",
    re.IGNORECASE,
)


def parse_polling_total(raw: str) -> Optional[int]:
    """
    Extrae 'Total de poolings: N' — cantidad de horarios ingresados en el colector (§10).
    """
    match = _TOTAL_PATTERN.search(raw or "")
    if not match:
        return None
    return int(match.group(1))


def parse_polling_response(raw: str) -> List[PollingSlot]:
    """
    Parsea respuesta del comando i (Comados.doc §10):

      Vendo horarios de pooling
      Total de poolings: 3     ← cantidad de horarios en el colector
      1 - De 00:00 até 03:59
      2 - De 04:00 até 15:59
      3 - De 16:00 até  2359
    """
    slots: List[PollingSlot] = []
    seen: set[tuple[str, str]] = set()

    for match in _SLOT_PATTERN.finditer(raw):
        idx, start, end = match.groups()
        key = (_norm_time(start), _norm_time(end))
        if key in seen:
            continue
        seen.add(key)
        slots.append(PollingSlot(index=int(idx), start=key[0], end=key[1]))

    if not slots:
        for i, match in enumerate(_SIMPLE_RANGE.finditer(raw), start=1):
            start, end = match.groups()
            key = (_norm_time(start), _norm_time(end))
            if key in seen:
                continue
            seen.add(key)
            slots.append(PollingSlot(index=i, start=key[0], end=key[1]))

    return slots


def format_polling_table(slots: List[PollingSlot], total: Optional[int] = None) -> str:
    """Tabla legible estilo manual §10."""
    count = total if total is not None else len(slots)
    lines = [
        "Vendo horarios de pooling",
        f"Total de poolings: {count}     (cantidad de horarios polling en el colector)",
        "",
    ]
    if slots:
        for s in slots:
            lines.append(f"{s.display()}     (horario polling)")
    else:
        lines.append("(sin franjas listadas)")
    return "\n".join(lines)


def polling_response_summary(raw: str) -> str:
    """Texto breve sobre la respuesta del comando i."""
    from protocol import is_empty_polling_response

    if is_empty_polling_response(raw):
        return "Sin horarios polling en el colector (NO / 0)"
    slots = parse_polling_response(raw)
    total = parse_polling_total(raw)
    if slots:
        if total is not None and total != len(slots):
            return f"Total de poolings: {total} | franjas leídas: {len(slots)}"
        return f"Total de poolings: {total if total is not None else len(slots)}"
    if total is not None:
        if total == 0:
            return "Total de poolings: 0 (sin horarios en el colector)"
        return f"Total de poolings: {total} (franjas no parseadas — revise log RX)"
    if re.search(r"horario|pooling|polling", raw, re.IGNORECASE):
        return "Respuesta recibida pero sin horarios parseables"
    if raw.strip():
        preview = raw.strip().replace("\n", " | ")[:120]
        return f"Respuesta no reconocida: {preview}"
    return "Sin respuesta al comando i (use QUIT antes)"


def default_full_day_slot() -> PollingSlot:
    return PollingSlot(index=1, start="00:00", end="23:59")


def default_standard_polling_slots() -> List[PollingSlot]:
    """
    Las 3 franjas del ejemplo Comados.doc §10:
      1 - De 00:00 até 03:59
      2 - De 04:00 até 15:59
      3 - De 16:00 até 23:59
    """
    return [
        PollingSlot(index=i, start=start, end=end)
        for i, (start, end) in enumerate(STANDARD_POLLING_SLOTS, start=1)
    ]


def parse_polling_line(line: str, index: int) -> Optional[PollingSlot]:
    """Formato editable: 00:00-03:59 o 00:00,03:59"""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    for sep in ("-", ",", ";", " "):
        if sep in line:
            parts = [p.strip() for p in line.split(sep) if p.strip()]
            if len(parts) >= 2:
                return PollingSlot(index=index, start=_norm_time(parts[0]), end=_norm_time(parts[1]))
    return None


def _norm_time(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) == 4:
        return f"{digits[0:2]}:{digits[2:4]}"
    if ":" in value:
        hh, mm = value.split(":", 1)
        return f"{int(hh):02d}:{int(mm):02d}"
    raise ValueError(f"Hora inválida: {value}")


def slots_to_lines(slots: List[PollingSlot]) -> str:
    return "\n".join(f"{s.start}-{s.end}" for s in slots)


def build_upload_commands(slots: List[PollingSlot]) -> List[str]:
    """
    Genera comandos para subir horarios al colector.
    Formato estimado (validar con equipo real): I + inicio(HHMM) + fin(HHMM)
    """
    commands: List[str] = []
    for slot in slots:
        commands.append(f"I{slot.start_hhmm}{slot.end_hhmm}")
    return commands
