# Usage

The core targets Windows, Linux and macOS with Python 3.11. Local execution evidence currently covers Windows only; other operating systems and the real Codex/MCP connection after extraction remain unverified. There is no bundled GUI.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and obtain a Codex CLI with an authenticated account and access to your chosen models. The CLI must be on PATH, or set `CODEX_CLI_PATH` to its executable file. No model credentials are bundled. No minimum compatible CLI version has yet been established: the adapter requires flags including `--no-daemon`, `--ignore-user-config`, `--strict-config`, `--ignore-rules`, feature toggles and stdio MCP configuration. An arbitrary Codex installation is not guaranteed compatible. Unsupported flags require a compatible CLI or adapter changes, not repeated research retries.

From the repository root, use the same commands in PowerShell, bash or zsh:

```text
uv python install 3.11
uv sync --locked
uv run --locked python production_session.py check-host
```

The optional `setup.ps1` wrapper runs these steps plus import checks. It does not install uv or Codex, authenticate, train, or call a model. A bootstrap failure stops the wrapper. A successful check only proves the help command works, not authentication, model availability, all adapter flags or MCP tools. If local cache access is denied, use a project-local `UV_CACHE_DIR`; if linking fails, retry `uv sync --locked --link-mode copy`.

The locked environment includes NumPy, pandas, scikit-learn, joblib and psutil for process cleanup. AutoML/GPU libraries are not included. Install additional dependencies with `uv add PACKAGE` **before** `prepare`; the agent cannot install packages. The lockfile hash is part of the fixed experiment identity. Changing it later requires a fresh experiment copy without old prepared data or runtime.

Configure `project.toml`: raw CSV, binary target, optional ID (`id = ""` disables it), holdout fraction/seed, candidate source and research model/effort. Only `roc_auc` is implemented. The default candidate is `templates/project/train.py`; to customize, copy it into `workspace/train.py` and set `[candidate].source` to that path. Edit the template program and knowledge before the first run; active knowledge is initialized once and thereafter lives in `workspace/runtime/knowledge/`.

```text
uv run --locked python loop_cli.py prepare
uv run --locked python loop_cli.py run --rounds 1
uv run --locked python loop_cli.py status
uv run --locked python loop_cli.py audit
```

Expert cadence is configured with `configure`, not project.toml. Omit configuration for the default N=0, or use `configure --review-every 0` to disable it. Model identifiers must be available in your Codex account. The example is a configuration example, not a guarantee of availability.

`prepare` creates one split, then verifies bytes/config/evaluator/lock identity on subsequent calls. It never replaces an existing domain. If scientific settings change, create an independent project copy with fresh runtime/data preparation. Do not copy old runtime into it.

`run` explicitly authorizes the local baseline and the requested model-driven research. Baseline fits once and costs no research round/model call. If its training receipt survived, recovery verifies it and only finishes evaluation. Unknown partial baseline artifacts block; they are not retrained silently.

`--rounds` is a cumulative accepted research target. Success, duplicate and accepted model failure each consume one round. `continue` receives an existing returned result without replaying model calls or training. An unknown started call blocks. Ctrl+C requests a safe stop after the current research result is evaluated and settled. No background monitoring is installed.

Periodic expert review occurs before the next research once N accepted rounds have passed since the last review. The final target takes precedence: no implicit tail expert is charged. `review` explicitly requests one expert update (or receives its pending returned result). Expert and baseline do not increment research count.

All task paths inside C02 use the task directory as base; output descriptors include `path_base`, `required`, and `exists`. For saved-model export, use the exact successful task:

```text
uv run --locked python predict.py --task workspace/runtime/tasks/luna-REPLACE_WITH_ID --input workspace/data/raw/test.csv --output workspace/submissions/prediction.csv
```

Export verifies the task's saved candidate/model and ordered IDs, never fits, and refuses to overwrite an existing output. The output prediction column is `prediction`; adapt your submission format separately. No upload tool is included.

`workspace/runtime/`, `workspace/baseline/`, environments, caches, raw/prepared data and exports stay local and ignored. The source, dependency lock, templates and docs are intended for Git. Other providers require your own implementation preserving these contracts. `--authorized-full-access` is an explicit host execution option; even the default tool route is not a security sandbox for arbitrary Python.

For an operator-authorized real integration check, inspect the first research result, configure an available expert model, request one explicit `review`, then increase the cumulative research target to 2. Verify the review publication and next task's parent/knowledge projection. These steps make model calls and are not part of dependency installation. A successful fixture is not a substitute for this check.

Process cleanup snapshots the owned descendants with psutil, kills and waits for observed processes, and records failures. It includes descendants in separate sessions but cannot guarantee containment of already orphaned processes or children created during enumeration. Hard crashes may bypass cleanup. Inspect unresolved process evidence before manual recovery; never blindly replay an unknown call.
