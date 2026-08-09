# Apply this patch

From the repo root:

```bash
unzip /path/to/rae-home-mobile-assets-patch.zip -d /tmp/rae-home-mobile-assets-patch
python3 /tmp/rae-home-mobile-assets-patch/apply_rae_home_mobile_assets_patch.py .
python -m compileall src
```

Then try:

```bash
./rae home scan /path/to/copied/pokemonhome/source --out exports/home_inventory.json
./rae home list /path/to/copied/pokemonhome/source
./rae run
```

Optional for object-level Unity inventory:

```bash
.venv/bin/python -m pip install UnityPy
```

This patch intentionally does not decrypt `.aba/.abap` packages. It detects them and asks for readable local cache/bundle files.
