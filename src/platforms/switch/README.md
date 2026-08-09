# Nintendo Switch platform island

Reads retail **NSP/XCI** dumps: decrypts NCAs, walks RomFS, unpacks the
Trinity ``data.trpfd`` / ``data.trpfs`` archive, and exports models as GLB.

## Requirements

| File | Purpose |
|------|---------|
| ``prod.keys`` | AES keys from your console (Lockpick_RCM). Place next to the ROM or in ``~/.switch/``. |
| ``oo2core_*.dll`` / ``liboo2core.dylib`` | Oodle decompressor for packed meshes/textures. Set ``RAE_SWITCH_OODLE_DLL`` to its path. |

Without ``prod.keys`` the ROM scans as a single locked row. Without Oodle,
models/textures inside TRPAKs cannot be decoded (metadata-only TRMDL reads work).

## Status

| Layer | State |
|-------|-------|
| NCA decrypt + RomFS | Done |
| TRPFS / TRPFD / TRPAK unpack | Done |
| PokeDocs hash cache (lazy download) | Done — ``roms/sv_hashes_inside_trpak.txt`` |
| Scan: Pokémon models/textures/anims, maps, characters, icons | Done |
| TRMDL + TRMSH + TRMBF + TRSKL → GLB | Done (needs Oodle at runtime) |
| BNTX texture preview | Planned |
| TRANM animation preview | Planned |

## Scan results (Pokémon Scarlet)

Typical first scan after hash cache download:

- ~993 Pokémon ``.trmdl`` models
- ~17k ``.bntx`` textures (Pokémon + maps + characters + icons)
- ~51k ``.tranm`` / ``.gfbanm`` animations (Pokémon)
- 24 loose RomFS files (audio, Trinity headers, movies)

## Island rules

- No imports from other ``platforms/*``.
- UI uses ``PlatformDispatch`` only.
- Tests: ``pytest tests/test_switch_platform.py tests/test_platform_import_isolation.py``.
