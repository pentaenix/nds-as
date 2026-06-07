from __future__ import annotations

import re
from pathlib import Path


def read_u16le(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 2], "little")


def read_u32le(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "little")


def safe_decode(raw: bytes) -> str:
    return raw.decode("shift_jis", errors="replace")


def sanitize_component(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return name or "unnamed"


def sanitize_virtual_path(path: str) -> Path:
    parts = [sanitize_component(p) for p in path.replace("\\", "/").split("/") if p and p not in {".", ".."}]
    return Path(*parts) if parts else Path("asset.bin")


def human_size(num: int) -> str:
    n = float(num)
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{num} B"
