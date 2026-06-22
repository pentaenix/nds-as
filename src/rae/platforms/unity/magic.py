from __future__ import annotations

UNITY_MAGICS = (b"UnityFS", b"UnityWeb", b"UnityRaw")

def classify_unity_magic(data: bytes) -> str:
    for magic in UNITY_MAGICS:
        if data.startswith(magic):
            return magic.decode("ascii")
    if data.startswith(b"CAB-"):
        return "CAB"
    return ""

def looks_like_unity_bundle(data: bytes) -> bool:
    return bool(classify_unity_magic(data))
