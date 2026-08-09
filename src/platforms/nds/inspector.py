"""Nintendo DS Tile Extractor inspector tab."""
from __future__ import annotations

import json
import shutil
import tempfile
import re
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QApplication,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.assets import Asset
from ...core.install import project_root

_GEN5_MAP_RE = re.compile(r"(?:^|/)a/0/0/8/file_\d+\.bin", re.IGNORECASE)


def _is_gen5_map_path(path: str) -> bool:
    return bool(_GEN5_MAP_RE.search(str(path or "")))


def _default_export_directory(category: str = "") -> Path:
    output = project_root() / "exports"
    if category:
        output /= category
    output.mkdir(parents=True, exist_ok=True)
    return output


def _default_export_file(filename: str, category: str = "") -> str:
    return str(_default_export_directory(category) / filename)


def _building_export_source(selected: object, *, include_door: bool) -> Path:
    if include_door:
        return Path(selected.glb_path)
    return Path(selected.doorless_glb_path or selected.glb_path)


def _map_export_action_ids(
    *,
    has_selection: bool,
    has_door: bool,
    has_multiple_variants: bool,
    has_discovered_doors: bool,
    has_interior: bool = False,
) -> tuple[str, ...]:
    """Return only the exports that make sense for the current map context."""
    actions = ["map"]
    if has_interior:
        actions.extend(("interior", "interior_kit"))
    if has_multiple_variants:
        actions.append("variants")
    if has_selection:
        actions.append("building_doorless")
        if has_door:
            actions.append("building_with_door")
        actions.append("building_tile")
    if has_discovered_doors:
        actions.append("doors")
    return tuple(actions)


class _MapCompositionWorker(QThread):
    progress = Signal(str)

    def __init__(
        self,
        *,
        asset_id: str,
        rom_path: Path,
        virtual_path: str,
        terrain_glb: Path,
        out_dir: Path,
        automatic: bool,
        map_index_override: int | None = None,
        area_index_override: int | None = None,
    ) -> None:
        super().__init__()
        self.asset_id = asset_id
        self.rom_path = rom_path
        self.virtual_path = virtual_path
        self.terrain_glb = terrain_glb
        self.out_dir = out_dir
        self.automatic = automatic
        self.map_index_override = map_index_override
        self.area_index_override = area_index_override
        self.result: object | None = None
        self.error = ""

    def _report(self, message: str) -> None:
        if self.isInterruptionRequested():
            raise InterruptedError("Exact map loading was cancelled")
        self.progress.emit(message)

    def run(self) -> None:
        try:
            from .map_objects import build_gen5_map_composition

            result = build_gen5_map_composition(
                self.rom_path,
                self.virtual_path,
                self.terrain_glb,
                self.out_dir,
                progress=self._report,
                map_index_override=self.map_index_override,
                area_index_override=self.area_index_override,
            )
        except Exception as exc:
            self.error = str(exc)
            return
        self.result = result


