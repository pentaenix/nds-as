# Mobile app ROM bundles

This patch generalizes Android fetches into `.rom` directory bundles.

Use:

```text
Device Toolkit → Mobile → Extract Installed App ROM…
```

RAE writes `roms/<app>.rom`, including `rae_mobile_rom.json`, APK splits, external files/cache, OBB files when present, and candidate Unity/mobile files.

Open the result with the normal File → Open ROM action.

Pokémon HOME remains a special mapping/profile inside the generic mobile backend: if the package id is `jp.pokemon.pokemonhome`, RAE also builds HOME form-package rows and `home_inventory.json`.

No decryption keys, server scraping, runtime hooks, or protected asset bypass are added.
