from __future__ import annotations

from pathlib import Path

from ...core.mapping import GameMapping, apply_mapping_to_assets, choose_mapping, load_mappings, mapping_summary
from .ids import parse_home_asset_id, pokemon_display_name


def choose_mapping_for_mobile_source(
    *,
    package_id: str = "",
    title: str = "",
    source_stem: str = "",
) -> GameMapping | None:
    package_id = (package_id or "").strip()
    title_l = (title or "").casefold()
    stem_l = (source_stem or "").casefold()
    maps = load_mappings(platform="mobile")
    best: tuple[int, GameMapping] | None = None
    for mapping in maps:
        score = 0
        if mapping.game_family == "pokemon_home":
            score = 10
        for game in mapping.games:
            for pid in game.get("packageIds", []):
                if package_id and str(pid).strip() == package_id:
                    score = max(score, 100)
            game_title = str(game.get("title", "")).casefold()
            short = str(game.get("shortTitle", "")).casefold()
            if game_title and game_title in title_l:
                score = max(score, 80)
            if short and short in title_l:
                score = max(score, 70)
        if "pokemon_home" in stem_l or "pokemonhome" in stem_l:
            score = max(score, 90)
        if score and (best is None or score > best[0]):
            best = (score, mapping)
    if best is not None:
        return best[1]
    return choose_mapping(title, "")


def apply_home_mapping_to_assets(assets: list, mapping: GameMapping | None) -> None:
    apply_mapping_to_assets(assets, mapping)
    for asset in assets:
        enrich_home_asset_label(asset)


def enrich_home_asset_label(asset) -> None:
    if getattr(asset, "magic", "") == "HOME":
        return
    virtual_path = str(getattr(asset, "virtual_path", "") or "")
    haystack = f"{virtual_path} {getattr(asset, 'mapping_label', '')}"
    parsed = parse_home_asset_id(haystack)
    if parsed is None:
        return
    name = pokemon_display_name(parsed.number)
    form_bits = []
    if parsed.form_a != "00":
        form_bits.append(f"form {parsed.form_a}")
    if parsed.form_b != "00":
        form_bits.append(f"variant {parsed.form_b}")
    form_suffix = f" ({', '.join(form_bits)})" if form_bits else ""
    base = Path(virtual_path.split("!/", 1)[-1]).name if virtual_path else parsed.canonical
    role = _role_from_path(haystack)
    preview_hint = _preview_hint_for_asset(asset, parsed.number)
    hint_suffix = f" [{preview_hint}]" if preview_hint else ""
    asset.mapping_label = f"{name} #{parsed.number:04d}{form_suffix} — {role}: {base}{hint_suffix}"
    if getattr(asset, "mapping_confidence", "") in {"", "format-signature", "mobile-rom-inventory"}:
        asset.mapping_confidence = "home-path-id"


def _preview_hint_for_asset(asset, species_number: int) -> str:
    mobile_root = _mobile_root_from_asset(asset)
    if mobile_root is None:
        return ""
    try:
        from .species_index import preview_status_label, species_preview_status

        return preview_status_label(species_preview_status(mobile_root, species_number))
    except Exception:
        return ""


def _mobile_root_from_asset(asset) -> Path | None:
    payload = getattr(asset, "data", b"")
    if isinstance(payload, bytes):
        try:
            import json

            row = json.loads(payload.decode("utf-8", "replace"))
            mobile = row.get("mobileRom") or {}
            root = mobile.get("root")
            if root:
                return Path(str(root)).expanduser().resolve()
            container = row.get("container")
            if container:
                container_path = Path(str(container)).expanduser()
                if container_path.is_file() and container_path.parent.name == "apk":
                    return container_path.parent.parent.resolve()
        except Exception:
            pass
    mobile = getattr(asset, "mobileRom", None)
    if isinstance(mobile, dict):
        root = mobile.get("root") or mobile.get("source")
        if root:
            return Path(str(root)).expanduser().resolve()
    return None


def _role_from_path(path: str) -> str:
    low = path.casefold()
    if "dependencies" in low:
        return "dependencies"
    if "prefab" in low:
        return "prefab model"
    if "/pokemons/" in low or "pokemons/pm" in low:
        return "model bundle"
    if "mt_pv_ev_" in low:
        return "Mitake preview"
    if "cap" in low and ".aba" in low:
        return "cached species bundle"
    if "bgm_mt_" in low:
        return "background music"
    if low.endswith(".aba") or low.endswith(".abap"):
        return "encrypted package"
    return "HOME asset"


def mobile_profile_summary(mapping: GameMapping | None, *, package_id: str = "", app_name: str = "") -> str:
    if mapping is None:
        return ""
    lines = [mapping_summary(mapping)]
    if package_id:
        lines.append(f"Package: {package_id}")
    if app_name:
        lines.append(f"App: {app_name}")
    lines.append(
        "HOME filenames use pm####_##_##, cap####_f##, and mt_pv_ev_####_##_## ids. "
        "RAE resolves National Dex numbers to species names automatically."
    )
    return "\n".join(lines)
