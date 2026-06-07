# NDS-AS mappings

Community mapping seed files for [nds-as](https://github.com/pentaenix/nds-as).

These files contain metadata only: paths, labels, source URLs, confidence levels, and relationship hints. They must not contain ROM bytes, extracted images, models, textures, or other copyrighted assets.

## Included seeds

- `generic_nds.json` — signature-based fallback for any Nintendo DS game.
- `pokemon_dp.json` — Pokémon Diamond / Pearl, based on public internal filelists.
- `pokemon_pt.json` — Pokémon Platinum, based on public internal filelists.
- `pokemon_hgss.json` — Pokémon HeartGold / SoulSilver, based on public NARC tables.
- `pokemon_bw.json` — Pokémon Black / White, deliberately partial; public BW1 archive maps are incomplete and conflicting.
- `pokemon_bw2.json` — Pokémon Black 2 / White 2, based on public B2W2 NARC lists.

## Confidence values

- `verified-design-rule` — true by NDS-AS/format design, not a ROM-specific claim.
- `community-known` — from a cited public filelist/tool reference.
- `heuristic` — useful hint, but NDS-AS must verify via file signatures and counts.
- `conflict` — public notes disagree or are too ambiguous; NDS-AS should show this in UI.

## Required legal guardrails

`roms/`, `exports/`, and `mapping_overrides/` should stay git-ignored. Mapping files can be shared; ROMs and extracted assets cannot.
