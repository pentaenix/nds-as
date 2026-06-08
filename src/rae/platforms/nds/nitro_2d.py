from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .nitro_textures import DecodedImage, bgr555_to_rgba
from ...core.util import read_u16le, read_u32le, sanitize_component

try:
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover
    Image = None
    ImageDraw = None


NITRO_2D_MAGICS = {
    b"RGCN": ("2D tile graphics", ".ncgr"),
    b"RLCN": ("2D palette", ".nclr"),
    b"RCSN": ("2D screen/tilemap", ".nscr"),
    b"RECN": ("2D sprite cells", ".ncer"),
    b"RNAN": ("2D sprite animation", ".nanr"),
    b"NFTR": ("Font", ".nftr"),
}


@dataclass(slots=True)
class TileGraphics:
    tile_data: bytes
    tile_count: int
    bit_depth: int
    tile_bytes: int


@dataclass(slots=True)
class ScreenMap:
    width_tiles: int
    height_tiles: int
    entries: list[int]


@dataclass(slots=True)
class CellOam:
    x: int
    y: int
    width: int
    height: int
    tile_index: int
    palette_bank: int
    hflip: bool
    vflip: bool


def decode_nitro2d_preview(data: bytes, magic: str) -> list[DecodedImage]:
    if magic == "RLCN":
        img = decode_nclr_swatch(data)
        return [img] if img else []
    if magic == "RGCN":
        img = decode_ncgr_tiles_grayscale(data)
        return [img] if img else []
    if magic == "RCSN":
        img = decode_nscr_map_grayscale(data)
        return [img] if img else []
    if magic == "RECN":
        img = decode_ncer_oam_preview(data)
        return [img] if img else []
    if magic == "RNAN":
        img = decode_nanor_metadata_preview(data)
        return [img] if img else []
    return []


def decode_nitro2d_related_preview(asset, related_assets: list) -> list[DecodedImage]:
    """Render a best-effort combined preview using graph-paired 2D assets.

    Original games normally load NCGR tile graphics, NCLR palettes, and NCER/NSCR
    layout data together from an archive/table context. DSM's graph supplies the
    most likely related files; this function composes what it can.
    """
    if asset.magic == "RGCN":
        palette = _best_related(asset, related_assets, "RLCN")
        if palette:
            img = compose_tilesheet(asset.data, palette.data)
            if img:
                return [img]
    if asset.magic == "RCSN":
        tile = _best_related(asset, related_assets, "RGCN")
        palette = _best_related(asset, related_assets, "RLCN")
        if tile and palette:
            img = compose_screen(asset.data, tile.data, palette.data)
            if img:
                return [img]
    if asset.magic == "RECN":
        tile = _best_related(asset, related_assets, "RGCN")
        palette = _best_related(asset, related_assets, "RLCN")
        if tile and palette:
            img = compose_ncer_cells(asset.data, tile.data, palette.data)
            if img:
                return [img]
    if asset.magic == "RNAN":
        cell = _best_related(asset, related_assets, "RECN")
        tile = _best_related(asset, related_assets, "RGCN")
        palette = _best_related(asset, related_assets, "RLCN")
        if cell and tile and palette:
            img = compose_ncer_cells(cell.data, tile.data, palette.data, title="animation_cell_sheet")
            if img:
                return [img]
    return decode_nitro2d_preview(asset.data, asset.magic)


def iter_sections(data: bytes):
    if len(data) < 0x10:
        return
    header_size = read_u16le(data, 0x0C)
    sections = read_u16le(data, 0x0E)
    pos = header_size if 0x10 <= header_size < len(data) else 0x10
    for _ in range(min(sections or 8, 32)):
        if pos + 8 > len(data):
            break
        magic = data[pos:pos + 4]
        size = read_u32le(data, pos + 4)
        if size < 8 or pos + size > len(data):
            break
        yield magic, pos + 8, data[pos + 8:pos + size]
        pos += size
        if pos & 3:
            pos = (pos + 3) & ~3


def find_section(data: bytes, names: set[bytes]) -> bytes | None:
    for magic, _start, payload in iter_sections(data) or []:
        if magic in names:
            return payload
    return None


def _asset_number(asset) -> int | None:
    import re
    nums = re.findall(r"\d+", getattr(asset, "virtual_path", ""))
    return int(nums[-1]) if nums else None


