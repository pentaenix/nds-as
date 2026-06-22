from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from ..android.source import AndroidSourceFile, AndroidSourceInventory, scan_android_source
from ..unity.bundle_inventory import UnityBundleInventory, inventory_unity_bundle, unitypy_available
from .animation import classify_animation_clip
from .ids import HomePokemonId, parse_home_pokemon_id, pokemon_display_name
from ...platforms.nds.scanner import Asset


UNITY_MODEL_TYPES = {"Mesh", "SkinnedMeshRenderer"}
UNITY_TEXTURE_TYPES = {"Texture2D", "Sprite"}
UNITY_RIG_TYPES = {"Animator", "Avatar", "Transform", "GameObject"}
UNITY_ANIMATION_TYPES = {"AnimationClip", "AnimatorController"}


@dataclass(slots=True)
class HomePokemonPackage:
    id: str
    number: int
    name: str
    form_a: str = "00"
    form_b: str = "00"
    source_files: list[dict] = field(default_factory=list)
    unity_objects: list[dict] = field(default_factory=list)
    object_counts: dict[str, int] = field(default_factory=dict)
    animation_candidates: dict[str, list[dict]] = field(default_factory=lambda: {"idle": [], "physical_attack": [], "special_attack": [], "unmapped": []})
    status: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def summarize_status(self) -> None:
        counts = dict(self.object_counts)
        has_mesh = any(k in counts for k in UNITY_MODEL_TYPES) or any("mesh" in f.get("classification", "") for f in self.source_files)
        has_tex = any(k in counts for k in UNITY_TEXTURE_TYPES) or any("texture" in f.get("virtual_path", "").casefold() or "dependencies" in f.get("virtual_path", "").casefold() for f in self.source_files)
        has_rig = any(k in counts for k in UNITY_RIG_TYPES)
        has_anim = any(k in counts for k in UNITY_ANIMATION_TYPES) or bool(self.animation_candidates.get("idle") or self.animation_candidates.get("physical_attack") or self.animation_candidates.get("special_attack"))
        self.status = {
            "model": "found" if has_mesh else "unknown",
            "textures": "found" if has_tex else "unknown",
            "skeletonRig": "found" if has_rig else "unknown",
            "animations": "found" if has_anim else "unknown",
            "idle": "found" if self.animation_candidates.get("idle") else "missing",
            "physicalAttack": "found" if self.animation_candidates.get("physical_attack") else "missing",
            "specialAttack": "found" if self.animation_candidates.get("special_attack") else "missing",
        }


@dataclass(slots=True)
class HomeLibrary:
    source: str
    package_id: str = ""
    unitypy: bool = False
    packages: list[HomePokemonPackage] = field(default_factory=list)
    ungrouped_files: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "packageId": self.package_id,
            "unityPyAvailable": self.unitypy,
            "packages": [p.to_dict() for p in self.packages],
            "ungroupedFiles": self.ungrouped_files,
            "warnings": list(self.warnings),
        }

    def write_json(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return out


def build_home_library(source: str | Path, progress: Callable[[str], None] | None = None, *, inspect_unity: bool = True) -> HomeLibrary:
    inv = scan_android_source(source, progress=progress)
    lib = HomeLibrary(source=inv.source, package_id=inv.package_id, unitypy=unitypy_available(), warnings=list(inv.warnings))
    packages: dict[str, HomePokemonPackage] = {}
    for entry in inv.files:
        pid = parse_home_pokemon_id(entry.virtual_path)
        if pid is None:
            lib.ungrouped_files.append(entry.to_dict())
            continue
        pkg = packages.setdefault(pid.canonical, _new_package(pid))
        pkg.source_files.append(entry.to_dict())
        if inspect_unity and entry.local_path and entry.magic in {"UnityFS", "UnityWeb", "UnityRaw"}:
            if progress:
                progress(f"Inspecting Unity objects: {entry.virtual_path}")
            uinv = inventory_unity_bundle(entry.local_path)
            _merge_unity_inventory(pkg, uinv)
            if uinv.error:
                pkg.warnings.append(f"{Path(entry.local_path).name}: {uinv.error}")
    for pkg in packages.values():
        pkg.summarize_status()
    lib.packages = sorted(packages.values(), key=lambda p: (p.number, p.form_a, p.form_b, p.id))
    return lib


def _new_package(pid: HomePokemonId) -> HomePokemonPackage:
    return HomePokemonPackage(
        id=pid.canonical,
        number=pid.number,
        name=pokemon_display_name(pid.number),
        form_a=pid.form_a,
        form_b=pid.form_b,
    )


def _merge_unity_inventory(pkg: HomePokemonPackage, inv: UnityBundleInventory) -> None:
    counts = inv.counts()
    for key, value in counts.items():
        pkg.object_counts[key] = pkg.object_counts.get(key, 0) + value
    for obj in inv.objects:
        row = obj.to_dict()
        row["bundle"] = inv.path
        pkg.unity_objects.append(row)
        if obj.type == "AnimationClip":
            bucket, confidence, reason = classify_animation_clip(obj.name)
            pkg.animation_candidates.setdefault(bucket, []).append({
                "name": obj.name,
                "pathId": obj.path_id,
                "bundle": inv.path,
                "confidence": confidence,
                "reason": reason,
            })


def home_packages_as_rae_assets(lib: HomeLibrary) -> list[Asset]:
    assets: list[Asset] = []
    for pkg in lib.packages:
        payload = json.dumps(pkg.to_dict(), indent=2, ensure_ascii=False).encode("utf-8")
        status = pkg.status
        bits = []
        for key in ("model", "textures", "skeletonRig", "animations"):
            bits.append(f"{key}:{status.get(key, 'unknown')}")
        asset = Asset(
            asset_id=f"home_{pkg.id}",
            virtual_path=f"pokemon_home/{pkg.id}_{pkg.name.replace(' ', '_')}.homepkg",
            kind="Pokémon HOME form package",
            magic="HOME",
            extension=".homepkg.json",
            data=payload,
            original_data=payload,
            mapping_category="models",
            mapping_label=f"{pkg.name} {pkg.id} ({'; '.join(bits)})",
            mapping_confidence="home-package-builder",
        )
        assets.append(asset)
    return assets
