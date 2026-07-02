from __future__ import annotations

IDLE_WORDS = ("idle", "wait", "stand", "breath", "loop", "default", "base")
PHYSICAL_WORDS = ("physical", "attack01", "atk01", "punch", "kick", "bite", "slash", "tackle", "hit", "body")
SPECIAL_WORDS = ("special", "attack02", "atk02", "beam", "cast", "shoot", "magic", "spatk", "sp_attack", "sp-attack")

def classify_animation_clip(name: str) -> tuple[str, float, str]:
    low = (name or "").casefold()
    if any(w in low for w in IDLE_WORDS):
        return "idle", 0.9, "name-rule"
    if any(w in low for w in SPECIAL_WORDS):
        return "special_attack", 0.75, "name-rule"
    if any(w in low for w in PHYSICAL_WORDS):
        return "physical_attack", 0.7, "name-rule"
    return "unmapped", 0.0, "unclassified"