def _best_related(asset, related_assets: list, magic: str):
    source_num = _asset_number(asset)
    candidates = [item for item in related_assets if getattr(item, "magic", "") == magic]
    if not candidates:
        return None
    def key(item):
        same_folder = getattr(item, "folder_key", "") == getattr(asset, "folder_key", "")
        same_container = bool(getattr(asset, "container_chain", ()) and getattr(item, "container_chain", ()) and getattr(asset, "container_chain", ())[-1:] == getattr(item, "container_chain", ())[-1:])
        same_category = bool(getattr(asset, "mapping_category", "") and getattr(asset, "mapping_category", "") == getattr(item, "mapping_category", ""))
        num = _asset_number(item)
        dist = abs(num - source_num) if num is not None and source_num is not None else 9999
        return (0 if same_folder else 1 if same_container else 2 if same_category else 3, dist, getattr(item, "virtual_path", ""))
    return sorted(candidates, key=key)[0]


def decode_nclr_colors(data: bytes) -> list[tuple[int, int, int, int]]:
    payload = find_section(data, {b"TTLP", b"PLTT"})
    if not payload:
        return []

    # find_section() returns the body after the 8-byte section header. In the
    # public Nitro docs, TTLP offsets include that section header, so the palette
    # bytes start at documented 0x18 -> payload 0x10. Older DSM builds used
    # 0x18 inside the payload and skipped most small palettes.
    candidates: list[bytes] = []
    if len(payload) >= 0x10:
        data_size = read_u32le(payload, 0x8) if len(payload) >= 0x0C else 0
        start = 0x10
        end = start + data_size if data_size and start + data_size <= len(payload) else len(payload)
        if start < len(payload):
            candidates.append(payload[start:end])
    # Recovery fallbacks for uncommon/variant files.
    if len(payload) > 0x18:
        candidates.append(payload[0x18:])
    candidates.append(payload)

    best: list[tuple[int, int, int, int]] = []
    for raw in candidates:
        vals = [bgr555_to_rgba(int.from_bytes(raw[i:i + 2], "little")) for i in range(0, len(raw) - 1, 2)]
        # Prefer normal palette sizes and non-empty decoded data.
        if len(vals) > len(best):
            best = vals
    return best


def decode_nclr_swatch(data: bytes) -> DecodedImage | None:
    colors = decode_nclr_colors(data)
    if not colors:
        return None
    return palette_swatch(colors[:256], name="palette_swatch")


