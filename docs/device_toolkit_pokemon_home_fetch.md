# Device Toolkit: Pokémon HOME fetch

Adds a menu item:

```text
Device Toolkit → Mobile → Fetch Pokémon HOME to roms/pokemon_home…
```

The action runs `adb` from the RAE UI, checks for an authorized Android device, pulls Pokémon HOME APK splits, external files/cache, OBB if present, and candidate HOME/Unity files into `roms/pokemon_home`. Existing `roms/pokemon_home` is removed before refetching.

It does not decrypt `.aba/.abap` files, scrape remote servers, or bypass protected assets.
