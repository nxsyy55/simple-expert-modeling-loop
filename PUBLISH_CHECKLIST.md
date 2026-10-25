# Publication checklist

Release name: **Simple Expert-Guided Modeling Loop** (`simple-expert-modeling-loop`). License: MIT. Initial public snapshot: 2026-10-25.

- [x] Export only the reviewed source/template/documentation allowlist into a new repository.
- [x] Start with clean Git history; exclude previous development and experiment history.
- [x] Remove old project identifiers, local absolute paths, private contact details, and internal handoff references.
- [x] Scan publishable text for common credential/token formats, private-key blocks, literal credential assignments, and private email addresses. No matching sensitive content was found; this is a scoped scan, not a universal proof that secrets cannot exist.
- [x] Exclude virtual environments, caches, runtime state, data, model artifacts, predictions, submissions, development tests, and local audit fixtures.
- [x] Use a public GitHub noreply identity for the initial commit.
- [x] Include the MIT license, dependency declaration, locked versions, setup script, and knowledge/tool examples.
- [x] Recheck lock consistency, Python syntax and relative documentation links after naming changes.
- [x] For the initial snapshot, retain the verified core Python source unchanged; record subsequent code changes and their checks in `docs/validation.md`.
- [x] State that a real new Codex research/expert cycle and research effectiveness have not been validated.

The owner explicitly authorized creation of a new public repository and publication of this reviewed snapshot. Local audit details remain excluded. A future release should repeat the content scan and check the staged Git tree rather than relying only on `.gitignore`.

Portability follow-up: shared uv setup, explicit external CLI prerequisites, process-tree cleanup and its limits are documented. Linux/macOS and real model integration remain unverified.
