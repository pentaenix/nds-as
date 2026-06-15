"""EasyFind build scope and incremental update modes."""
from __future__ import annotations

from dataclasses import dataclass, field

BUILD_MODE_FULL = "full"
BUILD_MODE_TYPES = "types"
BUILD_MODE_CATCHUP = "catchup"


@dataclass(frozen=True)
class EasyFindBuildOptions:
    """Controls what an EasyFind build or update bakes."""

    mode: str = BUILD_MODE_FULL
    bake_node_kinds: frozenset[str] = frozenset()

    def summary_label(self) -> str:
        if self.mode == BUILD_MODE_CATCHUP:
            return "Catch up missing graphics"
        if self.mode == BUILD_MODE_TYPES and self.bake_node_kinds:
            kinds = ", ".join(sorted(self.bake_node_kinds))
            return f"Selected types: {kinds}"
        return "Full rebuild"

    def includes_model_previews(self) -> bool:
        """Whether this build may bake BMD0 model thumbnails."""
        if self.mode in {BUILD_MODE_FULL, BUILD_MODE_CATCHUP}:
            return True
        return self.mode == BUILD_MODE_TYPES and "model" in self.bake_node_kinds
