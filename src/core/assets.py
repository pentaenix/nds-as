"""ROM scan result types shared by every platform."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath


@dataclass(slots=True)
class Asset:
  """One browsable file row from any platform scanner (NDS, mobile, GBA, …)."""

  asset_id: str
  virtual_path: str
  kind: str
  magic: str
  extension: str
  data: bytes
  original_data: bytes
  rom_file_id: int | None = None
  rom_offset: int | None = None
  compressed: bool = False
  container_chain: tuple[str, ...] = field(default_factory=tuple)
  carved: bool = False
  carved_offset: int | None = None
  mapping_category: str = "unknown"
  mapping_label: str = ""
  mapping_confidence: str = ""
  parent_asset_id: str | None = None
  texture_slot: str | None = None
  is_texture_slot: bool = False

  @property
  def size(self) -> int:
    return len(self.data)

  @property
  def original_size(self) -> int:
    return len(self.original_data)

  @property
  def folder_key(self) -> str:
    path = PurePosixPath(self.virtual_path)
    if len(path.parts) <= 1:
      return ""
    return str(path.parent)

  @property
  def suggested_filename(self) -> str:
    base = PurePosixPath(self.virtual_path).name or self.asset_id
    lower = base.lower()
    if not lower.endswith(self.extension):
      base = f"{base}{self.extension}"
    return base
