# Simple Expert-Guided Modeling Loop maintenance notes

- Use `uv` for dependency changes; update `uv.lock` with the project-local environment.
- Keep runtime files, raw data, prepared data, predictions, credentials, and local checks out of Git.
- Keep `loop_cli.py` as the public entry point and preserve the root-script layout.
- Keep candidate training code separate from the evaluator. Evaluation loads saved artifacts and verifies their byte hashes.
- Do not add providers, GUI layers, automated monitoring, or experiment-management services without a new user decision.
- Do not claim that local synthetic checks establish research effectiveness or autonomous reliability.
