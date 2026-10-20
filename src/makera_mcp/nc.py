from __future__ import annotations

import hashlib
import re
from pathlib import Path


def inspect_nc(path: str) -> dict:
    target = Path(path).resolve(strict=True)
    if target.suffix.lower() not in (".nc", ".gcode", ".tap") or not target.is_file():
        raise ValueError("Expected a local .nc/.gcode/.tap file")
    if target.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("NC file is larger than 32 MiB")
    content = target.read_bytes()
    text = content.decode("utf-8-sig", errors="replace")
    units, distance_modes, codes = set(), set(), set()
    words: dict[str, list[float]] = {a: [] for a in "XYZAFS"}
    for line in text.splitlines():
        line = re.sub(r"\([^)]*\)", "", line.split(";", 1)[0]).upper()
        for letter, number in re.findall(r"([A-Z])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))", line):
            value = float(number)
            if letter in ("G", "M"):
                code = letter + format(value, "g")
                codes.add(code)
                if code in ("G20", "G21"):
                    units.add(code)
                if code in ("G90", "G91"):
                    distance_modes.add(code)
            if letter in words:
                words[letter].append(value)
    return {
        "path": str(target), "bytes": len(content), "lines": len(text.splitlines()),
        "sha256": hashlib.sha256(content).hexdigest(), "unit_commands": sorted(units),
        "distance_mode_commands": sorted(distance_modes), "g_m_codes": sorted(codes),
        "literal_word_ranges": {key: {"min": min(values), "max": max(values)} for key, values in words.items() if values},
        "machining_approved": False,
        "limitations": ["Lexical inspection only; ranges are literal words, not reconstructed machine travel",
                        "Does not resolve modal offsets, arcs, expressions, units changes, subroutines or collisions",
                        "No Z1 Pro postprocessor compatibility or cutting safety is certified"],
    }
