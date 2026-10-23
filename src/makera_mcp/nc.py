from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path


def _code_lines(text: str):
    """Remove nested parenthesis and semicolon comments without reading commands in them."""
    depth = 0
    for number, raw in enumerate(text.splitlines(), 1):
        code = []
        for char in raw:
            if char == '(':
                depth += 1
            elif char == ')' and depth:
                depth -= 1
            elif char == ';' and not depth:
                break
            elif not depth:
                code.append(char)
        yield number, ''.join(code).upper().strip()


def audit_nc_sequence(text: str) -> dict:
    """Restricted standard-code diagnostics. Never infer Z1 firmware behavior."""
    spindle = None
    coolant = None
    last_start = last_stop = last_coolant_off = last_feed_motion = None
    coolant_used = False
    motion = None
    feed = None
    feed_mode = None
    inherited_feed_blocks = 0
    missing_feed_lines = []
    program_end = None
    percent_open = False
    uncertain_lines = []
    pauses = []
    last_executable = None
    for number, line in _code_lines(text):
        if not line:
            continue
        if line == '%':
            if percent_open:
                program_end = {'line': number, 'code': '%'}
                break
            percent_open = True
            continue
        if line.startswith('/') or any(c in line for c in '#[]='):
            uncertain_lines.append(number)
            continue
        words = re.findall(r'([A-Z])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))', line)
        remainder = re.sub(r'[A-Z]\s*[-+]?(?:\d+(?:\.\d*)?|\.\d+)', '', line)
        if remainder.strip() or any(not math.isfinite(float(v)) for _,v in words):
            uncertain_lines.append(number)
            continue
        codes = [a + format(float(v), 'g') for a, v in words if a in ('G', 'M')]
        for group in (('M3','M4','M5'),('M0','M1','M2','M30'),('G0','G1','G2','G3'),('G93','G94','G95')):
            if sum(code in group for code in codes) > 1:
                uncertain_lines.append(number)
        if any(code.startswith('M') and code not in ('M0','M1','M2','M3','M4','M5','M7','M8','M9','M30') for code in codes):
            uncertain_lines.append(number)
        if any(c in codes for c in ('M98', 'M99', 'G65')) or re.search(r'\b(IF|WHILE|GOTO|CALL|SUB)\b', line):
            uncertain_lines.append(number)
            continue
        last_executable = number
        for code in codes:
            if code in ('G0', 'G1', 'G2', 'G3'):
                motion = code
            if code in ('G93', 'G94', 'G95'):
                feed_mode = code
        f_words = [float(v) for a, v in words if a == 'F']
        if f_words:
            feed = f_words[-1]
        has_axes = any(a in 'XYZABC' for a, _ in words)
        # Non-motion coordinate setters and cycles cannot inherit a simple G1 interpretation.
        other_g = [c for c in codes if c.startswith('G') and c not in
                   ('G0','G1','G2','G3','G17','G18','G19','G20','G21','G40','G49','G54','G55','G56','G57','G58','G59','G80','G90','G91','G93','G94','G95')]
        if other_g:
            uncertain_lines.append(number)
            motion = None
        if has_axes and motion in ('G1', 'G2', 'G3'):
            last_feed_motion = number
            if feed is None or feed <= 0 or (feed_mode == 'G93' and not f_words):
                missing_feed_lines.append(number)
            elif not f_words:
                inherited_feed_blocks += 1
        for code in codes:
            if code in ('M3', 'M4'):
                spindle, last_start = 'on', number
            elif code == 'M5':
                spindle, last_stop = 'off', number
            elif code in ('M7', 'M8'):
                coolant, coolant_used = 'on', True
            elif code == 'M9':
                coolant, last_coolant_off = 'off', number
            elif code in ('M0', 'M1'):
                pauses.append(number)
        end = next((c for c in codes if c in ('M2', 'M30')), None)
        if end:
            program_end = {'line': number, 'code': end}
            break  # A stop after the end marker is not executable shutdown evidence.
    stop_after_motion = last_stop is not None and (last_feed_motion is None or last_stop > last_feed_motion)
    explicit_stop = spindle == 'off' and stop_after_motion
    coolant_closed = not coolant_used or coolant == 'off'
    complete_subset = not uncertain_lines
    return {
        'scope': 'ordered standard M codes and limited modal feed diagnostics; not a controller interpreter',
        'spindle_commanded_state_before_end': spindle or 'unknown',
        'last_spindle_start_line': last_start, 'last_explicit_m5_line': last_stop,
        'last_feed_motion_line': last_feed_motion, 'last_executable_line': last_executable,
        'explicit_spindle_stop_after_feed_motion': explicit_stop,
        'coolant_used': coolant_used, 'coolant_commanded_state_before_end': coolant or 'unknown',
        'last_explicit_m9_line': last_coolant_off, 'coolant_off_if_used': coolant_closed,
        'program_end': program_end, 'pause_lines': pauses,
        'unresolved_control_lines': sorted(set(uncertain_lines)),
        'restricted_sequence_check_passed': bool(explicit_stop and coolant_closed and program_end and complete_subset),
        'feed_blocks_using_inherited_f': inherited_feed_blocks,
        'feed_blocks_without_known_positive_f': missing_feed_lines,
        'hardware_stopped_verified': False, 'postprocessor_compatibility_verified': False,
        'notes': ['F is modal in the supported units-per-minute/revolution subset; absence on a later line alone does not mean no feed',
                  'M2/M30 implicit spindle/coolant behavior is controller-specific and is not assumed',
                  'M5/M9 text cannot prove actual spindle rundown or cooldown; unknown controls require manual review'],
    }


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
    for _, line in _code_lines(text):
        for letter, number in re.findall(r"([A-Z])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))", line):
            value = float(number)
            if not math.isfinite(value):
                continue
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
        "sequence_audit": audit_nc_sequence(text),
        "machining_approved": False,
        "limitations": ["Ranges are literal words; restricted ordered-command diagnostics do not reconstruct machine travel",
                        "Does not resolve modal offsets, arcs, expressions, units changes, subroutines or collisions",
                        "No Z1 Pro postprocessor compatibility or cutting safety is certified"],
    }
