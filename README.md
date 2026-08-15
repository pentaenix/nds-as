# RAE

RAE (Retro Asset Extractor) is a local desktop application and command-line
tool for inspecting game archives and converting supported assets into standard
formats. It is intended for research, preservation, and development workflows
using game data that you have obtained legally.

RAE does not download game images, include encryption keys, or distribute
extracted assets.

## Features

- Browse files and archives without unpacking an entire image first.
- Identify models, textures, animations, maps, sprites, and audio by format.
- Preview supported 2D and 3D assets in the desktop application.
- Resolve textures stored separately from their models.
- Export images as PNG and 3D scenes as self-contained GLB or COLLADA packages.
- Preserve available material, hierarchy, animation, and placement metadata.
- Save scan results and reuse conversion caches between sessions.

Support is format- and game-dependent. The active platform modules are Nintendo
DS, Nintendo 3DS, Nintendo Switch, Windows CD/ISO, and mobile application
archives. Game Boy, Game Boy Color, and Game Boy Advance modules are currently
placeholders.

## Installation

RAE requires Python 3.10 or newer.

On macOS or Linux:

```bash
./rae install
./rae run
```

On Windows:

```bat
rae.bat install
rae.bat run
```

The installer creates a virtual environment and installs the Python
dependencies. Some formats require optional external tools; RAE reports the
missing dependency when a related feature is used.

## Getting started

1. Place a legally obtained game image in `roms/`, or open it from another
   local directory.
2. Start RAE with `./rae run`.
3. Open the image and browse the detected folders or filter the asset list by
   format.
4. Select an asset to inspect its metadata and available previews.
5. Use **Export Selected** to choose a supported output format.

Generated files are written beneath `exports/` by default. ROMs, exports,
caches, and saved sessions are excluded from version control.

## Command line

The CLI provides the same basic scan and conversion pipeline for automated
workflows:

```bash
./rae info roms/game.nds
./rae list roms/game.nds --query BMD0
./rae decode roms/game.nds --out exports/textures --query BTX0
./rae convert roms/game.nds --out exports/models --query BMD0 --format glb
./rae audio roms/game.nds --out exports/audio
./rae mappings
```

Run `./rae --help` or `./rae <command> --help` for the current options.

## Optional model conversion tools

Nintendo DS model conversion uses the vendored `apicula` source under
`tools/apicula/`. `./rae install` builds it when a Rust toolchain is available.
RAE also checks `PATH`, `RAE_APICULA`, and the vendored release path.

Three-dimensional previews use Qt WebEngine and three.js. Install the PySide6
Add-ons package if WebEngine is unavailable. Assimp and platform-specific
decoders are used by selected conversion pipelines.

## Repository layout

```text
src/
  core/         shared data types, registry, and platform dispatch
  platforms/    format scanners, decoders, preview policy, and exporters
  ui/           Qt desktop application
  cli/          command-line interface
mappings/       game and platform metadata
docs/           architecture and format notes
scripts/        maintenance and batch utilities
tools/          optional conversion tools
roms/           local game images; ignored by Git
exports/        generated files; ignored by Git
```

Platform-specific parsing and rendering remain inside
`src/platforms/<platform>/`. See the [development documentation](docs/README.md)
before changing a platform module.

## Development

Install the project, then run the relevant platform tests and the import
boundary checks:

```bash
.venv/bin/python -m pytest -m nds
.venv/bin/python -m pytest \
  tests/test_platform_import_isolation.py \
  tests/test_platform_isolation_contract.py
```

The Python package is named `rae`. The older `dsm` import path remains as a
deprecated compatibility alias.

## Legal notice

Use RAE only with files you are authorized to inspect. Contributors should not
commit ROMs, keys, decrypted commercial data, or extracted copyrighted assets.
