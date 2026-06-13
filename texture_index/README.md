# texture_index/

On-disk texture dictionary indexes for Nintendo DS games, keyed by ROM header **game code**.

Each retail DS build has a stable 4-character product code in the ROM header (for example `IRBO`, `IPKE`). RAE stores one cache file per code:

```text
texture_index/IRBO.texture-index
texture_index/IPKE.texture-index
```

## How RAE uses this folder

- Cache files are keyed by Nintendo DS ROM header game code, not the filename on disk.
- RAE builds or loads `texture_index/<GAME_CODE>.texture-index` after scanning a ROM.
- A cache is invalidated when the ROM content hash, scan mode, asset fingerprint, or parser version changes.
- These files are local build artifacts and stay gitignored.

Reopening the same ROM skips re-parsing thousands of BTX0/BMD0 manifests when the cache is still valid.
