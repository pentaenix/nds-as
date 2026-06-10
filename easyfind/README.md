# easyfind/

Canonical EasyFind index files for Nintendo DS games, keyed by ROM header **game code**.

Each retail DS build has a stable 4-character product code in the ROM header (for example `IRBO`, `IPKE`). RAE names files here:

```text
easyfind/IRBO.easyfind
easyfind/IPKE.easyfind
```

Opening a ROM resolves the matching file automatically, regardless of the ROM filename on disk.

Built `.easyfind` files are local artifacts and are gitignored. This folder stays in the repo so paths are predictable.
