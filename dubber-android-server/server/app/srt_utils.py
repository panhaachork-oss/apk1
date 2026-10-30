"""SRT parse/format — same segment model as DUBBER AI PRO's table.

Segment = {index, start, end, original, translated, voice, pitch, rate}
Times are seconds (float). SRT timestamps are HH:MM:SS,mmm.
"""
import re

_TS = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def _to_seconds(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(text):
    """Parse SRT text into a list of segment dicts. Bad blocks are skipped."""
    segments = []
    blocks = re.split(r"\n\s*\n", (text or "").strip())
    for block in blocks:
        lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        # First line may be an index number; timestamp line is the next one.
        ts_line = lines[0] if "-->" in lines[0] else (lines[1] if len(lines) > 1 else "")
        m = _TS.search(ts_line)
        if not m:
            continue
        text_lines = lines[2:] if "-->" in lines[0] else lines[2:]
        if not text_lines and len(lines) > 2:
            text_lines = lines[2:]
        start = _to_seconds(*m.groups()[:4])
        end = _to_seconds(*m.groups()[4:])
        if end <= start:
            continue
        segments.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "original": " ".join(text_lines),
            "translated": "",
            "voice": "",
            "pitch": "+0Hz",
            "rate": "+0%",
        })
    for i, seg in enumerate(segments):
        seg["index"] = i
    return segments


def format_ts(seconds):
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:
        s += 1
        ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(segments, field="translated"):
    """Serialise segments back to SRT, falling back to original text."""
    out = []
    for i, seg in enumerate(segments):
        text = (seg.get(field) or seg.get("original") or "").strip()
        if not text:
            continue
        out.append(
            f"{i + 1}\n"
            f"{format_ts(seg['start'])} --> {format_ts(seg['end'])}\n"
            f"{text}\n"
        )
    return "\n".join(out)


def validate_segments(segments):
    """Return (cleaned, errors). Mirrors the desktop app's no-overlap rule."""
    errors = []
    cleaned = []
    prev_end = 0.0
    for i, seg in enumerate(segments):
        try:
            start, end = float(seg["start"]), float(seg["end"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"Segment {i + 1}: missing/invalid times")
            continue
        if end <= start:
            errors.append(f"Segment {i + 1}: end must be after start")
            continue
        if start < prev_end - 0.001:
            errors.append(f"Segment {i + 1}: overlaps the previous segment")
            continue
        prev_end = end
        cleaned.append({
            "index": len(cleaned),
            "start": round(start, 3),
            "end": round(end, 3),
            "original": str(seg.get("original", "")),
            "translated": str(seg.get("translated", "")),
            "voice": str(seg.get("voice", "")),
            "pitch": str(seg.get("pitch", "+0Hz")),
            "rate": str(seg.get("rate", "+0%")),
        })
    return cleaned, errors
