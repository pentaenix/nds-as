# Test plan

1. Apply the patch.
2. Run:

   ```bash
   python -m compileall src
   ```

3. Create a tiny fake HOME folder:

   ```bash
   mkdir -p /tmp/home_test/Models/Android/Mitake15sv/pokemons
   printf 'UnityFS\0fake' > /tmp/home_test/Models/Android/Mitake15sv/pokemons/pm0054_00_00
   ```

4. Run:

   ```bash
   ./rae home scan /tmp/home_test --out /tmp/home_inventory.json
   ./rae home list /tmp/home_test
   ./rae home report /tmp/home_inventory.json
   ```

5. Expected:

   - `pm0054_00_00` is grouped as Psyduck.
   - The file is classified as a readable Unity candidate by magic.
   - Without UnityPy, object-level inventory reports that UnityPy is missing.

6. Optional object test:

   ```bash
   .venv/bin/python -m pip install UnityPy
   ./rae home scan /path/to/readable/home/cache --out exports/home_inventory.json
   ```

7. UI smoke:

   - Run `./rae run`.
   - Use File → Open Pokémon HOME Source…
   - Pick a folder.
   - Package rows appear with `HOME` magic.
   - Selecting a package shows model/texture/rig/animation status in Details.
