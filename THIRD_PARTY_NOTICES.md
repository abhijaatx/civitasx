# Third-party notices

This file is the release checklist for reused open-source code. It must be
updated whenever a dependency is added, vendored, forked, or copied into the
repository.

| Component | Source URL | Commit/version | License | Files or package used | Local changes | Notice verified |
|---|---|---|---|---|---|---|
| Hermes Agent (Nous Research) | https://github.com/NousResearch/hermes-agent | 01382698fc32ec7740b6a204d9b7a6abeac74d33 | MIT | `services/api/src/civitas_api/hermes_runtime/{budget,events}.py` (adapted from `agent/iteration_budget.py` and `gateway/stream_events.py`); Hermes command names follow the public CLI convention | CivitasX-specific Python packaging, civic tool names, and evidence/action boundaries | Yes (`services/api/src/civitas_api/hermes_runtime/NOTICE-HERMES-MIT.txt`) |

Before distribution:

- Preserve each upstream license and copyright notice.
- Record transitive licenses in the generated SBOM.
- Keep GPL/AGPL/source-available components isolated or obtain an explicit
  legal decision before linking them into the CivitasX application.
- Record model-weight licenses separately from runtime-library licenses.
