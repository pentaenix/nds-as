# Nintendo DS platform (active)

This module contains everything RAE needs for `.nds` ROMs today:

- `rom.py` — ROM filesystem (FNT/FAT)
- `scanner.py` — asset index (NARC, LZ10, Nitro carving)
- `nitro/` — BTX0/TEX0 texture decode
- `nitro_models.py`, `nitro_2d.py`, `audio.py` — models, 2D tiles, SDAT audio
- `exporter.py` — readable export + apicula GLB conversion

Community path labels live in `mappings/nds/`.
