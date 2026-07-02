from __future__ import annotations

import importlib.util
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(slots=True)
class UnityObjectInfo:
    type: str
    name: str = ""
    path_id: str = ""
    container: str = ""
    size: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class UnityBundleInventory:
    path: str
    readable: bool
    reader: str = ""
    objects: list[UnityObjectInfo] = field(default_factory=list)
    error: str = ""

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for obj in self.objects:
            out[obj.type] = out.get(obj.type, 0) + 1
        return out

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "readable": self.readable,
            "reader": self.reader,
            "objects": [obj.to_dict() for obj in self.objects],
            "counts": self.counts(),
            "error": self.error,
        }


def unitypy_available() -> bool:
    return importlib.util.find_spec("UnityPy") is not None


def inventory_unity_bundle(path: str | Path, *, max_objects: int = 50000) -> UnityBundleInventory:
    p = Path(path)
    if not unitypy_available():
        return UnityBundleInventory(path=str(p), readable=False, error="UnityPy is not installed. Install it in the RAE venv to inspect Unity object types.")
    try:
        import UnityPy  # type: ignore
    except Exception as exc:
        return UnityBundleInventory(path=str(p), readable=False, error=f"UnityPy import failed: {exc}")
    try:
        env = UnityPy.load(str(p))
        objects: list[UnityObjectInfo] = []
        for index, obj in enumerate(env.objects):
            if index >= max_objects:
                break
            type_name = getattr(getattr(obj, "type", None), "name", "") or str(getattr(obj, "type", ""))
            name = ""
            size = 0
            try:
                data = obj.read()
                name = str(getattr(data, "name", "") or "")
                raw = getattr(data, "data", b"")
                if isinstance(raw, (bytes, bytearray)):
                    size = len(raw)
            except Exception:
                pass
            objects.append(UnityObjectInfo(
                type=type_name,
                name=name,
                path_id=str(getattr(obj, "path_id", "")),
                container=str(getattr(obj, "container", "") or ""),
                size=size,
            ))
        return UnityBundleInventory(path=str(p), readable=True, reader="UnityPy", objects=objects)
    except Exception as exc:
        return UnityBundleInventory(path=str(p), readable=False, reader="UnityPy", error=str(exc))
