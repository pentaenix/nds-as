# RAE community mappings

Shared mapping files for [Retro Asset Extractor](https://github.com/pentaenix/rae) (RAE).

These files contain **metadata only**: paths, labels, source URLs, confidence levels, and relationship hints. They must not contain ROM bytes, extracted images, models, textures, or other copyrighted assets.

## Layout

```text
mappings/
  schema.json       JSON Schema for all mapping files
  nds/              Nintendo DS games (active)
  gba/              Game Boy Advance (planned)
  gbc/              Game Boy Color (planned)
  gb/               Game Boy (planned)
  3ds/              Nintendo 3DS (planned)
```

Each JSON file should include `"platform": "nds"` (or `gba`, `gbc`, `gb`, `3ds`). If omitted, the parent folder name is used.

## Contributing

1. Add or edit a file under the correct `mappings/<platform>/` folder.
2. Cite public sources in `sources[]`.
3. Use confidence values honestly (`verified-design-rule`, `community-known`, `heuristic`, `conflict`).
4. Open a pull request — no personal override folders; everyone shares the same mapping set.

### TODO: in-app mapping editor

We plan a UI workflow to draft mappings interactively (browse a ROM, label archives, export JSON) so contributors do not have to hand-edit large JSON files. Until then, copy an existing seed file as a template.

## Included DS seeds (`mappings/nds/`)

- `generic_nds.json` — signature-based fallback for any Nintendo DS game.
- `pokemon_dp.json`, `pokemon_pt.json`, `pokemon_hgss.json`, `pokemon_bw.json`, `pokemon_bw2.json` — Pokémon DS archive hints from public filelists.

## Legal guardrails

`roms/` and `exports/` stay git-ignored. Mapping files can be shared; ROMs and extracted assets cannot.
