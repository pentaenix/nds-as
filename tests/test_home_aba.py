from __future__ import annotations

import json
from pathlib import Path

import pytest

from rae.platforms.home.aba import decrypt_aba_bytes, probe_aba_decrypt
from rae.platforms.home.aba_preview import materialize_aba_source, related_home_aba_paths
from rae.platforms.home.ids import parse_home_cap_id, parse_home_mitake_preview_id


def test_home_cap_and_mitake_ids():
    cap = parse_home_cap_id("cap0054_f00_s0_128.aba")
    assert cap is not None
    assert cap.number == 54
    mitake = parse_home_mitake_preview_id("mt_pv_ev_0054_00_00.aba")
    assert mitake is not None
    assert mitake.number == 54


@pytest.mark.skipif(
    not Path("rae/.cache/mobile-rom/pokemon_home/external_files/files/tyranitar/mt_pv_ev_0054_00_00.aba").exists()
    and not Path(".cache/mobile-rom/pokemon_home/external_files/files/tyranitar/mt_pv_ev_0054_00_00.aba").exists(),
    reason="local HOME cache sample not available",
)
def test_aba_decrypt_changes_header_but_preserves_tail():
    candidates = [
        Path("rae/.cache/mobile-rom/pokemon_home/external_files/files/tyranitar/mt_pv_ev_0054_00_00.aba"),
        Path(".cache/mobile-rom/pokemon_home/external_files/files/tyranitar/mt_pv_ev_0054_00_00.aba"),
    ]
    source = next(path for path in candidates if path.exists())
    raw = source.read_bytes()
    dec = decrypt_aba_bytes(raw)
    assert dec[:32] != raw[:32]
    assert dec[1024:] == raw[1024:]
    probe = probe_aba_decrypt(source)
    assert probe["ok"] is True


@pytest.mark.skipif(
    not Path(".cache/mobile-rom/pokemon_home/external_files/files/tyranitar/mt_pv_ev_0054_00_00.aba").exists(),
    reason="local HOME cache sample not available",
)
def test_related_home_aba_paths_groups_species_siblings():
    base = Path(".cache/mobile-rom/pokemon_home/external_files/files/tyranitar/mt_pv_ev_0054_00_00.aba")
    mobile_root = Path(".cache/mobile-rom/pokemon_home")
    related = related_home_aba_paths(base, mobile_root=mobile_root)
    names = {path.name for path in related}
    assert "mt_pv_ev_0054_00_00.aba" in names
    assert "cap0054_f00_s0_128.aba" in names


@pytest.mark.skipif(
    not Path(".cache/mobile-rom/pokemon_home/apk/base.apk").exists(),
    reason="local HOME apk sample not available",
)
def test_materialize_aba_from_apk_member():
    apk = Path(".cache/mobile-rom/pokemon_home/apk/base.apk").resolve()
    mobile_root = Path(".cache/mobile-rom/pokemon_home").resolve()

    class _Asset:
        virtual_path = "mobile/pokemon_home/base.apk!/assets/AB/cap0004_f00_s0.aba"
        data = json.dumps(
            {
                "container": str(apk),
                "virtual_path": virtual_path,
            }
        ).encode("utf-8")
        mobileRom = {"root": str(mobile_root)}

    path = materialize_aba_source(_Asset())
    assert path is not None
    assert path.is_file()
    assert path.name == "cap0004_f00_s0.aba"
    assert path.stat().st_size > 1000
