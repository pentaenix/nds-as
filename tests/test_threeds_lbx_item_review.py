from __future__ import annotations

from dataclasses import dataclass

import pytest

from rae.platforms.threeds.lbx_item_review import (
    item_export_job,
    item_category,
    normalized_item_title,
    item_model_paths,
    proposed_item_title,
    sanitize_submission_title,
)


pytestmark = pytest.mark.threeds


@dataclass
class Entry:
    path: str


def test_item_catalog_includes_only_bcmdl_models_and_naturally_sorts() -> None:
    entries = [
        Entry("/3ddata/item/etc073_20.bcmdl"),
        Entry("/3ddata/item/etc073_10.bcmdl"),
        Entry("/3ddata/item/etc073_10.bctex"),
        Entry("/3ddata/wpn/etc001_00.bcmdl"),
    ]

    assert item_model_paths(entries) == [
        "/3ddata/item/etc073_10.bcmdl",
        "/3ddata/item/etc073_20.bcmdl",
    ]


def test_item_names_are_neutral_and_submission_safe() -> None:
    assert proposed_item_title("/3ddata/item/etc073_10.bcmdl") == "Item 073 Variant 10"
    assert proposed_item_title("/3ddata/item/etc090_00_L_tr.bcmdl") == "Item 090 Variant 00 L TR"
    assert sanitize_submission_title('Desk: Office / Large') == "Desk Office Large"


def test_reviewed_items_export_to_the_lbx_item_props_category() -> None:
    job = item_export_job("/3ddata/item/etc073_10.bcmdl", "Control Panel")

    assert job.category == ("Items", "Props")
    assert job.title == "Control Panel"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Phone 3", ("Items", "Phones")),
        ("NPC 12", ("Items", "NPCs")),
        ("Arena 2", ("Items", "Arenas")),
        ("Achilles Full", ("Items", "Full Robots")),
        ("Car 3 Sepia", ("Items", "Vehicles")),
        ("Traffic", ("Items", "Vehicles")),
        ("Rocket", ("Items", "Vehicles")),
        ("Fighter plane", ("Items", "Vehicles")),
        ("Bulldozer Body", ("Parts", "Body")),
        ("Bulldozer Left Arm", ("Parts", "Left Arm")),
        ("Bulldozer Yellow", ("Items", "Props")),
    ],
)
def test_reviewed_item_categories(title: str, expected: tuple[str, ...]) -> None:
    assert item_category(title) == expected


def test_item_renames_are_applied_before_categorization() -> None:
    assert normalized_item_title("Arena 15") == "Arena 8"
    job = item_export_job("/3ddata/item/etc068_00.bcmdl", "Bulldozer 1")
    assert job.title == "Bulldozer Full"
    assert job.category == ("Items", "Full Robots")
