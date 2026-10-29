# Simple Expert-Guided Modeling Loop maintenance notes

- Use `uv` for dependency changes; update `uv.lock` with the project-local environment.
- Keep runtime files, raw data, prepared data, predictions, credentials, and local checks out of Git.
- Keep `loop_cli.py` as the public entry point and preserve the root-script layout.
- Keep candidate training code separate from the evaluator. Evaluation loads saved artifacts and verifies their byte hashes.
- Do not add providers, GUI layers, automated monitoring, or experiment-management services without a new user decision.
- Do not claim that local synthetic checks establish research effectiveness or autonomous reliability.

## Report publication

GitHub Pages serves main:/docs. Edit docs/report/report.md and run npm --prefix tools/report install then npm --prefix tools/report run build. Commit generated HTML with its source. Keep working-draft status until author approval. Only the article, figures, and compact numerical summaries are authorized for this publication; full historical reports, raw call records, conversation reconstructions, and model/data archives remain local.
