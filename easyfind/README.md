# easyfind/

EasyFind index files for Nintendo DS games, keyed by ROM header **game code**.

Each retail DS build has a stable 4-character product code in the ROM header (for example `IRBO`, `IPKE`). RAE stores files here:

```text
easyfind/IRBO.easyfind
easyfind/IPKE.easyfind
```

## How RAE uses this folder

- EasyFind files are keyed by Nintendo DS ROM header game code.
- RAE automatically looks for `easyfind/<GAME_CODE>.easyfind` when a ROM or session is loaded.
- Before using an EasyFind file, RAE validates its structure and schema.
- If a file is missing or invalid, the user can rebuild it from their loaded ROM.

Opening a ROM resolves the matching file automatically, regardless of the ROM filename on disk.

## Local only

Built `.easyfind` files can be large (previews, color signatures, layout data). They stay **gitignored** — build them locally from your own ROM. Temporary build artifacts (`*.tmp`, `*.easyfind.tmp`) are ignored as well.
