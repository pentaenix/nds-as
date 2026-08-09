# RAE Cursor rules (nested copy)

Canonical agent rules for this monorepo live at the **repository root**:

```
title_screen_demo/
  .cursor/rules/rae-*.mdc          # platform-isolation, parallel-agents, shared-glb-policy, viewport-isolation, …
  .cursor/skills/rae-platform-islands/
  AGENTS.md                        # monorepo router
  rae/AGENTS.md                    # RAE entry
  rae/docs/agents/platform-isolation-contract.md   # hard rules
  rae/docs/agents/platform-islands.md
```

This folder (`rae/.cursor/rules/`) keeps a file-triggered reminder when working inside `rae/`. Prefer editing rules at the repo root so all subprojects share one `.cursor` tree.