def palette_swatch(colors: list[tuple[int, int, int, int]], *, name: str) -> DecodedImage:
    if Image is None:
        raise RuntimeError("Pillow is required for palette previews. Install requirements.txt.")
    cell = 16
    cols = 16
    rows = max(1, (len(colors) + cols - 1) // cols)
    im = Image.new("RGBA", (cols * cell, rows * cell), (0, 0, 0, 0))
    for i, color in enumerate(colors):
        x = (i % cols) * cell
        y = (i // cols) * cell
        for yy in range(y, y + cell):
            for xx in range(x, x + cell):
                im.putpixel((xx, yy), color)
    return DecodedImage(name, im.width, im.height, im.tobytes(), "NCLR palette swatch")


def parse_ncgr(data: bytes) -> TileGraphics | None:
    payload = find_section(data, {b"RAHC", b"CHAR"})
    if not payload:
        return None

    # RAHC/CHAR body starts after its section header. Documented offsets include
    # the 8-byte section header, so bit depth is documented 0x0C -> payload 0x04,
    # data size is documented 0x18 -> payload 0x10, and tile data begins around
    # documented 0x20/0x24 -> payload 0x18/0x1C.
    bit_depth = read_u32le(payload, 0x04) if len(payload) >= 0x08 else 3
    if bit_depth not in {3, 4}:
        bit_depth = 3
    tile_bytes = 32 if bit_depth == 3 else 64

    candidates: list[bytes] = []
    data_size = read_u32le(payload, 0x10) if len(payload) >= 0x14 else 0
    for off in (0x18, 0x1C, 0x20, 0x24):
        if 0 <= off < len(payload):
            end = off + data_size if data_size and off + data_size <= len(payload) else len(payload)
            raw = payload[off:end]
            if len(raw) >= tile_bytes:
                candidates.append(raw)
    candidates.append(payload)

    def score(raw: bytes) -> tuple[int, int]:
        whole = len(raw) // tile_bytes
        remainder = len(raw) % tile_bytes
        return (whole, -remainder)

    best = max(candidates, key=score) if candidates else b""
    tile_count = len(best) // tile_bytes
    if tile_count <= 0:
        return None
    tile_count = min(tile_count, 4096)
    return TileGraphics(best[:tile_count * tile_bytes], tile_count, bit_depth, tile_bytes)


def decode_ncgr_tiles_grayscale(data: bytes) -> DecodedImage | None:
    gfx = parse_ncgr(data)
    if not gfx:
        return None
    colors = [(i * 17, i * 17, i * 17, 255) for i in range(16)]
    return render_tilesheet(gfx, colors, "tile_sheet_grayscale", source="NCGR grayscale tile sheet")


def compose_tilesheet(ncgr_data: bytes, nclr_data: bytes) -> DecodedImage | None:
    gfx = parse_ncgr(ncgr_data)
    colors = decode_nclr_colors(nclr_data)
    if not gfx or not colors:
        return None
    return render_tilesheet(gfx, colors, "tile_sheet_palette", source="NCGR + NCLR paired tile sheet")


def tile_pixels(gfx: TileGraphics, tile_index: int, palette: list[tuple[int, int, int, int]], palette_bank: int = 0, hflip: bool = False, vflip: bool = False) -> list[tuple[int, int, int, int]]:
    if tile_index < 0 or tile_index >= gfx.tile_count:
        return [(0, 0, 0, 0)] * 64
    tile = gfx.tile_data[tile_index * gfx.tile_bytes:(tile_index + 1) * gfx.tile_bytes]
    out = [(0, 0, 0, 0)] * 64
    if gfx.bit_depth == 4:  # 8bpp
        for y in range(8):
            for x in range(8):
                idx = tile[y * 8 + x] if y * 8 + x < len(tile) else 0
                color = palette[idx % len(palette)] if palette else (idx, idx, idx, 255)
                dx = 7 - x if hflip else x
                dy = 7 - y if vflip else y
                out[dy * 8 + dx] = color
    else:  # 4bpp, palette bank selects one 16-color subpalette.
        bank_off = palette_bank * 16
        for y in range(8):
            for xpair in range(4):
                byte = tile[y * 4 + xpair] if y * 4 + xpair < len(tile) else 0
                for sub, idx in enumerate((byte & 0xF, (byte >> 4) & 0xF)):
                    x = xpair * 2 + sub
                    pal_idx = bank_off + idx
                    color = palette[pal_idx % len(palette)] if palette else (idx * 17, idx * 17, idx * 17, 255)
                    # Color 0 is commonly transparent for sprite cells.
                    if idx == 0:
                        color = (color[0], color[1], color[2], 0)
                    dx = 7 - x if hflip else x
                    dy = 7 - y if vflip else y
                    out[dy * 8 + dx] = color
    return out


def render_tilesheet(gfx: TileGraphics, palette: list[tuple[int, int, int, int]], name: str, *, source: str) -> DecodedImage:
    cols = 16
    rows = (gfx.tile_count + cols - 1) // cols
    w, h = cols * 8, rows * 8
    rgba = bytearray([0, 0, 0, 0] * (w * h))
    for t in range(gfx.tile_count):
        pixels = tile_pixels(gfx, t, palette, 0)
        tx = (t % cols) * 8
        ty = (t // cols) * 8
        for y in range(8):
            for x in range(8):
                off = ((ty + y) * w + (tx + x)) * 4
                rgba[off:off + 4] = bytes(pixels[y * 8 + x])
    return DecodedImage(name, w, h, bytes(rgba), source, 3, None)


def parse_nscr(data: bytes) -> ScreenMap | None:
    payload = find_section(data, {b"NRCS", b"SCRN"})
    if not payload or len(payload) < 8:
        return None
    width_px = read_u16le(payload, 0) or 256
    height_px = read_u16le(payload, 2) or 256
    # NRCS body offsets are section-offset minus 8. Screen data size is
    # documented at 0x10 -> payload 0x08; entries begin at 0x14 -> payload 0x0C.
    data_size = read_u32le(payload, 0x08) if len(payload) >= 0x0C else 0
    start = 0x0C if len(payload) > 0x0C else 8
    raw = payload[start:start + data_size if data_size and start + data_size <= len(payload) else len(payload)]
    entries = [read_u16le(raw, i) for i in range(0, len(raw) - 1, 2)]
    if not entries:
        return None
    wt = max(1, min(128, width_px // 8))
    ht = max(1, min(128, height_px // 8))
    return ScreenMap(wt, ht, entries[:wt * ht])


def decode_nscr_map_grayscale(data: bytes) -> DecodedImage | None:
    screen = parse_nscr(data)
    if not screen:
        return None
    w, h = screen.width_tiles * 8, screen.height_tiles * 8
    rgba = bytearray([0, 0, 0, 255] * (w * h))
    for i, entry in enumerate(screen.entries[:screen.width_tiles * screen.height_tiles]):
        tx = (i % screen.width_tiles) * 8
        ty = (i // screen.width_tiles) * 8
        v = (entry & 0x3FF) % 256
        color = bytes((v, v, v, 255))
        for y in range(8):
            for x in range(8):
                off = ((ty + y) * w + (tx + x)) * 4
                rgba[off:off + 4] = color
    return DecodedImage("screen_tilemap_preview", w, h, bytes(rgba), "NSCR tilemap grayscale")


def compose_screen(nscr_data: bytes, ncgr_data: bytes, nclr_data: bytes) -> DecodedImage | None:
    screen = parse_nscr(nscr_data)
    gfx = parse_ncgr(ncgr_data)
    palette = decode_nclr_colors(nclr_data)
    if not screen or not gfx or not palette:
        return None
    w, h = screen.width_tiles * 8, screen.height_tiles * 8
    rgba = bytearray([0, 0, 0, 0] * (w * h))
    for i, entry in enumerate(screen.entries[:screen.width_tiles * screen.height_tiles]):
        tile_index = entry & 0x3FF
        hflip = bool(entry & 0x400)
        vflip = bool(entry & 0x800)
        pal_bank = (entry >> 12) & 0xF
        pixels = tile_pixels(gfx, tile_index, palette, pal_bank, hflip, vflip)
        tx = (i % screen.width_tiles) * 8
        ty = (i // screen.width_tiles) * 8
        for y in range(8):
            for x in range(8):
                off = ((ty + y) * w + (tx + x)) * 4
                rgba[off:off + 4] = bytes(pixels[y * 8 + x])
    return DecodedImage("screen_tilemap_palette", w, h, bytes(rgba), "NSCR + NCGR + NCLR paired map")


def parse_ncer_cells(data: bytes, *, max_cells: int = 48) -> list[list[CellOam]]:
    payload = find_section(data, {b"KBEC", b"CEBK"})
    if not payload or len(payload) < 0x20:
        return []
    image_count = read_u32le(payload, 0)
    if image_count <= 0 or image_count > 4096:
        image_count = read_u16le(payload, 0)
    if image_count <= 0 or image_count > 4096:
        return []
    table_start = 0x18 if len(payload) > 0x18 else 0x10
    table_bytes = image_count * 8
    if table_start + table_bytes > len(payload):
        table_start = 0x20 if 0x20 + table_bytes <= len(payload) else 0x10
    data_base = table_start + table_bytes
    cells: list[list[CellOam]] = []
    for i in range(min(image_count, max_cells)):
        off = table_start + i * 8
        if off + 8 > len(payload):
            break
        oam_count = read_u16le(payload, off)
        oam_rel = read_u32le(payload, off + 4)
        if oam_count <= 0 or oam_count > 256:
            continue
        start_candidates = [data_base + oam_rel, oam_rel, table_start + oam_rel]
        start = -1
        for cand in start_candidates:
            if 0 <= cand <= len(payload) and cand + oam_count * 6 <= len(payload):
                start = cand
                break
        if start < 0:
            continue
        oams = []
        for j in range(oam_count):
            p = start + j * 6
            attr0 = read_u16le(payload, p)
            attr1 = read_u16le(payload, p + 2)
            attr2 = read_u16le(payload, p + 4)
            parsed = parse_oam(attr0, attr1, attr2)
            if parsed:
                oams.append(parsed)
        if oams:
            cells.append(oams)
    return cells


def parse_oam(attr0: int, attr1: int, attr2: int) -> CellOam | None:
    shape = (attr0 >> 14) & 0x3
    size = (attr1 >> 14) & 0x3
    dims = {
        0: [(8, 8), (16, 16), (32, 32), (64, 64)],
        1: [(16, 8), (32, 8), (32, 16), (64, 32)],
        2: [(8, 16), (8, 32), (16, 32), (32, 64)],
    }.get(shape)
    if not dims:
        return None
    width, height = dims[size]
    y = attr0 & 0xFF
    x = attr1 & 0x1FF
    if y >= 192:
        y -= 256
    if x >= 256:
        x -= 512
    return CellOam(
        x=x,
        y=y,
        width=width,
        height=height,
        tile_index=attr2 & 0x3FF,
        palette_bank=(attr2 >> 12) & 0xF,
        hflip=bool(attr1 & 0x1000),
        vflip=bool(attr1 & 0x2000),
    )


def decode_ncer_oam_preview(data: bytes) -> DecodedImage | None:
    cells = parse_ncer_cells(data, max_cells=32)
    if not cells or Image is None:
        return None
    cell = 96
    cols = 4
    rows = max(1, (len(cells) + cols - 1) // cols)
    im = Image.new("RGBA", (cols * cell, rows * cell), (30, 30, 30, 255))
    draw = ImageDraw.Draw(im) if ImageDraw else None
    for idx, oams in enumerate(cells):
        ox = (idx % cols) * cell + cell // 2
        oy = (idx // cols) * cell + cell // 2
        if draw:
            draw.text((ox - cell // 2 + 3, oy - cell // 2 + 3), f"cell {idx}", fill=(220, 220, 220, 255))
            for oam in oams:
                x0 = ox + oam.x // 2
                y0 = oy + oam.y // 2
                x1 = x0 + max(2, oam.width // 2)
                y1 = y0 + max(2, oam.height // 2)
                draw.rectangle((x0, y0, x1, y1), outline=(120, 220, 255, 255))
    return DecodedImage("ncer_cell_layout", im.width, im.height, im.tobytes(), "NCER cell/OAM layout preview")


def compose_ncer_cells(ncer_data: bytes, ncgr_data: bytes, nclr_data: bytes, *, title: str = "ncer_cell_sheet") -> DecodedImage | None:
    cells = parse_ncer_cells(ncer_data, max_cells=64)
    gfx = parse_ncgr(ncgr_data)
    palette = decode_nclr_colors(nclr_data)
    if not cells or not gfx or not palette or Image is None:
        return None
    cell_w = 128
    cell_h = 128
    cols = 4
    rows = max(1, (len(cells) + cols - 1) // cols)
    im = Image.new("RGBA", (cols * cell_w, rows * cell_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im) if ImageDraw else None
    for idx, oams in enumerate(cells):
        base_x = (idx % cols) * cell_w + cell_w // 2
        base_y = (idx // cols) * cell_h + cell_h // 2
        for oam in oams:
            # DS sprite tile index addresses 8x8 tiles. Wide/tall sprites consume
            # sequential tiles left-to-right, top-to-bottom.
            tiles_x = max(1, oam.width // 8)
            tiles_y = max(1, oam.height // 8)
            for ty in range(tiles_y):
                for tx in range(tiles_x):
                    tile_index = oam.tile_index + ty * tiles_x + tx
                    pixels = tile_pixels(gfx, tile_index, palette, oam.palette_bank, oam.hflip, oam.vflip)
                    px0 = base_x + oam.x + tx * 8
                    py0 = base_y + oam.y + ty * 8
                    for py in range(8):
                        for px in range(8):
                            color = pixels[py * 8 + px]
                            if color[3] == 0:
                                continue
                            xx, yy = px0 + px, py0 + py
                            if 0 <= xx < im.width and 0 <= yy < im.height:
                                im.putpixel((xx, yy), color)
        if draw:
            draw.text(((idx % cols) * cell_w + 3, (idx // cols) * cell_h + 3), f"cell {idx}", fill=(255, 255, 255, 220))
    return DecodedImage(title, im.width, im.height, im.tobytes(), "NCER + NCGR + NCLR paired cell sheet")


def decode_nanor_metadata_preview(data: bytes) -> DecodedImage | None:
    if Image is None:
        return None
    payload = find_section(data, {b"KNBA", b"ABNK"})
    anim_count = read_u16le(payload, 0) if payload and len(payload) >= 2 else 0
    frame_count = read_u16le(payload, 2) if payload and len(payload) >= 4 else 0
    im = Image.new("RGBA", (360, 100), (32, 32, 32, 255))
    draw = ImageDraw.Draw(im) if ImageDraw else None
    if draw:
        draw.text((12, 12), "NANR sprite animation", fill=(240, 240, 240, 255))
        draw.text((12, 38), f"animations: {anim_count}", fill=(220, 220, 220, 255))
        draw.text((12, 62), f"frames: {frame_count}", fill=(220, 220, 220, 255))
    return DecodedImage("nanr_metadata", im.width, im.height, im.tobytes(), "NANR metadata preview")


def save_preview_images(images: Iterable[DecodedImage], out_dir: str | Path, *, prefix: str = "preview") -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    used: dict[str, int] = {}
    for image in images:
        stem = sanitize_component(image.name or prefix)
        index = used.get(stem, 0)
        used[stem] = index + 1
        if index:
            stem = f"{stem}_{index:02d}"
        path = out / f"{stem}.png"
        image.save_png(path)
        written.append(path)
    return written
