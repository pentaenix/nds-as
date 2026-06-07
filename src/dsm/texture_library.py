from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable, Iterable

from .scanner import Asset
from .nitro_textures import DecodedImage, Tex0Info, decode_texture, decode_btx_images, palette_options_for_texture, parse_tex0_candidates, prepare_tex0, parse_tex0_manifest

Progress = Callable[[str], None]


@dataclass(slots=True)
class TextureRecord:
    asset_id: str
    asset_path: str
    texture_name: str
    palette_names: tuple[str, ...]
    width: int
    height: int
    format_id: int


@dataclass(slots=True)
class TextureBinding:
    texture_asset_id: str
    texture_asset_path: str
    texture_name: str
    palette_name: str | None
    confidence: str
    reason: str


class TextureLibrary:
    def __init__(self, assets_by_id: dict[str, Asset], manifests: dict[str, Tex0Info]):
        self.assets_by_id = assets_by_id
        self.manifests = manifests
        self.records: list[TextureRecord] = []
        self._by_texture_name: dict[str, list[TextureRecord]] = {}
        for asset_id, manifest in manifests.items():
            asset = assets_by_id[asset_id]
            for tex in manifest.textures:
                palettes = tuple(p.name for p in palette_options_for_texture(tex, manifest.palettes, strict=True) if p is not None)
                record = TextureRecord(
                    asset_id=asset_id,
                    asset_path=asset.virtual_path,
                    texture_name=tex.name,
                    palette_names=palettes,
                    width=tex.width,
                    height=tex.height,
                    format_id=tex.format_id,
                )
                self.records.append(record)
                self._by_texture_name.setdefault(tex.name.casefold(), []).append(record)

    @classmethod
    def from_assets(
        cls,
        assets: Iterable[Asset],
        *,
        progress: Progress | None = None,
        manifest_cache: dict[str, Tex0Info | None] | None = None,
    ) -> "TextureLibrary":
        assets_by_id: dict[str, Asset] = {}
        manifests: dict[str, Tex0Info] = {}
        cache = manifest_cache if manifest_cache is not None else {}
        texture_assets = [a for a in assets if a.magic in {"BTX0", "BMD0"}]
        for index, asset in enumerate(texture_assets, start=1):
            if progress and (index == 1 or index % 100 == 0 or index == len(texture_assets)):
                progress(f"Texture library: reading texture manifests {index}/{len(texture_assets)}...")
            manifest = cache.get(asset.asset_id)
            if manifest is None and asset.asset_id not in cache:
                manifest = parse_tex0_manifest(asset.data)
                cache[asset.asset_id] = manifest
            if manifest and manifest.textures:
                assets_by_id[asset.asset_id] = asset
                manifests[asset.asset_id] = manifest
        return cls(assets_by_id, manifests)

    def manifest_for_asset(self, asset_id: str) -> Tex0Info | None:
        return self.manifests.get(asset_id)

    def find_exact(self, texture_name: str, palette_name: str | None = None) -> list[TextureBinding]:
        rows = self._by_texture_name.get(texture_name.casefold(), [])
        out: list[TextureBinding] = []
        for row in rows:
            chosen_palette = None
            if palette_name:
                if palette_name in row.palette_names:
                    chosen_palette = palette_name
                elif row.palette_names:
                    # Keep the texture match, but let decode try every palette variant.
                    chosen_palette = None
                else:
                    chosen_palette = None
            elif row.palette_names:
                chosen_palette = row.palette_names[0]
            out.append(TextureBinding(
                texture_asset_id=row.asset_id,
                texture_asset_path=row.asset_path,
                texture_name=row.texture_name,
                palette_name=chosen_palette,
                confidence="exact",
                reason=f"exact NSBMD material texture name matched NSBTX texture dictionary name: {texture_name}",
            ))
        return out

    def _decode_texture_images(self, asset_id: str, texture_name: str) -> list[DecodedImage]:
        asset = self.assets_by_id.get(asset_id)
        if asset is None:
            return []
        tex = None
        for candidate in parse_tex0_candidates(asset.data):
            prepared = prepare_tex0(candidate)
            tex = next((t for t in prepared.textures if t.name == texture_name), None)
            if tex is None:
                continue
            paired_count = len(prepared.textures) if len(prepared.textures) == len(prepared.palettes) else None
            texture_index = next((i for i, t in enumerate(prepared.textures) if t.name == texture_name), None)
            if tex.format_id == 7:
                img = decode_texture(tex, None, prepared)
                return [img] if img else []
            images: list[DecodedImage] = []
            palettes = palette_options_for_texture(
                tex,
                prepared.palettes,
                strict=False,
                texture_index=texture_index,
                paired_count=paired_count,
            )
            for palette in palettes:
                try:
                    img = decode_texture(tex, palette, prepared)
                except Exception:
                    img = None
                if img is not None:
                    img.name = f"{img.name}__{palette.name}" if palette is not None else img.name
                    images.append(img)
            if images:
                return images
        return []

    def decode_texture_all_palettes(self, texture_asset_id: str, texture_name: str) -> list[DecodedImage]:
        return self._decode_texture_images(texture_asset_id, texture_name)

    def decode_binding(self, binding: TextureBinding) -> DecodedImage | None:
        images = self._decode_texture_images(binding.texture_asset_id, binding.texture_name)
        if not images:
            return None
        if binding.palette_name:
            for image in images:
                if image.palette_name == binding.palette_name or image.name.startswith(f"{binding.texture_name}__{binding.palette_name}"):
                    image.name = binding.texture_name
                    return image
        first = images[0]
        first.name = binding.texture_name
        return first


def texture_library_fingerprint(assets: Iterable[Asset]) -> tuple[int, str]:
    """Stable session key for the ROM's BTX0/BMD0 texture dictionary set."""
    texture_assets = sorted(
        (a for a in assets if a.magic in {"BTX0", "BMD0"}),
        key=lambda asset: asset.asset_id,
    )
    digest = hashlib.sha1()
    for asset in texture_assets:
        digest.update(asset.asset_id.encode("utf-8"))
        digest.update(b"\0")
    return len(texture_assets), digest.hexdigest()[:16]


class TextureLibraryStore:
    """Session-scoped cache for parsed NSBTX manifests and exact-match lookups."""

    def __init__(self) -> None:
        self.library: TextureLibrary | None = None
        self._fingerprint: tuple[int, str] | None = None
        self._manifest_cache: dict[str, Tex0Info | None] = {}

    def clear(self) -> None:
        self.library = None
        self._fingerprint = None
        self._manifest_cache.clear()

    def fingerprint(self, assets: Iterable[Asset]) -> tuple[int, str]:
        return texture_library_fingerprint(assets)

    def is_ready_for(self, assets: Iterable[Asset]) -> bool:
        return self.library is not None and self._fingerprint == self.fingerprint(assets)

    def get_or_build(self, assets: Iterable[Asset], *, progress: Progress | None = None) -> TextureLibrary:
        fp = self.fingerprint(assets)
        if self.library is not None and self._fingerprint == fp:
            return self.library
        self.library = TextureLibrary.from_assets(
            assets,
            progress=progress,
            manifest_cache=self._manifest_cache,
        )
        self._fingerprint = fp
        return self.library
