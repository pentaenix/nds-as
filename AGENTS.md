# RAE repository guidance

Before changing RAE, read:

1. [`docs/development/platform-isolation.md`](docs/development/platform-isolation.md)
2. [`docs/development/platform-architecture.md`](docs/development/platform-architecture.md)
3. Any repository rule in `.cursor/rules/` that matches the files being edited

Keep console-specific scanning, decoding, GLB processing, preview policy, and
export behavior inside `src/platforms/<id>/`. Platform modules must not import
one another unless the boundary allow-list explicitly permits it. Shared UI
code reaches platforms through `PlatformDispatch`.

Run the tests for the platform you changed together with:

```bash
.venv/bin/python -m pytest \
  tests/test_platform_import_isolation.py \
  tests/test_platform_isolation_contract.py
```
