"""Parseo de archivos para carga masiva de medidores."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from protocol import cmd_add_meter, pad_meter_id


@dataclass
class BulkAddLine:
    meter: str
    header: Optional[str] = None
    individual: bool = False
    tariff_suffix: Optional[str] = None
    raw_command: Optional[str] = None
    line_no: int = 0
    error: Optional[str] = None

    def to_command(self) -> str:
        if self.raw_command:
            return self.raw_command.strip().upper()
        if self.error:
            raise ValueError(self.error)
        if self.individual:
            return cmd_add_meter(self.meter, individual_tariff=True)
        if self.header:
            return cmd_add_meter(self.meter, self.header)
        if self.tariff_suffix is not None:
            meter = pad_meter_id(self.meter)
            suffix = re.sub(r"\D", "", self.tariff_suffix).zfill(2)[-2:]
            return f"A{meter}{suffix}"
        raise ValueError("Línea sin datos suficientes para armar comando A")


def _parse_a_command(line: str) -> BulkAddLine:
    cmd = line.strip().upper()
    if not cmd.startswith("A") or len(cmd) < 14:
        return BulkAddLine(meter="", raw_command=cmd, error="Comando A inválido")
    body = cmd[1:]
    if len(body) == 14:
        return BulkAddLine(meter=body[:12], individual=True, raw_command=cmd)
    if len(body) == 26:
        return BulkAddLine(meter=body[:12], header=body[12:24], raw_command=cmd)
    if len(body) == 16:
        return BulkAddLine(
            meter=body[:12],
            tariff_suffix=body[12:14],
            raw_command=cmd,
        )
    return BulkAddLine(meter="", raw_command=cmd, error="Longitud de comando A no reconocida")


def parse_bulk_text(content: str) -> List[BulkAddLine]:
    """Interpreta CSV/TXT con medidores o comandos A completos."""
    lines = content.splitlines()
    results: List[BulkAddLine] = []

    for idx, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        if line.upper().startswith("A"):
            item = _parse_a_command(line)
            item.line_no = idx
            results.append(item)
            continue

        if ";" in line:
            parts = [p.strip() for p in line.split(";")]
        elif "," in line:
            parts = [p.strip() for p in line.split(",")]
        elif "\t" in line:
            parts = [p.strip() for p in line.split("\t")]
        else:
            parts = re.split(r"\s+", line)

        parts = [p for p in parts if p]
        try:
            if len(parts) == 1:
                results.append(
                    BulkAddLine(meter=parts[0], individual=True, line_no=idx)
                )
            elif len(parts) == 2:
                meter, second = parts
                if second in ("00", "0", "CP4", "cp4", "individual"):
                    results.append(
                        BulkAddLine(meter=meter, individual=True, line_no=idx)
                    )
                elif len(re.sub(r"\D", "", second)) <= 2:
                    results.append(
                        BulkAddLine(meter=meter, tariff_suffix=second, line_no=idx)
                    )
                else:
                    results.append(
                        BulkAddLine(meter=meter, header=second, line_no=idx)
                    )
            else:
                results.append(
                    BulkAddLine(
                        meter="",
                        line_no=idx,
                        error=f"Formato no reconocido: {line}",
                    )
                )
        except ValueError as exc:
            results.append(BulkAddLine(meter="", line_no=idx, error=str(exc)))

    return results


def parse_bulk_csv_file(path: str) -> List[BulkAddLine]:
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return parse_bulk_text(fh.read())


def preview_commands(items: List[BulkAddLine]) -> List[str]:
    preview: List[str] = []
    for item in items:
        try:
            preview.append(item.to_command())
        except ValueError:
            preview.append(f"ERROR línea {item.line_no}: {item.error}")
    return preview


def build_bulk_add_sequence(items: List[BulkAddLine], *, with_quit_wake_zoom: bool = True) -> List[str]:
    from protocol import CMD_QUIT, CMD_WAKE, CMD_ZOOM

    commands: List[str] = []
    if with_quit_wake_zoom:
        commands.append(CMD_QUIT)
    for item in items:
        if item.error:
            continue
        commands.append(item.to_command())
    if with_quit_wake_zoom:
        commands.extend([CMD_WAKE, CMD_ZOOM])
    return commands
