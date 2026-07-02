"""Browser labels, type filters, and shared UI styling constants."""
from __future__ import annotations

BROWSER_COLUMNS = ["Name", "File", "Type", "Path"]
FLAT_COLUMNS = ["#", "Name", "File", "Type", "Path"]
TYPE_LABELS = {
    "BMD0": "Model",
    "BTX0": "Texture",
    "BCA0": "Skel anim",
    "BTA0": "Tex SRT anim",
    "BTP0": "Tex pattern",
    "BMA0": "Mat anim",
    "BVA0": "Vis anim",
    "BPC0": "Color anim",
    "RGCN": "Tiles",
    "RLCN": "Palette",
    "RCSN": "Tilemap",
    "RECN": "Sprite cells",
    "RNAN": "Sprite anim",
    "NFTR": "Font",
    "PNG": "PNG",
    "SDAT": "Sound archive",
    "SSEQ": "Sequence",
    "SSAR": "SFX archive",
    "SBNK": "Instruments",
    "SWAR": "Wave archive",
    "SWAV": "Sample",
    "STRM": "Stream",
    "HOME": "HOME package",
    "UNITY": "Unity bundle",
    "ABA": "Mobile package",
    "MOBL": "Mobile asset",
}
TYPE_FILTER_ORDER = (
    "BMD0",
    "BTX0",
    "RGCN",
    "RLCN",
    "RCSN",
    "RECN",
    "RNAN",
    "NFTR",
    "PNG",
    "BCA0",
    "BTA0",
    "BTP0",
    "BMA0",
    "BVA0",
    "BPC0",
    "SDAT",
    "SSEQ",
    "SSAR",
    "SBNK",
    "SWAR",
    "SWAV",
    "STRM",
    "HOME",
    "UNITY",
    "ABA",
    "MOBL",
)
TYPE_FILTER_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Models & textures", ("BMD0", "BTX0")),
    ("2D graphics", ("RGCN", "RLCN", "RCSN", "RECN", "RNAN", "NFTR", "PNG")),
    ("Model animation", ("BCA0", "BTA0", "BTP0", "BMA0", "BVA0", "BPC0")),
    ("Audio", ("SDAT", "SSEQ", "SSAR", "SBNK", "SWAR", "SWAV", "STRM")),
    ("Mobile / Unity", ("HOME", "UNITY", "ABA", "MOBL")),
)
DEFAULT_TYPE_FILTER_ON = frozenset({"BMD0", "BTX0", "HOME", "UNITY", "ABA", "MOBL"})
FILTER_CHIP_STYLE = (
    "QPushButton { padding: 4px 10px; border: 1px solid #666; border-radius: 6px; "
    "background: #ececec; color: #111; }"
    "QPushButton:checked { background: #2563eb; border-color: #1d4ed8; color: #fff; font-weight: 600; }"
)
DROPDOWN_BUTTON_STYLE = (
    "QPushButton { padding: 4px 12px; border: 1px solid #585858; border-radius: 4px; "
    "background: #454545; color: #ececec; text-align: left; min-height: 22px; }"
    "QPushButton::menu-indicator { subcontrol-origin: padding; subcontrol-position: center right; "
    "padding-right: 8px; }"
    "QPushButton:hover { background: #525252; }"
)
CHROME_BUTTON_STYLE = (
    "QPushButton, QToolButton {"
    " background: #454545; color: #ececec; border: 1px solid #585858;"
    " border-radius: 4px; padding: 4px 10px; min-height: 22px; min-width: 28px;"
    "}"
    "QPushButton:hover, QToolButton:hover { background: #525252; }"
    "QPushButton:disabled, QToolButton:disabled { background: #383838; color: #888888; }"
)
CHECKER_LIGHT = "#4a4a4a"
CHECKER_DARK = "#353535"
GL_BG_COLORS = {
    "White": (1.0, 1.0, 1.0, 1.0),
    "Checkered": (0.28, 0.28, 0.28, 1.0),
    "Black": (0.08, 0.08, 0.08, 1.0),
}
VIEWPORT_BANNER_STYLE = (
    "background: rgba(30, 30, 30, 220); color: #ececec;"
    "padding: 5px 10px; font-size: 11px; border-bottom: 1px solid #585858;"
)