class NdsAnimationsWidget(QWidget):
    """NDS-owned playable list for Nitro map material animations."""

    def __init__(self, window: object) -> None:
        super().__init__()
        self._window = window
        self._generation = 0
        self._glb_path: Path | None = None
        self._map_clip_ids: set[str] = set()
        self._default_map_clip = ""
        self._skeletal_clip_id = ""
        self._skeletal_clip_ids: set[str] = set()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        self.summary = QLabel("No exact DS material animation is loaded.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.clips = QListWidget()
        self.clips.setToolTip("Double-click an animation to play it in the map preview.")
        self.clips.itemDoubleClicked.connect(lambda _item: self._play())
        layout.addWidget(self.clips, 1)
        row = QHBoxLayout()
        play = QPushButton("Play")
        play.clicked.connect(self._play)
        stop = QPushButton("Stop")
        stop.clicked.connect(self._stop)
        row.addWidget(play)
        row.addWidget(stop)
        row.addStretch(1)
        layout.addLayout(row)

    def clear(self) -> None:
        self._generation += 1
        self._glb_path = None
        self._map_clip_ids.clear()
        self._default_map_clip = ""
        self._skeletal_clip_id = ""
        self._skeletal_clip_ids.clear()
        self.clips.clear()
        self.summary.setText("No exact DS material animation is loaded.")

    def set_glb(self, path: Path, *, autoplay: bool = True) -> int:
        from .gltf.glb_io import read_glb

        self._generation += 1
        self._glb_path = Path(path)
        generation = self._generation
        self.clips.clear()
        self._map_clip_ids.clear()
        self._default_map_clip = ""
        self._skeletal_clip_id = ""
        self._skeletal_clip_ids.clear()
        try:
            glb = read_glb(path)
            motion = (((glb.json.get("extras") or {}).get("rae") or {}).get("mapMaterialMotion") or {})
        except Exception:
            glb = None
            motion = {}
        default_clip = str(motion.get("defaultClip") or "")
        self._default_map_clip = default_clip
        clips = [clip for clip in (motion.get("clips") or []) if clip.get("tracks")]
        for row, clip in enumerate(clips):
            clip_id = str(clip.get("id") or clip.get("name") or f"animation_{row}")
            self._map_clip_ids.add(clip_id)
            tracks = list(clip.get("tracks") or [])
            uv_count = sum(bool(track.get("frameOffsets")) for track in tracks)
            pattern_count = sum(bool(track.get("imageKeyframes")) for track in tracks)
            kinds = []
            if uv_count:
                kinds.append(f"UV motion on {uv_count}")
            if pattern_count:
                kinds.append(f"texture frames on {pattern_count}")
            label = f"{clip.get('name') or clip_id}  —  {', '.join(kinds) or f'{len(tracks)} track(s)'}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, clip_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, "material")
            item.setToolTip("\n".join(str(track.get("material") or "") for track in tracks))
            self.clips.addItem(item)
            if clip_id == default_clip:
                self.clips.setCurrentRow(row)
        skeletal = []
        if glb is not None:
            for animation in glb.json.get("animations") or []:
                if not isinstance(animation, dict):
                    continue
                channels = [channel for channel in (animation.get("channels") or []) if isinstance(channel, dict)]
                if not any(isinstance((channel.get("target") or {}).get("node"), int) for channel in channels):
                    continue
                skeletal.append(animation)
        if skeletal:
            preferred = next(
                (
                    animation
                    for animation in skeletal
                    if str((((animation.get("extras") or {}).get("rae") or {}).get("source") or ""))
                    == "mapObjectAnimations"
                ),
                skeletal[0],
            )
            self._skeletal_clip_id = str(preferred.get("name") or "Exact map object animations")
            for animation in skeletal:
                clip_id = str(animation.get("name") or f"Model animation {len(self._skeletal_clip_ids) + 1}")
                if clip_id in self._skeletal_clip_ids:
                    continue
                self._skeletal_clip_ids.add(clip_id)
                item = QListWidgetItem(f"{clip_id}  —  model motion")
                item.setData(Qt.ItemDataRole.UserRole, clip_id)
                item.setData(Qt.ItemDataRole.UserRole + 1, "skeletal")
                item.setToolTip(
                    "Plays this model state together with the default material animation."
                )
                self.clips.addItem(item)
        if self.clips.count() and self.clips.currentRow() < 0:
            self.clips.setCurrentRow(0)
        total = len(clips) + len(self._skeletal_clip_ids)
        self.summary.setText(
            f"{len(clips)} material animation(s) and {len(self._skeletal_clip_ids)} model animation state(s). "
            "Model and terrain motion play together."
            if total else
            "This exact map has no animation that targets one of its materials or models."
        )
        if autoplay and total:
            QTimer.singleShot(900, lambda: self._play() if generation == self._generation else None)
        return total

    def _selected_id(self) -> str:
        item = self.clips.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item is not None else ""

    def _play(self) -> None:
        clip_id = self._selected_id()
        if not clip_id:
            return
        map_widget = getattr(self._window, "nds_map_objects", None)
        preview = getattr(self._window, "preview", None)
        current_path = getattr(preview, "_last_path", None)
        already_showing = False
        try:
            already_showing = bool(
                self._glb_path
                and current_path
                and Path(current_path).resolve() == self._glb_path.resolve()
            )
        except (OSError, TypeError, ValueError):
            pass
        restored = bool(
            not already_showing
            and map_widget is not None
            and hasattr(map_widget, "ensure_exact_preview")
            and map_widget.ensure_exact_preview(force=True)
        )
        generation = self._generation
        delays = (300, 850, 1500) if restored else (0, 450)
        for attempt, delay in enumerate(delays):
            QTimer.singleShot(
                delay,
                lambda announce=attempt == 0: self._play_current(generation, announce=announce),
            )

    def _play_current(self, generation: int, *, announce: bool) -> None:
        if generation != self._generation:
            return
        clip_id = self._selected_id()
        selected_item = self.clips.currentItem()
        selected_kind = (
            str(selected_item.data(Qt.ItemDataRole.UserRole + 1) or "")
            if selected_item is not None
            else ""
        )
        preview = getattr(self._window, "preview", None)
        if clip_id and preview is not None and hasattr(preview, "play_glb_animation"):
            selected_map_clip = clip_id if clip_id in self._map_clip_ids else self._default_map_clip
            skeletal_clip = clip_id if selected_kind == "skeletal" else self._skeletal_clip_id
            web_view = getattr(preview, "_web_view", None)
            run_js = getattr(web_view, "_run_when_api_ready", None)
            if skeletal_clip and selected_map_clip and callable(run_js):
                # playAnimation starts the skeletal mixer; the material-only API
                # then layers AreaData motion without stopping that mixer.
                run_js(
                    f"window.raeGlbPreview.playAnimation({json.dumps(skeletal_clip)});"
                    f"window.raeGlbPreview.playMapMaterialMotion({json.dumps(selected_map_clip)});"
                )
            else:
                preview.play_glb_animation(clip_id)
            update = getattr(self._window, "_update_status", None)
            if announce and callable(update):
                update(f"Playing exact DS map animation: {clip_id}")

    def _stop(self) -> None:
        preview = getattr(self._window, "preview", None)
        if preview is not None and hasattr(preview, "stop_glb_animation"):
            preview.stop_glb_animation()


class NdsTileExtractorWidget(QWidget):
    def __init__(self, window: object) -> None:
        super().__init__()
        self._window = window
        self._asset: Asset | None = None
        self._asset_id = ""
        self._source_glb: Path | None = None
        self._source_preview_state: dict[str, object] = {}
        self._source_mesh_labels: list[str] = []
        self._source_mesh_texture_paths: list[Path | None] = []
        self._animated_materials: dict[str, str] = {}
        self._material_components: dict[str, tuple[object, ...]] = {}
        self._row_components: dict[str, tuple[object, ...]] = {}
        self._row_anchor_materials: dict[str, str] = {}
        self._automatic_spatial_rows: set[str] = set()
        self._logical_layer_rows: set[str] = set()
        self._components_source: Path | None = None
        self._component_indices: dict[str, int] = {}
        self._repeat_patch_materials: set[str] = set()
        self._spatial_tile_enabled = False
        self._tile_preview_workspace: tempfile.TemporaryDirectory[str] | None = None
        self._side_preview_workspace: tempfile.TemporaryDirectory[str] | None = None
        self._live_tile_preview = None
        self._live_preview_attempted = False
        self._preview_pending = False
        self._preview_source = QPixmap()
        self._preview_generation = 0
        self._preview_yaw = 35.0
        self._preview_pitch = 28.0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self.summary = QLabel(
            "Select the material parts that belong to one reusable tile. The export uses the textures currently shown in the preview."
        )
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.parts = QTreeWidget()
        self.parts.setHeaderLabels(["Use", "Material / part", "Texture", "Motion", "Tiles"])
        self.parts.setRootIsDecorated(False)
        self.parts.setAlternatingRowColors(True)

        browser = QSplitter(Qt.Orientation.Horizontal)
        browser.setChildrenCollapsible(False)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.parts, 1)
        select_row = QHBoxLayout()
        select_all = QPushButton("All")
        select_all.setFixedWidth(48)
        select_all.setToolTip("Select every material part for export.")
        select_all.clicked.connect(lambda: self._set_all_checked(True))
        clear = QPushButton("None")
        clear.setFixedWidth(48)
        clear.setToolTip("Clear the export selection.")
        clear.clicked.connect(lambda: self._set_all_checked(False))
        select_row.addWidget(select_all)
        select_row.addWidget(clear)
        select_row.addStretch(1)
        left_layout.addLayout(select_row)
        browser.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_title = QLabel("Tile preview")
        right_layout.addWidget(self.preview_title)
        self.selection_preview = QLabel("Click any tile part to preview it here.")
        self.selection_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.selection_preview.setWordWrap(True)
        self.selection_preview.setMinimumSize(220, 160)
        self.selection_preview.setFrameShape(QFrame.Shape.StyledPanel)
        self.selection_preview.setStyleSheet("color: #b8b8b8; background: #303030; padding: 6px;")
        self.selection_preview.setToolTip(
            "This renders the isolated geometry and texture bindings that will be written into the .tile file."
        )
        self._preview_host_layout = QVBoxLayout()
        self._preview_host_layout.setContentsMargins(0, 0, 0, 0)
        self._preview_host_layout.addWidget(self.selection_preview, 1)
        right_layout.addLayout(self._preview_host_layout, 1)
        self.camera_controls = QWidget()
        camera_row = QHBoxLayout(self.camera_controls)
        camera_row.setContentsMargins(0, 0, 0, 0)
        self.perspective_button = QPushButton("3D view")
        self.perspective_button.setCheckable(True)
        self.perspective_button.setChecked(True)
        self.perspective_button.clicked.connect(lambda: self._set_preview_camera(top=False))
        self.top_button = QPushButton("Top view (flat tiles)")
        self.top_button.setCheckable(True)
        self.top_button.clicked.connect(lambda: self._set_preview_camera(top=True))
        camera_row.addWidget(self.perspective_button)
        camera_row.addWidget(self.top_button)
        camera_row.addStretch(1)
        right_layout.addWidget(self.camera_controls)
        piece_row = QHBoxLayout()
        self.previous_piece_button = QPushButton("‹")
        self.previous_piece_button.setFixedWidth(30)
        self.previous_piece_button.setToolTip("Show the previous disconnected tile using this material.")
        self.previous_piece_button.clicked.connect(lambda: self._change_component(-1))
        self.component_label = QLabel("Tile 1 / 1")
        self.next_piece_button = QPushButton("›")
        self.next_piece_button.setFixedWidth(30)
        self.next_piece_button.setToolTip("Show the next disconnected tile using this material.")
        self.next_piece_button.clicked.connect(lambda: self._change_component(1))
        self.repeat_patch = QCheckBox("Repeat unit")
        self.repeat_patch.setToolTip(
            "For a repeated flat surface, export one texture-repeat-sized tile instead of the whole plane."
        )
        self.repeat_patch.toggled.connect(self._repeat_patch_toggled)
        self.whole_object = QCheckBox("Assemble layers")
        self.whole_object.setToolTip(
            "Combine co-located geometry layers inside the same inferred DS tile footprint."
        )
        self.whole_object.toggled.connect(self._whole_object_toggled)
        piece_row.addWidget(self.previous_piece_button)
        piece_row.addWidget(self.component_label)
        piece_row.addWidget(self.next_piece_button)
        piece_row.addStretch(1)
        piece_row.addWidget(self.whole_object)
        piece_row.addWidget(self.repeat_patch)
        right_layout.addLayout(piece_row)
        browser.addWidget(right)
        browser.setStretchFactor(0, 3)
        browser.setStretchFactor(1, 2)
        layout.addWidget(browser, 1)

        row = QHBoxLayout()
        self.export_all_button = QPushButton("Export all tiles…")
        self.export_all_button.setToolTip(
            "Export every arrow-selectable occurrence in the focused row as its own layered, animated .tile file."
        )
        self.export_all_button.clicked.connect(self._export_all_occurrences)
        self.export_button = QPushButton("Export .tile…")
        self.export_button.clicked.connect(self._export)
        self.preview_3d_button = QPushButton("Open in main")
        self.preview_3d_button.setToolTip("Open only the focused tile instance in the main 3D viewport.")
        self.preview_3d_button.clicked.connect(self._preview_selected_3d)
        self.restore_button = QPushButton("Source")
        self.restore_button.setToolTip("Restore the full source map in the main viewport.")
        self.restore_button.clicked.connect(self._restore_source_model)
        row.addWidget(self.preview_3d_button)
        row.addWidget(self.restore_button)
        row.addStretch(1)
        row.addWidget(self.export_all_button)
        row.addWidget(self.export_button)
        layout.addLayout(row)
        self.parts.itemChanged.connect(self._selection_changed)
        self.parts.itemSelectionChanged.connect(self._focused_selection_changed)
        self.parts.itemDoubleClicked.connect(self._toggle_item_for_export)
        self.set_context(None)

    def set_context(self, asset: Asset | None, *, force_source: bool = False) -> None:
        self._asset = asset
        same_asset = bool(asset and asset.asset_id == self._asset_id)
        self._asset_id = asset.asset_id if asset else ""
        self.parts.clear()
        self._animated_materials = {}
        self._material_components = {}
        self._row_components = {}
        self._row_anchor_materials = {}
        self._automatic_spatial_rows = set()
        self._logical_layer_rows = set()
        self._components_source = None
        self._component_indices = {}
        self._repeat_patch_materials = set()
        self._spatial_tile_enabled = False
        self.whole_object.blockSignals(True)
        self.whole_object.setChecked(False)
        self.whole_object.blockSignals(False)
        self._preview_generation += 1
        self._preview_pending = False
        self._preview_source = QPixmap()
        self.preview_title.setText("Tile preview")
        if self._live_tile_preview is not None:
            self._live_tile_preview.clear_scene()
            self._live_tile_preview.hide()
        self.selection_preview.show()
        self.selection_preview.setText("Click any tile part to preview it here.")
        preview = getattr(self._window, "preview", None)
        source_glb = getattr(preview, "_last_path", None)
        if force_source or not same_asset or self._source_glb is None:
            self._source_glb = Path(source_glb) if source_glb and Path(source_glb).is_file() else None
            self._source_preview_state = {
                "fallback_textures": list(getattr(preview, "_fallback_texture_paths", [])),
                "texture_by_name": dict(getattr(preview, "_texture_by_name", {})),
                "material_to_texture": dict(getattr(preview, "_material_to_texture", {})),
                "texture_bind_order": list(getattr(preview, "_texture_bind_order", [])),
            }
            self._source_mesh_labels = list(getattr(self._window, "_preview_mesh_labels", []))
            self._source_mesh_texture_paths = list(getattr(preview, "_mesh_texture_paths", []))
        ready = bool(asset and asset.magic == "BMD0" and self._source_glb and self._source_glb.is_file())
        self.export_button.setEnabled(False)
        self.export_all_button.setEnabled(False)
        self.preview_3d_button.setEnabled(False)
        self.restore_button.setEnabled(bool(self._source_glb))
        if not ready:
            self.summary.setText("Preview an NDS BMD0 model first, then select material parts to extract.")
            self._sync_component_controls()
            return

        labels = list(self._source_mesh_labels)
        paths = list(self._source_mesh_texture_paths)
        texture_path_by_label = {
            str(label).casefold(): paths[index]
            for index, label in enumerate(labels)
            if index < len(paths)
        }
        embedded_texture_labels: dict[str, str] = {}
        specs_getter = getattr(self._window, "_material_sequence_spec", None)
        specs = specs_getter(asset.asset_id) if callable(specs_getter) else {}
        self._animated_materials = {}
        try:
            from .gltf.glb_io import read_glb

            gltf = read_glb(self._source_glb).json
            materials = [
                material for material in (gltf.get("materials") or [])
                if isinstance(material, dict) and material.get("name")
            ]
            # Material names are authoritative extraction choices. Preview mesh
            # labels can be incomplete for flat or repeated map primitives.
            if materials:
                labels = [str(material["name"]) for material in materials]
                paths = [texture_path_by_label.get(label.casefold()) for label in labels]
            for material in materials:
                key = str(material["name"]).casefold()
                try:
                    texture_index = material["pbrMetallicRoughness"]["baseColorTexture"]["index"]
                    texture = gltf["textures"][texture_index]
                    image = gltf["images"][texture["source"]]
                    name = str(image.get("name") or image.get("uri") or "")
                    embedded_texture_labels[key] = Path(name).stem if name else "embedded texture"
                except (IndexError, KeyError, TypeError):
                    pass
            motion = (((gltf.get("extras") or {}).get("rae") or {}).get("mapMaterialMotion") or {})
            details: dict[str, dict[str, object]] = {}
            for clip in motion.get("clips") or []:
                for track in clip.get("tracks") or []:
                    material = str(track.get("material") or "")
                    if not material:
                        continue
                    body = details.setdefault(material.casefold(), {"kinds": set(), "frames": 0})
                    if track.get("frameOffsets"):
                        body["kinds"].add("UV")
                        body["frames"] = max(int(body["frames"]), len(track["frameOffsets"]))
                    if track.get("imageKeyframes"):
                        body["kinds"].add("frames")
                        body["frames"] = max(int(body["frames"]), int(track.get("frameCount") or 0))
            for key, body in details.items():
                kinds = " + ".join(sorted(body["kinds"]))
                frames = int(body["frames"])
                self._animated_materials[key] = f"▶ {kinds}{f' · {frames}f' if frames else ''}"
        except Exception:
            self._animated_materials = {}
        from .gltf.extract import (
            group_logical_tile_materials,
            is_shadow_material,
            logical_tile_family,
        )

        material_paths = {
            str(label or f"part_{index}").casefold(): paths[index]
            for index, label in enumerate(labels)
            if index < len(paths)
        }
        groups = group_logical_tile_materials(
            tuple(str(label or f"part_{index}") for index, label in enumerate(labels))
        )
        for group in groups:
            family_key = logical_tile_family(group[0]) if group else None
            material_keys = [material.casefold() for material in group]
            texture_labels: list[str] = []
            motion_labels: list[str] = []
            for material, key in zip(group, material_keys):
                texture_path = material_paths.get(key)
                texture_label = (
                    Path(texture_path).stem
                    if texture_path
                    else embedded_texture_labels.get(key, "unassigned")
                )
                if texture_label not in texture_labels:
                    texture_labels.append(texture_label)
                spec = next((body for name, body in specs.items() if name.casefold() == key), {})
                frame_count = len(spec.get("frames") or [])
                motion = self._animated_materials.get(key)
                motion_label = motion or (f"▶ {frame_count} frames" if frame_count > 1 else "static")
                if motion_label not in motion_labels:
                    motion_labels.append(motion_label)
            if family_key:
                kind, family = family_key.split(":", 1)
                label = {
                    "tree": "Tree",
                    "grass": "Grass",
                    "puddle": "Puddle",
                    "shore": "Beach shore" if family == "sea" else "Shore",
                }.get(kind, "Feature")
                display_name = f"{label} {family} — " + " + ".join(group)
                row_key = family_key
            else:
                display_name = (
                    f"Shadow / decal — {group[0]}"
                    if is_shadow_material(group[0])
                    else group[0]
                )
                row_key = f"material:{group[0].casefold()}"
            item = QTreeWidgetItem([
                "",
                display_name,
                ", ".join(texture_labels),
                " + ".join(motion_labels),
                "…",
            ])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            item.setData(0, Qt.ItemDataRole.UserRole, tuple(group))
            item.setData(0, Qt.ItemDataRole.UserRole + 1, row_key)
            self.parts.addTopLevelItem(item)
        self.parts.resizeColumnToContents(0)
        self.parts.resizeColumnToContents(1)
        self.parts.resizeColumnToContents(4)
        self.summary.setText(
            f"{len(groups)} logical tile part(s). Props exclude terrain floors; trees retain their own shadow, and water/shore rows assemble only their matching animated layers."
        )
        if self.parts.topLevelItemCount():
            self.parts.setCurrentItem(self.parts.topLevelItem(0))
            if self.isVisible():
                # showEvent only runs the first time the inspector opens. A
                # newly selected map must rebuild occurrences immediately when
                # the Tile Extractor tab is already visible.
                self._load_component_catalog()
        else:
            self._sync_component_controls()

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for index in range(self.parts.topLevelItemCount()):
            self.parts.topLevelItem(index).setCheckState(0, state)

    @staticmethod
    def _item_materials(item: QTreeWidgetItem | None) -> list[str]:
        if item is None:
            return []
        value = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(value, (list, tuple)):
            return [str(material) for material in value if str(material)]
        return [str(value)] if value else []

    @staticmethod
    def _item_row_key(item: QTreeWidgetItem | None) -> str:
        if item is None:
            return ""
        value = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if value:
            return str(value)
        materials = NdsTileExtractorWidget._item_materials(item)
        return f"material:{materials[0].casefold()}" if materials else ""

    def _selected_materials(self) -> list[str]:
        selected: list[str] = []
        seen: set[str] = set()
        for index in range(self.parts.topLevelItemCount()):
            item = self.parts.topLevelItem(index)
            if item.checkState(0) != Qt.CheckState.Checked:
                continue
            for material in self._item_materials(item):
                key = material.casefold()
                if key not in seen:
                    selected.append(material)
                    seen.add(key)
        return selected

    def _focused_materials(self) -> list[str]:
        return self._item_materials(self.parts.currentItem())

    def _focused_row_key(self) -> str:
        return self._item_row_key(self.parts.currentItem())

    def _focused_row_components(self) -> tuple[object, ...]:
        return self._row_components.get(self._focused_row_key(), ())

    def _focused_automatic_occurrence(self) -> object | None:
        row_key = self._focused_row_key()
        occurrences = self._focused_row_components()
        if row_key not in self._automatic_spatial_rows or not occurrences:
            return None
        index = min(self._component_indices.get(row_key, 0), len(occurrences) - 1)
        return occurrences[index]

    def _assembled_materials(self, materials: list[str]) -> list[str]:
        """Add every co-located layer belonging to the focused DS tile."""
        anchor = self._focused_automatic_occurrence()
        if anchor is None or not self._material_components:
            return list(materials)
        bounds = (
            float(anchor.bounds_min[0]),
            float(anchor.bounds_min[2]),
            float(anchor.bounds_max[0]),
            float(anchor.bounds_max[2]),
        )
        from .gltf.extract import spatial_assembly_materials, tile_feature_kind

        focused = self._focused_materials()

        return list(
            spatial_assembly_materials(
                self._material_components,
                tuple(materials),
                bounds,
                (float(anchor.bounds_min[1]), float(anchor.bounds_max[1])),
                feature_kind=tile_feature_kind(tuple(focused or materials)),
            )
        )

    def _assembled_materials_for_occurrence(
        self,
        materials: list[str],
        occurrence: object,
    ) -> list[str]:
        """Resolve semantic companion layers for an arbitrary row occurrence."""
        if not self._material_components:
            return list(materials)
        bounds = (
            float(occurrence.bounds_min[0]),
            float(occurrence.bounds_min[2]),
            float(occurrence.bounds_max[0]),
            float(occurrence.bounds_max[2]),
        )
        from .gltf.extract import spatial_assembly_materials, tile_feature_kind

        focused = self._focused_materials()
        return list(
            spatial_assembly_materials(
                self._material_components,
                tuple(materials),
                bounds,
                (float(occurrence.bounds_min[1]), float(occurrence.bounds_max[1])),
                feature_kind=tile_feature_kind(tuple(focused or materials)),
            )
        )

    def _preview_materials(self) -> list[str]:
        """The focused row drives the side viewport; checks only drive export."""
        if self._spatial_tile_enabled:
            materials = self._selected_materials() or self._focused_materials()
        else:
            materials = self._focused_materials() or self._selected_materials()
        return self._assembled_materials(materials)

    def _component_options(self, material: str) -> tuple[object, ...]:
        return self._material_components.get(str(material).casefold(), ())

    def _load_component_catalog(self) -> None:
        source = self._source_glb
        if source is None or not source.is_file() or self._components_source == source:
            return
        try:
            from .gltf.extract import list_material_components

            self._material_components = {
                name.casefold(): tuple(components)
                for name, components in list_material_components(source).items()
            }
        except Exception:
            self._material_components = {}
        self._components_source = source
        self._row_components = {}
        self._row_anchor_materials = {}
        self._automatic_spatial_rows = set()
        self._logical_layer_rows = set()
        self._repeat_patch_materials = {
            key
            for key, components in self._material_components.items()
            if components and bool(getattr(components[0], "repeat_patch_recommended", False))
        }
        from .gltf.extract import (
            choose_logical_tile_anchor,
            cluster_spatial_components,
            is_composite_object_material,
            logical_tile_family,
            shoreline_tile_occurrences,
            spatial_tile_occurrences,
        )

        for index in range(self.parts.topLevelItemCount()):
            item = self.parts.topLevelItem(index)
            materials = self._item_materials(item)
            row_key = self._item_row_key(item)
            family = logical_tile_family(materials[0]) if materials else None
            family_group = bool(
                family
                and all(logical_tile_family(material) == family for material in materials)
            )
            available = [
                (material, self._component_options(material))
                for material in materials
                if self._component_options(material)
            ]
            if family_group and available:
                anchor_material = choose_logical_tile_anchor(
                    self._material_components,
                    tuple(materials),
                )
                raw_components = self._component_options(anchor_material)
                family_kind = str(family).split(":", 1)[0]
                if family_kind == "shore":
                    occurrences = shoreline_tile_occurrences(raw_components)
                elif family_kind == "puddle":
                    # Puddle corner, edge, and center geometry is already
                    # authored at its intended 16/48-unit footprint. Its
                    # deeper reflection plane is added by semantic assembly.
                    occurrences = raw_components
                else:
                    occurrences = spatial_tile_occurrences(raw_components)
                self._logical_layer_rows.add(row_key)
                self._automatic_spatial_rows.add(row_key)
            elif available and is_composite_object_material(available[0][0]):
                anchor_material, raw_components = available[0]
                occurrences = cluster_spatial_components(raw_components)
                self._logical_layer_rows.add(row_key)
                self._automatic_spatial_rows.add(row_key)
                if not item.text(1).startswith("Object"):
                    item.setText(1, f"Object — {item.text(1)}")
            elif available:
                anchor_material, raw_components = available[0]
                occurrences = spatial_tile_occurrences(raw_components)
                if len(occurrences) != len(raw_components) or any(
                    occurrence.bounds_min != raw.bounds_min
                    or occurrence.bounds_max != raw.bounds_max
                    for occurrence, raw in zip(occurrences, raw_components)
                ):
                    self._automatic_spatial_rows.add(row_key)
            else:
                anchor_material, occurrences = (materials[0] if materials else ""), ()
            self._row_components[row_key] = tuple(occurrences)
            self._row_anchor_materials[row_key] = anchor_material
            item.setText(4, str(len(occurrences) or 1))
        self._component_indices = {key: 0 for key in self._row_components}
        self.parts.resizeColumnToContents(4)
        self._sync_component_controls()

    def _extraction_options(
        self,
        materials: list[str],
    ) -> tuple[
        dict[str, int],
        set[str],
        tuple[float, float, float, float] | None,
        bool,
        bool,
        float | None,
    ]:
        focused = self._focused_materials()
        row_key = self._focused_row_key()
        occurrences = self._focused_row_components()
        index = min(
            self._component_indices.get(row_key, 0),
            max(0, len(occurrences) - 1),
        )
        automatic_spatial = row_key in self._automatic_spatial_rows and bool(occurrences)
        component_indices: dict[str, int] = {}
        if not automatic_spatial and not self._spatial_tile_enabled:
            for material in materials:
                if not self._component_options(material):
                    continue
                component_indices[material] = (
                    index if material in focused and row_key else 0
                )
        repeat_patches = {
            material
            for material in materials
            if material.casefold() in self._repeat_patch_materials
        }
        spatial_bounds = None
        preserve_spatial_components = False
        spatial_component_center_filter = False
        if automatic_spatial:
            anchor = occurrences[index]
            spatial_bounds = (
                float(anchor.bounds_min[0]),
                float(anchor.bounds_min[2]),
                float(anchor.bounds_max[0]),
                float(anchor.bounds_max[2]),
            )
            # Logical DS objects are often batched planes, not disconnected
            # meshes. Crop every layer to the inferred occurrence footprint;
            # preserving a whole connected component can retain two trees or
            # four grass clumps from the same source batch.
            repeat_patches.clear()
        elif self._spatial_tile_enabled:
            components = self._component_options(focused[0]) if focused else ()
            if components:
                index = min(index, len(components) - 1)
                from .gltf.extract import (
                    is_waterfall_body_material,
                    is_waterfall_material,
                    suggest_spatial_feature_bounds,
                    suggest_spatial_tile_bounds,
                )

                anchor = components[index]
                if is_waterfall_material(focused[0]):
                    focus_center = (
                        (anchor.bounds_min[0] + anchor.bounds_max[0]) / 2.0,
                        (anchor.bounds_min[2] + anchor.bounds_max[2]) / 2.0,
                    )
                    body_candidates = [
                        component
                        for material in materials
                        if is_waterfall_body_material(material)
                        for component in self._component_options(material)
                        if component.extents[1] > 16.0
                    ]
                    if body_candidates:
                        anchor = min(
                            body_candidates,
                            key=lambda component: (
                                (
                                    (component.bounds_min[0] + component.bounds_max[0]) / 2.0
                                    - focus_center[0]
                                ) ** 2
                                + (
                                    (component.bounds_min[2] + component.bounds_max[2]) / 2.0
                                    - focus_center[1]
                                ) ** 2,
                                -component.extents[1],
                            ),
                        )
                    # Include the small crest/bottom-wave layers adjoining the
                    # tall waterfall body without exporting the whole river.
                    spatial_bounds = suggest_spatial_feature_bounds(anchor, padding=24.0)
                else:
                    spatial_bounds = suggest_spatial_tile_bounds(anchor)
        from .gltf.extract import suggest_tile_surface_origin_y

        return (
            component_indices,
            repeat_patches,
            spatial_bounds,
            preserve_spatial_components,
            spatial_component_center_filter,
            suggest_tile_surface_origin_y(self._material_components, tuple(materials)),
        )

    def _sync_component_controls(self) -> None:
        focused = self._focused_materials()
        material = focused[0] if focused else ""
        row_key = self._focused_row_key()
        components = self._focused_row_components()
        count = len(components)
        index = min(self._component_indices.get(row_key, 0), max(0, count - 1))
        enabled = count > 0
        automatic_spatial = row_key in self._automatic_spatial_rows
        self.previous_piece_button.setEnabled(count > 1)
        self.next_piece_button.setEnabled(count > 1)
        self.component_label.setText(f"Tile {index + 1} / {count}" if enabled else "Whole part")
        batch_enabled = automatic_spatial and count > 1 and bool(focused) and self._asset is not None
        self.export_all_button.setEnabled(batch_enabled)
        self.export_all_button.setText(f"Export all {count}…" if batch_enabled else "Export all tiles…")
        self.repeat_patch.blockSignals(True)
        self.repeat_patch.setEnabled(
            not automatic_spatial
            and not self._spatial_tile_enabled
            and enabled
            and len(focused) == 1
            and bool(getattr(components[index], "is_planar", False))
        )
        self.repeat_patch.setChecked(material.casefold() in self._repeat_patch_materials)
        self.repeat_patch.blockSignals(False)
        self.whole_object.blockSignals(True)
        self.whole_object.setChecked(automatic_spatial or self._spatial_tile_enabled)
        self.whole_object.blockSignals(False)
        self.whole_object.setEnabled(enabled and not automatic_spatial)
        if row_key in self._logical_layer_rows:
            self.whole_object.setToolTip(
                "This object is already one logical tile made from all co-located DS material layers."
            )
        elif automatic_spatial:
            self.whole_object.setToolTip(
                "This repeated batch is automatically clipped to its inferred UV-sized map tile."
            )
        else:
            self.whole_object.setToolTip(
                "Combine co-located geometry layers inside the same inferred DS tile footprint."
            )

    def _change_component(self, delta: int) -> None:
        focused = self._focused_materials()
        if not focused:
            return
        material = focused[0]
        row_key = self._focused_row_key()
        components = self._focused_row_components()
        if not components:
            return
        index = (self._component_indices.get(row_key, 0) + int(delta)) % len(components)
        self._component_indices[row_key] = index
        component = components[index]
        key = material.casefold()
        if row_key not in self._automatic_spatial_rows:
            if bool(getattr(component, "repeat_patch_recommended", False)):
                self._repeat_patch_materials.add(key)
            else:
                self._repeat_patch_materials.discard(key)
        self._sync_component_controls()
        self._request_selection_preview()

    def _repeat_patch_toggled(self, checked: bool) -> None:
        focused = self._focused_materials()
        if not focused:
            return
        key = focused[0].casefold()
        if checked:
            self._repeat_patch_materials.add(key)
        else:
            self._repeat_patch_materials.discard(key)
        self._request_selection_preview()

    def _whole_object_toggled(self, checked: bool) -> None:
        self._spatial_tile_enabled = bool(checked)
        focused = self._focused_materials()
        if checked and focused:
            labels = [
                material
                for index in range(self.parts.topLevelItemCount())
                for material in self._item_materials(self.parts.topLevelItem(index))
            ]
            from .gltf.extract import suggest_logical_materials

            related = {name.casefold() for name in suggest_logical_materials(labels, focused[0])}
            related.update(material.casefold() for material in focused)
            self.parts.blockSignals(True)
            try:
                for index in range(self.parts.topLevelItemCount()):
                    item = self.parts.topLevelItem(index)
                    item_materials = {
                        material.casefold() for material in self._item_materials(item)
                    }
                    if item_materials & related:
                        item.setCheckState(0, Qt.CheckState.Checked)
            finally:
                self.parts.blockSignals(False)
            self.export_button.setEnabled(bool(self._selected_materials() and self._asset))
        self._sync_component_controls()
        self._request_selection_preview()

    def _request_selection_preview(self) -> None:
        selected = self._preview_materials()
        self._sync_component_controls()
        self.preview_3d_button.setEnabled(bool(selected and self._asset and self._source_glb))
        self._preview_generation += 1
        if not selected:
            self._preview_source = QPixmap()
            self._show_preview_fallback("Click any tile part to preview it here.")
            self.preview_title.setText("Tile preview")
            return
        item = self.parts.currentItem()
        animation = item.text(3) if item is not None else ""
        focused_count = len(self._focused_materials())
        companion_count = max(0, len(selected) - focused_count)
        companion_label = f"  ·  +{companion_count} companion layer(s)" if companion_count else ""
        self.preview_title.setText(
            f"{selected[0]}  —  {animation or 'static'}{companion_label}"
        )
        self.selection_preview.setPixmap(QPixmap())
        generation = self._preview_generation
        self.selection_preview.setText(f"Rendering {selected[0]} in the tile viewport…")
        if not self.isVisible():
            self._preview_pending = True
            return
        self._preview_pending = False
        QTimer.singleShot(120, lambda: self._refresh_selection_preview(generation))

    def _focused_selection_changed(self) -> None:
        self._request_selection_preview()

    def _toggle_item_for_export(self, item: QTreeWidgetItem, _column: int) -> None:
        state = Qt.CheckState.Unchecked if item.checkState(0) == Qt.CheckState.Checked else Qt.CheckState.Checked
        item.setCheckState(0, state)

    def _set_preview_camera(self, *, top: bool) -> None:
        self._preview_yaw = 0.0 if top else 35.0
        self._preview_pitch = 89.0 if top else 28.0
        self.top_button.setChecked(top)
        self.perspective_button.setChecked(not top)
        self._request_selection_preview()

    def _selection_changed(self, _item: QTreeWidgetItem, column: int) -> None:
        if column != 0:
            return
        selected = self._selected_materials()
        self.export_button.setEnabled(bool(selected and self._asset))
        if self.parts.currentItem() is None:
            self._request_selection_preview()

    def _ensure_live_tile_preview(self):
        """Create the side WebGL viewport lazily, without importing shared UI."""
        if self._live_tile_preview is not None:
            return self._live_tile_preview
        if self._live_preview_attempted:
            return None
        main_preview = getattr(self._window, "preview", None)
        main_web = getattr(main_preview, "_web_view", None)
        if main_web is None:
            return None
        self._live_preview_attempted = True
        if not hasattr(main_web, "is_available") or not main_web.is_available():
            return None
        try:
            live = type(main_web)(self)
            if not live.is_available():
                live.deleteLater()
                return None
            live.setMinimumSize(220, 160)
            live.set_background_name("Checkered")
            self._preview_host_layout.insertWidget(0, live, 1)
            self._live_tile_preview = live
            return live
        except Exception:
            return None

    def _show_preview_fallback(self, message: str) -> None:
        if self._live_tile_preview is not None:
            self._live_tile_preview.hide()
        self.selection_preview.show()
        self.camera_controls.show()
        self.selection_preview.setPixmap(QPixmap())
        self.selection_preview.setText(message)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._load_component_catalog()
        if self._preview_pending or self._preview_materials():
            self._request_selection_preview()

    def _refresh_selection_preview(self, generation: int) -> None:
        if generation != self._preview_generation or self._asset is None:
            return
        selected = self._preview_materials()
        source_glb = self._source_glb
        if not selected or source_glb is None or not source_glb.is_file():
            return
        (
            component_indices,
            repeat_patches,
            spatial_bounds,
            preserve_spatial_components,
            spatial_component_center_filter,
            origin_y,
        ) = self._extraction_options(selected)
        live = self._ensure_live_tile_preview()
        if live is not None:
            old_workspace = self._side_preview_workspace
            workspace = tempfile.TemporaryDirectory(prefix="rae_nds_tile_side_")
            try:
                from .export_module.tile_extract import build_preview_materials_glb

                bundle = build_preview_materials_glb(
                    source_glb=source_glb,
                    selected_materials=selected,
                    mesh_labels=list(self._source_mesh_labels),
                    mesh_texture_paths=list(self._source_mesh_texture_paths),
                    texture_by_name=dict(self._source_preview_state.get("texture_by_name", {})),
                    fallback_paths=list(self._source_preview_state.get("fallback_textures", [])),
                    material_to_texture=dict(self._source_preview_state.get("material_to_texture", {})),
                    texture_bind_order=list(self._source_preview_state.get("texture_bind_order", [])),
                    out_dir=Path(workspace.name),
                    component_indices=component_indices,
                    repeat_patch_materials=repeat_patches,
                    spatial_tile_bounds=spatial_bounds,
                    preserve_spatial_components=preserve_spatial_components,
                    spatial_component_center_filter=spatial_component_center_filter,
                    origin_y=origin_y,
                )
                if generation != self._preview_generation:
                    workspace.cleanup()
                    return
                self._side_preview_workspace = workspace
                live.load_glb(bundle.patched_glb, preview_platform_id="nds")
                live.show()
                self.selection_preview.hide()
                self.camera_controls.hide()
                from .gltf.glb_io import read_glb

                motion = (((read_glb(bundle.patched_glb).json.get("extras") or {}).get("rae") or {}).get("mapMaterialMotion") or {})
                default_clip = str(motion.get("defaultClip") or "")
                if default_clip:
                    for delay in (650, 1300):
                        QTimer.singleShot(
                            delay,
                            lambda clip=default_clip, expected=generation: (
                                live.play_animation(clip) if expected == self._preview_generation else None
                            ),
                        )
                if old_workspace is not None:
                    old_workspace.cleanup()
                return
            except Exception as exc:
                workspace.cleanup()
                self._show_preview_fallback(f"Live tile preview failed; trying snapshot…\n{exc}")
        try:
            from .export_module.tile_extract import render_preview_materials_png

            png = render_preview_materials_png(
                source_glb=source_glb,
                selected_materials=selected,
                mesh_labels=list(self._source_mesh_labels),
                mesh_texture_paths=list(self._source_mesh_texture_paths),
                texture_by_name=dict(self._source_preview_state.get("texture_by_name", {})),
                fallback_paths=list(self._source_preview_state.get("fallback_textures", [])),
                material_to_texture=dict(self._source_preview_state.get("material_to_texture", {})),
                texture_bind_order=list(self._source_preview_state.get("texture_bind_order", [])),
                width=max(320, self.selection_preview.width() - 12),
                height=max(240, self.selection_preview.height() - 12),
                yaw_deg=self._preview_yaw,
                pitch_deg=self._preview_pitch,
                component_indices=component_indices,
                repeat_patch_materials=repeat_patches,
                spatial_tile_bounds=spatial_bounds,
                preserve_spatial_components=preserve_spatial_components,
                spatial_component_center_filter=spatial_component_center_filter,
                origin_y=origin_y,
            )
        except Exception as exc:
            if generation == self._preview_generation:
                self._show_preview_fallback(f"Exact preview could not be rendered:\n{exc}")
            return
        if generation != self._preview_generation:
            return
        pixmap = QPixmap()
        if not png or not pixmap.loadFromData(png, "PNG"):
            self._show_preview_fallback(
                "Exact preview is unavailable in this runtime, but Open in main and export are still available."
            )
            return
        self.selection_preview.show()
        self.camera_controls.show()
        self._preview_source = pixmap
        self._fit_selection_preview()

    def _preview_selected_3d(self) -> None:
        selected = self._preview_materials()
        source_glb = self._source_glb
        preview = getattr(self._window, "preview", None)
        if not selected or source_glb is None or preview is None:
            return
        (
            component_indices,
            repeat_patches,
            spatial_bounds,
            preserve_spatial_components,
            spatial_component_center_filter,
            origin_y,
        ) = self._extraction_options(selected)
        if self._tile_preview_workspace is not None:
            self._tile_preview_workspace.cleanup()
        self._tile_preview_workspace = tempfile.TemporaryDirectory(prefix="rae_nds_tile_3d_")
        try:
            from .export_module.tile_extract import build_preview_materials_glb

            bundle = build_preview_materials_glb(
                source_glb=source_glb,
                selected_materials=selected,
                mesh_labels=list(self._source_mesh_labels),
                mesh_texture_paths=list(self._source_mesh_texture_paths),
                texture_by_name=dict(self._source_preview_state.get("texture_by_name", {})),
                fallback_paths=list(self._source_preview_state.get("fallback_textures", [])),
                material_to_texture=dict(self._source_preview_state.get("material_to_texture", {})),
                texture_bind_order=list(self._source_preview_state.get("texture_bind_order", [])),
                out_dir=Path(self._tile_preview_workspace.name),
                component_indices=component_indices,
                repeat_patch_materials=repeat_patches,
                spatial_tile_bounds=spatial_bounds,
                preserve_spatial_components=preserve_spatial_components,
                spatial_component_center_filter=spatial_component_center_filter,
                origin_y=origin_y,
            )
            preview.load_glb(
                bundle.patched_glb,
                fallback_textures=[],
                texture_by_name={},
                material_to_texture={},
                texture_bind_order=[],
                preview_platform_id="nds",
            )
            map_widget = getattr(self._window, "nds_map_objects", None)
            if map_widget is not None and hasattr(map_widget, "allow_temporary_preview"):
                map_widget.allow_temporary_preview(bundle.patched_glb)
            store = getattr(self._window, "_store_preview_mesh_labels", None)
            if callable(store):
                store(list(getattr(preview, "_last_mesh_labels", [])))
            from .gltf.glb_io import read_glb

            motion = (((read_glb(bundle.patched_glb).json.get("extras") or {}).get("rae") or {}).get("mapMaterialMotion") or {})
            default_clip = str(motion.get("defaultClip") or "")
            if default_clip:
                QTimer.singleShot(900, lambda: preview.play_glb_animation(default_clip))
            self.selection_preview.setText(
                f"Opened one {selected[0]} tile in the main 3D viewport."
                + (f" Playing {default_clip}." if default_clip else "")
            )
        except Exception as exc:
            QMessageBox.critical(self, "3D tile preview failed", str(exc))

    def _restore_source_model(self) -> None:
        preview = getattr(self._window, "preview", None)
        if preview is None or self._source_glb is None:
            return
        preview.load_glb(self._source_glb, preview_platform_id="nds", **self._source_preview_state)
        map_widget = getattr(self._window, "nds_map_objects", None)
        if map_widget is not None and hasattr(map_widget, "allow_temporary_preview"):
            map_widget.allow_temporary_preview(self._source_glb)
        store = getattr(self._window, "_store_preview_mesh_labels", None)
        if callable(store):
            store(list(getattr(preview, "_last_mesh_labels", [])))

    def _fit_selection_preview(self) -> None:
        if self._preview_source.isNull():
            return
        fitted = self._preview_source.scaled(
            max(1, self.selection_preview.width() - 12),
            max(1, self.selection_preview.height() - 12),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.selection_preview.setText("")
        self.selection_preview.setPixmap(fitted)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_selection_preview()

    def _export(self) -> None:
        asset = self._asset
        explicitly_selected = self._selected_materials()
        if asset is None or not explicitly_selected:
            QMessageBox.information(self, "Nothing selected", "Check at least one material part to export.")
            return
        selected = self._assembled_materials(explicitly_selected)
        suggested = f"{Path(asset.virtual_path).stem}_{explicitly_selected[0]}.tile"
        output_name, _ = QFileDialog.getSaveFileName(
            self,
            "Export selected tile",
            _default_export_file(suggested, "tiles"),
            "Pokemon Resort tile (*.tile)",
        )
        if not output_name:
            return
        output = Path(output_name)
        if output.suffix.casefold() != ".tile":
            output = output.with_suffix(".tile")
        preview = self._window.preview
        (
            component_indices,
            repeat_patches,
            spatial_bounds,
            preserve_spatial_components,
            spatial_component_center_filter,
            origin_y,
        ) = self._extraction_options(selected)
        try:
            from .export_module.tile_extract import export_preview_materials_as_tile

            update = getattr(self._window, "_update_status", None)
            if callable(update):
                update(f"Extracting {len(selected)} NDS material part(s) as {output.name}…")
            result = export_preview_materials_as_tile(
                asset=asset,
                source_glb=self._source_glb or Path(preview._last_path),
                selected_materials=selected,
                output_path=output,
                mesh_labels=list(self._source_mesh_labels),
                mesh_texture_paths=list(self._source_mesh_texture_paths),
                texture_by_name=dict(self._source_preview_state.get("texture_by_name", {})),
                fallback_paths=list(self._source_preview_state.get("fallback_textures", [])),
                material_to_texture=dict(self._source_preview_state.get("material_to_texture", {})),
                texture_bind_order=list(self._source_preview_state.get("texture_bind_order", [])),
                material_specs=self._window._material_sequence_spec(asset.asset_id),
                component_indices=component_indices,
                repeat_patch_materials=repeat_patches,
                spatial_tile_bounds=spatial_bounds,
                preserve_spatial_components=preserve_spatial_components,
                spatial_component_center_filter=spatial_component_center_filter,
                origin_y=origin_y,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Tile extraction failed", str(exc))
            return
        if callable(update):
            update(f"Exported isolated NDS tile: {result}")
        QMessageBox.information(self, "Tile exported", f"Created:\n{result}")

    @staticmethod
    def _batch_footprint(occurrence: object, *, tile_size: float = 16.0) -> tuple[int, int]:
        width = max(1, round((float(occurrence.bounds_max[0]) - float(occurrence.bounds_min[0])) / tile_size))
        height = max(1, round((float(occurrence.bounds_max[2]) - float(occurrence.bounds_min[2])) / tile_size))
        return width, height

    def _export_all_occurrences(self) -> None:
        asset = self._asset
        focused = self._focused_materials()
        occurrences = self._focused_row_components()
        row_key = self._focused_row_key()
        if (
            asset is None
            or not focused
            or row_key not in self._automatic_spatial_rows
            or len(occurrences) < 2
        ):
            QMessageBox.information(
                self,
                "No tile set selected",
                "Focus a row with arrow-selectable tile occurrences first.",
            )
            return
        folder_name = QFileDialog.getExistingDirectory(
            self,
            "Export every tile in this row",
            str(_default_export_directory("tiles")),
        )
        if not folder_name:
            return

        from .export_module.tile_extract import (
            PreviewTileBatchItem,
            export_preview_material_occurrences_as_tiles,
        )

        output_dir = Path(folder_name)
        asset_stem = Path(str(asset.virtual_path).split("#", 1)[0]).stem or "nds_map"
        row_slug = re.sub(r"[^A-Za-z0-9_-]+", "_", row_key.replace(":", "_")).strip("_") or "tiles"
        kind_counts: dict[str, int] = {}
        items: list[PreviewTileBatchItem] = []
        for occurrence in occurrences:
            footprint = self._batch_footprint(occurrence)
            shape = "corner" if footprint[0] > 1 and footprint[1] > 1 else "straight"
            kind = f"{shape}_{footprint[0]}x{footprint[1]}"
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            serial = kind_counts[kind]
            name = f"{asset_stem} {row_slug.replace('_', ' ')} {shape} {serial:02d} ({footprint[0]}x{footprint[1]})"
            filename = f"{asset_stem}_{row_slug}_{kind}_{serial:02d}.tile"
            bounds = (
                float(occurrence.bounds_min[0]),
                float(occurrence.bounds_min[2]),
                float(occurrence.bounds_max[0]),
                float(occurrence.bounds_max[2]),
            )
            selected_materials = tuple(
                self._assembled_materials_for_occurrence(focused, occurrence)
            )
            from .gltf.extract import suggest_tile_surface_origin_y

            items.append(
                PreviewTileBatchItem(
                    name=name,
                    filename=filename,
                    selected_materials=selected_materials,
                    spatial_tile_bounds=bounds,
                    footprint=footprint,
                    origin_y=suggest_tile_surface_origin_y(
                        self._material_components,
                        selected_materials,
                    ),
                )
            )

        preview = self._window.preview
        update = getattr(self._window, "_update_status", None)
        self.export_all_button.setEnabled(False)
        self.export_button.setEnabled(False)

        def report(current: int, total: int, output: Path) -> None:
            if callable(update):
                update(f"Exporting exact NDS tile {current} / {total}: {output.name}")
            QApplication.processEvents()

        try:
            results = export_preview_material_occurrences_as_tiles(
                asset=asset,
                source_glb=self._source_glb or Path(preview._last_path),
                items=items,
                output_dir=output_dir,
                mesh_labels=list(self._source_mesh_labels),
                mesh_texture_paths=list(self._source_mesh_texture_paths),
                texture_by_name=dict(self._source_preview_state.get("texture_by_name", {})),
                fallback_paths=list(self._source_preview_state.get("fallback_textures", [])),
                material_to_texture=dict(self._source_preview_state.get("material_to_texture", {})),
                texture_bind_order=list(self._source_preview_state.get("texture_bind_order", [])),
                material_specs=self._window._material_sequence_spec(asset.asset_id),
                progress=report,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Tile set extraction failed", str(exc))
            return
        finally:
            self.export_button.setEnabled(bool(self._selected_materials() and self._asset))
            self._sync_component_controls()

        if callable(update):
            update(f"Exported {len(results)} exact NDS tiles to {output_dir}")
        QMessageBox.information(
            self,
            "Tile set exported",
            f"Created {len(results)} layered tile bundles in:\n{output_dir}",
        )


class NdsMapObjectsWidget(QWidget):
    """NDS-only map composition and placed-building exporter."""

    def __init__(self, window: object) -> None:
        super().__init__()
        self._window = window
        self._asset: Asset | None = None
        self._asset_id = ""
        self._terrain_glb: Path | None = None
        self._terrain_preview_state: dict[str, object] = {}
        self._composition = None
        self._worker: _MapCompositionWorker | None = None
        self._shutting_down = False
        self._auto_retry_count = 0
        self._exact_reassert_pending = False
        self._intentional_preview_path: Path | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self.summary = QLabel(
            "Select a carved Gen 5 map to resolve its externally stored buildings."
        )
        self.summary.setWordWrap(False)
        self.summary.setToolTip(
            "Gen 5 maps store buildings outside the terrain model. RAE resolves them from AreaData and placement records."
        )
        layout.addWidget(self.summary)

        variant_row = QHBoxLayout()
        variant_row.addWidget(QLabel("Variant"))
        self.variant_combo = QComboBox()
        self.variant_combo.setToolTip(
            "Switch the map geometry and matching AreaData texture/lighting as one exact variant."
        )
        self.variant_combo.currentIndexChanged.connect(self._variant_changed)
        variant_row.addWidget(self.variant_combo, 1)
        self.view_combo = QComboBox()
        self.view_combo.setToolTip("Choose whether the viewport shows the composed map or terrain alone.")
        self.view_combo.addItem("Map + models", "composed")
        self.view_combo.addItem("Terrain only", "terrain")
        self.view_combo.currentIndexChanged.connect(self._view_changed)
        variant_row.addWidget(self.view_combo)
        self.load_button = QPushButton("Retry")
        self.load_button.clicked.connect(self._load_objects)
        self.load_button.setVisible(False)
        variant_row.addWidget(self.load_button)
        self.map_export_button = QPushButton("Export…")
        self.map_export_button.setToolTip("Export the map, selected building, tile, or discovered doors.")
        self.map_export_button.clicked.connect(self._show_export_dialog)
        variant_row.addWidget(self.map_export_button)
        layout.addLayout(variant_row)

        self.objects = QTreeWidget()
        self.objects.setHeaderLabels(["#", "Placed model", "Position (x, y, z)", "Rotation"])
        self.objects.setRootIsDecorated(False)
        self.objects.setAlternatingRowColors(True)
        self.objects.itemSelectionChanged.connect(self._selection_changed)
        self.objects.itemDoubleClicked.connect(lambda _item, _column: self._preview_selected())
        self.objects.setMinimumHeight(150)
        header = self.objects.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.objects, 1)

        footer = QHBoxLayout()
        self.selection_status = QLabel("Select a building; double-click to preview it.")
        footer.addWidget(self.selection_status, 1)
        self.preview_button = QPushButton("Preview selected")
        self.preview_button.clicked.connect(self._preview_selected)
        footer.addWidget(self.preview_button)
        layout.addLayout(footer)
        self.set_context(None)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown_map_worker)

    def set_context(self, asset: Asset | None) -> None:
        same_asset = bool(asset and asset.asset_id == self._asset_id)
        self._asset = asset
        worker_running = self._worker is not None and self._worker.isRunning()
        if same_asset and (self._composition is not None or worker_running):
            if self._composition is not None:
                self._schedule_exact_reassert()
            return
        if not same_asset:
            self._auto_retry_count = 0
            self._intentional_preview_path = None
        self._asset_id = asset.asset_id if asset else ""
        self.objects.clear()
        self._composition = None
        self.variant_combo.blockSignals(True)
        self.variant_combo.clear()
        self.variant_combo.blockSignals(False)
        self.variant_combo.setEnabled(False)
        self.map_export_button.setEnabled(False)
        self.view_combo.setEnabled(False)
        animations = getattr(self._window, "nds_animations", None)
        tabs = getattr(self._window, "preview_inspector_tabs", None)
        animation_index = getattr(self._window, "_inspector_tab_nds_animations", None)
        if animations is not None:
            animations.clear()
        if tabs is not None and animation_index is not None:
            tabs.setTabVisible(animation_index, False)
        preview = getattr(self._window, "preview", None)
        source = getattr(preview, "_last_path", None)
        self._terrain_glb = Path(source) if source and Path(source).is_file() else None
        self._terrain_preview_state = {
            "fallback_textures": list(getattr(preview, "_fallback_texture_paths", [])),
            "texture_by_name": dict(getattr(preview, "_texture_by_name", {})),
            "material_to_texture": dict(getattr(preview, "_material_to_texture", {})),
            "texture_bind_order": list(getattr(preview, "_texture_bind_order", [])),
        }
        ready = bool(asset and _is_gen5_map_path(asset.virtual_path) and self._terrain_glb)
        self.load_button.setEnabled(ready)
        self.load_button.setVisible(False)
        self._set_selection_actions(False)
        if asset is None:
            self._set_summary("Select a carved Gen 5 a/0/0/8 map model.")
        elif not _is_gen5_map_path(asset.virtual_path):
            self._set_summary("This model is not a carved Gen 5 a/0/0/8 map.")
        elif self._terrain_glb is None:
            self._set_summary("Preview this map once, then load its externally stored buildings.")
        else:
            self._set_summary(
                "Loading the exact AreaData texture, DS material animations, and placed models in the background…"
            )
            self._queue_auto_load()

    def _set_summary(self, text: str, details: str = "") -> None:
        self.summary.setText(text)
        self.summary.setToolTip(details or text)

    def _set_selection_actions(self, enabled: bool) -> None:
        self.preview_button.setEnabled(enabled)
        self.preview_button.setVisible(enabled)
        selected = self._selected_preview() if enabled else None
        if selected is None:
            self.selection_status.setText("Select a building; double-click to preview it.")
        else:
            door_note = " · animated door" if selected.door is not None else " · no resolved door"
            self.selection_status.setText(f"{selected.model.name}{door_note}")

    def _selection_changed(self) -> None:
        self._set_selection_actions(self._selected_preview() is not None)

    def _selected_preview(self):
        if self._composition is None:
            return None
        selected = self.objects.selectedItems()
        if not selected:
            return None
        index = selected[0].data(0, Qt.ItemDataRole.UserRole)
        try:
            return self._composition.previews[int(index)]
        except (IndexError, TypeError, ValueError):
            return None

    def _status(self, message: str) -> None:
        update = getattr(self._window, "_update_status", None)
        if callable(update):
            update(message)
        QApplication.processEvents()

    def _queue_auto_load(self, delay_ms: int = 0) -> None:
        asset_id = self._asset_id
        QTimer.singleShot(delay_ms, lambda: self._auto_load_objects(asset_id))

    def _schedule_exact_reassert(self) -> None:
        if self._exact_reassert_pending:
            return
        self._exact_reassert_pending = True
        QTimer.singleShot(0, self._finish_exact_reassert)

    def _finish_exact_reassert(self) -> None:
        self._exact_reassert_pending = False
        self.ensure_exact_preview()

    def allow_temporary_preview(self, path: Path) -> None:
        try:
            self._intentional_preview_path = Path(path).resolve()
        except (OSError, TypeError, ValueError):
            self._intentional_preview_path = None

    def ensure_exact_preview(self, *, force: bool = False) -> bool:
        """Restore the authoritative composed GLB after a late generic preview wins a race."""
        composition = self._composition
        preview = getattr(self._window, "preview", None)
        if composition is None or preview is None:
            return False
        current = getattr(preview, "_last_path", None)
        try:
            current_path = Path(current).resolve() if current else None
            exact_paths = {
                Path(composition.composed_glb).resolve(),
                Path(composition.terrain_glb).resolve(),
            }
        except (OSError, TypeError, ValueError):
            current_path = None
            exact_paths = set()
        if current_path in exact_paths:
            return False
        if not force and current_path is not None and current_path == self._intentional_preview_path:
            return False
        self._show_composed()
        return True

    def _auto_load_objects(self, asset_id: str) -> None:
        if asset_id != self._asset_id or self._composition is not None:
            return
        self._start_map_load(automatic=True)

    def _load_objects(self) -> None:
        self._auto_retry_count = 0
        self._start_map_load(automatic=False)

    def _variant_changed(self, _index: int) -> None:
        data = self.variant_combo.currentData()
        if not data or self._worker is not None:
            return
        current_key = getattr(getattr(self._composition, "objects", None), "variant_key", "")
        if str(data[2]) == str(current_key):
            return
        self._set_summary(f"Loading {self.variant_combo.currentText()} with its exact texture and models…")
        self._start_map_load(automatic=False)

    def _view_changed(self, _index: int) -> None:
        if self.view_combo.currentData() == "terrain":
            self._show_terrain()
        else:
            self._show_composed()

    def _start_map_load(self, *, automatic: bool) -> None:
        asset = self._asset
        rom_path = getattr(self._window, "rom_path", None)
        if asset is None or not rom_path or self._terrain_glb is None:
            if automatic and self._auto_retry_count < 2:
                self._auto_retry_count += 1
                self._queue_auto_load(250)
                return
            self.load_button.setVisible(bool(asset and _is_gen5_map_path(asset.virtual_path)))
            self.load_button.setEnabled(bool(asset))
            self._set_summary("Waiting for the source ROM and carved terrain preview; use Retry when ready.")
            if not automatic:
                QMessageBox.information(self, "Map unavailable", "Open the source ROM and preview this map first.")
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self.load_button.setEnabled(False)
        self.load_button.setVisible(False)
        self.variant_combo.setEnabled(False)
        self.map_export_button.setEnabled(False)
        self.view_combo.setEnabled(False)
        self._set_summary("Resolving the exact map texture, animation, and building pack…")
        preview_temp = Path(getattr(self._window, "preview_temp", tempfile.gettempdir()))
        variant_data = self.variant_combo.currentData()
        map_override = int(variant_data[0]) if variant_data else None
        area_override = int(variant_data[1]) if variant_data else None
        variant_suffix = f"{map_override}_{area_override}" if variant_data else "default"
        out_dir = preview_temp / asset.asset_id / "exact_map" / variant_suffix
        worker = _MapCompositionWorker(
            asset_id=asset.asset_id,
            rom_path=Path(rom_path),
            virtual_path=asset.virtual_path,
            terrain_glb=self._terrain_glb,
            out_dir=out_dir,
            automatic=automatic,
            map_index_override=map_override,
            area_index_override=area_override,
        )
        self._worker = worker
        worker.progress.connect(self._status)
        # Consume the result from QThread.finished, after run() has returned.
        # Clearing the last Python reference from the earlier custom result
        # signal could destroy the QThread during its final few instructions.
        worker.finished.connect(lambda current=worker: self._map_worker_finished(current))
        worker.start()

    def _map_worker_finished(self, worker: _MapCompositionWorker) -> None:
        if worker is not self._worker:
            worker.deleteLater()
            return
        self._worker = None
        composition = worker.result
        error = worker.error
        automatic = worker.automatic
        asset_id = worker.asset_id
        worker.deleteLater()
        if self._shutting_down:
            return
        self._composition_finished(asset_id, composition, error, automatic=automatic)

    def _shutdown_map_worker(self) -> None:
        """Let the exact-map thread stop before Qt tears down its QObject."""
        self._shutting_down = True
        worker = self._worker
        if worker is None:
            return
        if worker.isRunning():
            worker.requestInterruption()
            worker.wait()
        self._worker = None

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API name
        self._shutdown_map_worker()
        super().closeEvent(event)

    def _composition_finished(
        self,
        asset_id: str,
        composition: object,
        error: str,
        *,
        automatic: bool,
    ) -> None:
        if asset_id != self._asset_id:
            if self._asset is not None and self._terrain_glb is not None:
                self._queue_auto_load(50)
            return
        if error or composition is None:
            self._composition = None
            if automatic and self._auto_retry_count < 2:
                self._auto_retry_count += 1
                self._set_summary("Retrying exact AreaData textures and placed models…")
                self._queue_auto_load(200)
                return
            self.load_button.setEnabled(True)
            self.load_button.setVisible(True)
            self.variant_combo.setEnabled(self.variant_combo.count() > 1)
            self._set_summary("Exact map could not be loaded. Use Retry.", error)
            self._status(f"Exact map loading failed: {error}")
            return
        self._auto_retry_count = 0
        self._composition = composition
        self.variant_combo.blockSignals(True)
        self.variant_combo.clear()
        current_variant_row = 0
        for row, variant in enumerate(composition.objects.variants):
            self.variant_combo.addItem(
                f"{variant.label}  ·  texture {variant.texture_index}",
                (variant.map_file_index, variant.area_index, variant.key),
            )
            if variant.key == composition.objects.variant_key:
                current_variant_row = row
        self.variant_combo.setCurrentIndex(current_variant_row)
        self.variant_combo.blockSignals(False)
        self.variant_combo.setEnabled(self.variant_combo.count() > 1)
        self.map_export_button.setEnabled(True)
        self.view_combo.setEnabled(True)
        self.objects.clear()

        door_count = 0
        inferred_door_count = 0
        for preview_index, preview in enumerate(composition.previews):
            placement = preview.placement
            door = preview.door
            if door is not None:
                door_count += 1
                inferred_door_count += int(door.source == "event_warp")
            model_label = f"{preview.model.name}  [model {preview.model.index}]"
            if door is not None:
                model_label += f"  +  {door.model.name} (closed)"
            item = QTreeWidgetItem(
                [
                    str(placement.index),
                    model_label,
                    f"{placement.x:g}, {placement.y:g}, {placement.z:g}",
                    f"{placement.rotation_degrees}°",
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, preview_index)
            item.setToolTip(
                1,
                f"{composition.objects.model_archive_path}, pack {composition.objects.area.building_pack}, model {preview.model.index}"
                + (
                    f"\nAnimated door {door.model.index}: {door.model.name}; "
                    + (
                        f"inferred from destination {door.interior_family} and event warp "
                        f"(confidence {door.confidence:.0%})."
                        if door.source == "event_warp"
                        else "referenced directly by the building's AB record."
                    )
                    + " Select the building and press Play to animate it."
                    if door is not None
                    else "\nNo explicit or high-confidence event-warp door resolves for this placement."
                ),
            )
            self.objects.addTopLevelItem(item)
        for column in range(4):
            self.objects.resizeColumnToContents(column)
        area = composition.objects.area
        location = "outside" if area.is_outside else "inside"
        details = (
            f"Exact AreaData textures and DS animations loaded; {len(composition.previews)} placed model(s) "
            f"and {door_count} animated door attachment(s) loaded "
            f"({door_count - inferred_door_count} explicit, {inferred_door_count} inferred from ROM event warps) from "
            f"{location} building pack {area.building_pack}; "
            f"variant {composition.objects.variant_label} (AreaData {area.index}, "
            + (
                f"Zone {composition.objects.zone_index})."
                if composition.objects.zone_index >= 0
                else "inferred from exact texture/model matches)."
            )
            + (
                f" Unsupported placement index(es) retained as metadata: {list(composition.objects.unresolved_model_indices)}."
                if composition.objects.unresolved_model_indices
                else ""
            )
        )
        zone = (
            f"Zone {composition.objects.zone_index}"
            if composition.objects.zone_index >= 0
            else "exact-match inference"
        )
        self._set_summary(
            f"{len(composition.previews)} models · {door_count} doors · {location} pack {area.building_pack} · "
            f"{composition.objects.variant_label} · AreaData {area.index} · {zone}",
            details,
        )
        self.load_button.setVisible(False)
        self.view_combo.blockSignals(True)
        self.view_combo.setCurrentIndex(0)
        self.view_combo.blockSignals(False)
        self._show_composed()

    def _show_export_dialog(self) -> None:
        composition = self._composition
        if composition is None:
            return
        selected = self._selected_preview()
        from .export_module.interior_map import is_gen5_interior_candidate

        try:
            has_interior = is_gen5_interior_candidate(composition.terrain_glb)
        except Exception:
            has_interior = False
        action_ids = _map_export_action_ids(
            has_selection=selected is not None,
            has_door=bool(selected and selected.door is not None),
            has_multiple_variants=self.variant_combo.count() > 1,
            has_discovered_doors=any(preview.door is not None for preview in composition.previews),
            has_interior=has_interior,
        )
        actions = {
            "map": (
                "Current map",
                "Export the composed terrain, placed models, and animations as one GLB.",
                self._export_exact_map_glb,
            ),
            "variants": (
                "All seasons / variants",
                "Export every resolved map variant into a folder.",
                self._export_all_variants,
            ),
            "interior": (
                "Interior shell + grid metadata",
                "Export exact floor/wall geometry plus Resort collision, height, opening, and provenance metadata.",
                self._export_interior_package,
            ),
            "interior_kit": (
                "Interior kit pieces",
                "Export reusable floor, wall, entrance, stair, window, and shadow material groups with a manifest.",
                self._export_interior_kit,
            ),
            "building_doorless": (
                "Selected building — doorless",
                "Export a placement-ready building GLB for Pokémon Resort.",
                lambda: self._export_selected_glb(include_door=False),
            ),
            "building_with_door": (
                "Selected building — animated door",
                "Export the building with its correctly placed door and animation clips.",
                lambda: self._export_selected_glb(include_door=True),
            ),
            "building_tile": (
                "Selected building as .tile",
                "Package the selected building for the Resort map maker.",
                self._export_selected_tile,
            ),
            "doors": (
                "All discovered doors",
                "Export every resolved animated door as a reusable .tile bundle.",
                self._export_all_doors,
            ),
        }

        dialog = QDialog(self)
        dialog.setWindowTitle("Export map objects")
        dialog.setMinimumWidth(520)
        dialog_layout = QVBoxLayout(dialog)
        intro = QLabel("Choose what to export. Options use the current variant and selection.")
        intro.setWordWrap(True)
        dialog_layout.addWidget(intro)
        radios: list[tuple[QRadioButton, object]] = []
        for action_id in action_ids:
            title, description, callback = actions[action_id]
            radio = QRadioButton(title)
            radio.setChecked(not radios)
            dialog_layout.addWidget(radio)
            detail = QLabel(description)
            detail.setWordWrap(True)
            detail.setStyleSheet("color: palette(mid); margin-left: 24px; margin-bottom: 5px;")
            dialog_layout.addWidget(detail)
            radios.append((radio, callback))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        dialog_layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        for radio, callback in radios:
            if radio.isChecked():
                callback()
                return

    def _export_exact_map_glb(self) -> None:
        composition = self._composition
        if composition is None:
            return
        safe_variant = re.sub(r"[^A-Za-z0-9_-]+", "_", composition.objects.variant_label).strip("_")
        output_name, _ = QFileDialog.getSaveFileName(
            self,
            "Export exact composed map",
            _default_export_file(
                f"map_{composition.objects.map_file_index:04d}_{safe_variant or 'exact'}.glb",
                "maps",
            ),
            "glTF binary (*.glb)",
        )
        if not output_name:
            return
        output = Path(output_name)
        if output.suffix.casefold() != ".glb":
            output = output.with_suffix(".glb")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(composition.composed_glb, output)
        self._status(f"Exported exact composed map with buildings and animations: {output}")
        QMessageBox.information(self, "Map exported", f"Created:\n{output}")

    def _choose_interior_output(self, title: str) -> Path | None:
        composition = self._composition
        if composition is None:
            return None
        default = _default_export_directory("interiors") / f"map_{composition.objects.map_file_index:04d}"
        default.mkdir(parents=True, exist_ok=True)
        folder_name = QFileDialog.getExistingDirectory(self, title, str(default))
        return Path(folder_name) if folder_name else None

    def _export_interior_package(self) -> None:
        composition = self._composition
        asset = self._asset
        rom_path = getattr(self._window, "rom_path", None)
        if composition is None or asset is None:
            return
        output_dir = self._choose_interior_output("Export interior shell and grid metadata")
        if output_dir is None:
            return
        from .export_module.interior_map import export_gen5_interior_package

        glb_path, metadata_path = export_gen5_interior_package(
            composition,
            output_dir,
            rom_name=Path(rom_path).stem if rom_path else "",
            virtual_path=asset.virtual_path,
        )
        self._status(f"Exported Resort interior package: {output_dir}")
        QMessageBox.information(
            self,
            "Interior exported",
            f"Created:\n{glb_path}\n{metadata_path}",
        )

    def _export_interior_kit(self) -> None:
        composition = self._composition
        asset = self._asset
        rom_path = getattr(self._window, "rom_path", None)
        if composition is None or asset is None:
            return
        output_dir = self._choose_interior_output("Export reusable interior kit")
        if output_dir is None:
            return
        from .export_module.interior_map import export_gen5_interior_kit, export_gen5_interior_package

        shell_path, metadata_path = export_gen5_interior_package(
            composition,
            output_dir,
            rom_name=Path(rom_path).stem if rom_path else "",
            virtual_path=asset.virtual_path,
        )
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        manifest_path, parts = export_gen5_interior_kit(shell_path, metadata, output_dir / "kit")
        self._status(f"Exported {len(parts)} reusable interior part(s): {manifest_path}")
        QMessageBox.information(
            self,
            "Interior kit exported",
            f"Created {len(parts)} reusable GLB part(s) and:\n{manifest_path}",
        )

    def _export_all_variants(self) -> None:
        composition = self._composition
        asset = self._asset
        rom_path = getattr(self._window, "rom_path", None)
        if composition is None or asset is None or not rom_path or self._terrain_glb is None:
            return
        folder_name = QFileDialog.getExistingDirectory(
            self,
            "Export all map seasons / variants",
            str(_default_export_directory("maps")),
        )
        if not folder_name:
            return
        output_dir = Path(folder_name)
        output_dir.mkdir(parents=True, exist_ok=True)
        preview_temp = Path(getattr(self._window, "preview_temp", tempfile.gettempdir()))
        written: list[Path] = []
        self.map_export_button.setEnabled(False)
        try:
            from .map_objects import build_gen5_map_composition

            for variant in composition.objects.variants:
                self._status(f"Exporting {variant.label} exact map…")
                QApplication.processEvents()
                built = build_gen5_map_composition(
                    Path(rom_path),
                    asset.virtual_path,
                    self._terrain_glb,
                    preview_temp / asset.asset_id / "season_exports" / variant.key.replace(":", "_"),
                    progress=self._status,
                    map_index_override=variant.map_file_index,
                    area_index_override=variant.area_index,
                )
                safe_label = re.sub(r"[^A-Za-z0-9_-]+", "_", variant.label).strip("_")
                output = output_dir / f"map_{variant.map_file_index:04d}_{safe_label}.glb"
                shutil.copyfile(built.composed_glb, output)
                written.append(output)
        except Exception as exc:
            QMessageBox.warning(self, "Season export failed", str(exc))
            return
        finally:
            self.map_export_button.setEnabled(self._composition is not None)
        self._status(f"Exported {len(written)} exact map variant(s) with buildings and animations.")
        QMessageBox.information(
            self,
            "Seasons exported",
            f"Created {len(written)} GLB file(s) in:\n{output_dir}",
        )

    def _load_preview_path(self, path: Path, **state: object) -> None:
        preview = getattr(self._window, "preview", None)
        if preview is None:
            return
        preview.load_glb(path, preview_platform_id="nds", **state)
        store = getattr(self._window, "_store_preview_mesh_labels", None)
        if callable(store):
            store(list(getattr(preview, "_last_mesh_labels", [])))
        if self._asset is not None:
            sync_clips = getattr(self._window, "_sync_texture_clip_preview", None)
            if callable(sync_clips):
                sync_clips(asset_id=self._asset.asset_id)
            refresh_states = getattr(self._window, "_refresh_texture_states", None)
            if callable(refresh_states):
                refresh_states(self._asset)

    def _offer_current_map_to_tile_extractor(self) -> None:
        extractor = getattr(self._window, "nds_tile_extractor", None)
        if extractor is not None and self._asset is not None:
            extractor.set_context(self._asset, force_source=True)

    def _show_composed(self) -> None:
        if self._composition is None:
            return
        self._exact_reassert_pending = False
        self._intentional_preview_path = None
        self._load_preview_path(
            self._composition.composed_glb,
            fallback_textures=[],
            texture_by_name={},
            material_to_texture={},
            texture_bind_order=[],
        )
        self._offer_current_map_to_tile_extractor()
        animations = getattr(self._window, "nds_animations", None)
        tabs = getattr(self._window, "preview_inspector_tabs", None)
        animation_index = getattr(self._window, "_inspector_tab_nds_animations", None)
        if animations is not None:
            count = animations.set_glb(self._composition.composed_glb, autoplay=True)
            if tabs is not None and animation_index is not None:
                tabs.setTabVisible(animation_index, bool(count))
        self._status(f"Showing map {self._composition.objects.map_file_index} with all placed models.")

    def _show_terrain(self) -> None:
        self._intentional_preview_path = None
        exact = self._composition.terrain_glb if self._composition is not None else None
        if exact is not None:
            self._load_preview_path(
                exact,
                fallback_textures=[],
                texture_by_name={},
                material_to_texture={},
                texture_bind_order=[],
            )
            self._offer_current_map_to_tile_extractor()
            self._status("Showing exact AreaData-textured terrain without placed buildings.")
            return
        if self._terrain_glb is None:
            return
        self._load_preview_path(self._terrain_glb, **self._terrain_preview_state)
        self._status("Showing original carved terrain preview.")

    def _preview_selected(self) -> None:
        selected = self._selected_preview()
        if selected is None:
            return
        self.allow_temporary_preview(selected.glb_path)
        self._load_preview_path(
            selected.glb_path,
            fallback_textures=[],
            texture_by_name={},
            material_to_texture={},
            texture_bind_order=[],
        )
        animations = getattr(self._window, "nds_animations", None)
        tabs = getattr(self._window, "preview_inspector_tabs", None)
        animation_index = getattr(self._window, "_inspector_tab_nds_animations", None)
        if animations is not None:
            # Building door clips are brief interaction states. Keep the
            # selected model in its closed bind pose until the user presses
            # Play instead of looping a door continuously in the viewport.
            count = animations.set_glb(selected.glb_path, autoplay=False)
            if tabs is not None and animation_index is not None:
                tabs.setTabVisible(animation_index, bool(count))
        try:
            from .gltf.glb_io import read_glb

            motion = (((read_glb(selected.glb_path).json.get("extras") or {}).get("rae") or {}).get("mapMaterialMotion") or {})
            default_clip = str(motion.get("defaultClip") or "")
            preview = getattr(self._window, "preview", None)
            has_interaction_door = selected.door is not None
            if default_clip and preview is not None and not has_interaction_door:
                for delay in (650, 1300):
                    QTimer.singleShot(delay, lambda clip=default_clip: preview.play_glb_animation(clip))
        except Exception:
            pass
        self._status(f"Previewing placed model {selected.model.index}: {selected.model.name}.")

    def _suggested_name(self, suffix: str) -> str:
        selected = self._selected_preview()
        name = selected.model.name if selected is not None else "building"
        return f"{name}{suffix}"

    def _export_selected_glb(self, *, include_door: bool) -> None:
        selected = self._selected_preview()
        if selected is None:
            return
        if include_door and selected.door is None:
            QMessageBox.information(self, "No attached door", "This building has no resolved animated door.")
            return
        suffix = "_with_animated_door.glb" if include_door else "_doorless.glb"
        output_name, _ = QFileDialog.getSaveFileName(
            self,
            "Export building with animated door" if include_door else "Export doorless building",
            _default_export_file(self._suggested_name(suffix), "buildings"),
            "glTF binary (*.glb)",
        )
        if not output_name:
            return
        output = Path(output_name)
        if output.suffix.casefold() != ".glb":
            output = output.with_suffix(".glb")
        from .gltf.extract import recenter_glb_geometry

        source = _building_export_source(selected, include_door=include_door)
        recenter_glb_geometry(source, output)
        flavor = "with animated door" if include_door else "doorless"
        self._status(f"Exported {flavor} placed building GLB: {output}")
        QMessageBox.information(self, "Model exported", f"Created:\n{output}")

    def _export_selected_tile(self) -> None:
        selected = self._selected_preview()
        composition = self._composition
        if selected is None or composition is None:
            return
        output_name, _ = QFileDialog.getSaveFileName(
            self,
            "Export placed model as tile",
            _default_export_file(self._suggested_name(".tile"), "tiles"),
            "Pokemon Resort tile (*.tile)",
        )
        if not output_name:
            return
        output = Path(output_name)
        if output.suffix.casefold() != ".tile":
            output = output.with_suffix(".tile")
        model = selected.model
        asset = Asset(
            asset_id=f"gen5-map-{composition.objects.map_file_index}-building-{model.index}",
            virtual_path=(
                f"{composition.objects.model_archive_path}/file_{composition.objects.area.building_pack:04d}.bin"
                f"#building_{model.index:02d}_{model.name}.nsbmd"
            ),
            kind="Model",
            magic="BMD0",
            extension=".nsbmd",
            data=model.data,
            original_data=model.data,
        )
        with tempfile.TemporaryDirectory(prefix="rae_nds_building_tile_") as temp_name:
            staging = Path(temp_name)
            from .export_module.tile_bundle import _material_motion_animations, write_tile_archive
            from .gltf.extract import recenter_glb_geometry

            model_glb = recenter_glb_geometry(selected.glb_path, staging / "placement_ready.glb")

            write_tile_archive(
                output,
                model_glb=model_glb,
                asset=asset,
                animations=_material_motion_animations(model_glb, staging),
                staging=staging,
            )
        self._status(f"Exported placed building tile: {output}")
        QMessageBox.information(self, "Tile exported", f"Created:\n{output}")

    def _export_all_doors(self) -> None:
        composition = self._composition
        if composition is None:
            return
        folder_name = QFileDialog.getExistingDirectory(
            self,
            "Export every discovered door",
            str(_default_export_directory("doors")),
        )
        if not folder_name:
            return
        from .export_module.door_tile import export_all_discovered_door_tiles

        output_dir = Path(folder_name)
        written = export_all_discovered_door_tiles(composition, output_dir)
        self._status(f"Exported {len(written)} animated door tile(s) to {output_dir}")
        QMessageBox.information(
            self,
            "Doors exported",
            f"Created {len(written)} .tile file(s) in:\n{output_dir}",
        )


def install_nds_tile_extractor(window: object) -> None:
    tabs = getattr(window, "preview_inspector_tabs", None)
    if tabs is None or hasattr(window, "nds_tile_extractor"):
        return
    widget = NdsTileExtractorWidget(window)
    window.nds_tile_extractor = widget
    window._inspector_tab_nds_tile_extractor = tabs.addTab(widget, "Tile Extractor")
    tabs.setTabVisible(window._inspector_tab_nds_tile_extractor, False)
    map_widget = NdsMapObjectsWidget(window)
    window.nds_map_objects = map_widget
    window._inspector_tab_nds_map_objects = tabs.addTab(map_widget, "Map Objects")
    tabs.setTabVisible(window._inspector_tab_nds_map_objects, False)
    animations = NdsAnimationsWidget(window)
    window.nds_animations = animations
    window._inspector_tab_nds_animations = tabs.addTab(animations, "Animations")
    tabs.setTabVisible(window._inspector_tab_nds_animations, False)


def sync_nds_tile_extractor(window: object, asset: Asset | None) -> None:
    widget = getattr(window, "nds_tile_extractor", None)
    tabs = getattr(window, "preview_inspector_tabs", None)
    index = getattr(window, "_inspector_tab_nds_tile_extractor", None)
    if widget is None or tabs is None or index is None:
        return
    platform_id = getattr(window, "_rom_platform_id", None) or "nds"
    active = bool(asset and asset.magic == "BMD0" and platform_id == "nds")
    tabs.setTabVisible(index, active)
    widget.set_context(asset if active else None)
    map_widget = getattr(window, "nds_map_objects", None)
    map_index = getattr(window, "_inspector_tab_nds_map_objects", None)
    if map_widget is not None and map_index is not None:
        map_active = bool(active and asset and _is_gen5_map_path(asset.virtual_path))
        tabs.setTabVisible(map_index, map_active)
        map_widget.set_context(asset if map_active else None)
    animations = getattr(window, "nds_animations", None)
    animation_index = getattr(window, "_inspector_tab_nds_animations", None)
    if animations is not None and animation_index is not None and not active:
        animations.clear()
        tabs.setTabVisible(animation_index, False)
