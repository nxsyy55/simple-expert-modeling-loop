# Usage

Windows, Python 3.11 and `uv` are the initial supported setup. `setup.ps1` installs the locked NumPy/pandas/scikit-learn/joblib environment and checks the local Codex CLI bootstrap. It does not fit, call a model, log in, or start monitoring. If uv is absent, follow the official installation link printed by the script. There is no bundled GUI.

Configure `project.toml`: raw CSV, binary target, optional ID, holdout fraction/seed, candidate source and research model/effort. Only `roc_auc` is implemented. The default candidate is `templates/project/train.py`; to customize, copy it into `workspace/train.py` and set `[candidate].source` to that path. Edit the template program and knowledge before the first run; active knowledge is initialized once and thereafter lives in `workspace/runtime/knowledge/`.

```powershell
.\setup.ps1
uv run --locked python loop_cli.py prepare
uv run --locked python loop_cli.py configure --expert-model gpt-6.1-sol --review-every 5
uv run --locked python loop_cli.py run --rounds 10
uv run --locked python loop_cli.py status
uv run --locked python loop_cli.py audit
```

Expert cadence is configured with `configure`, not project.toml. Omit configuration for the default N=0, or use `configure --review-every 0` to disable it. Model identifiers must be available in your Codex account. The example is a configuration example, not a guarantee of availability.

`prepare` creates one split, then verifies bytes/config/evaluator/lock identity on subsequent calls. It never replaces an existing domain. If scientific settings change, create an independent project copy with fresh runtime/data preparation. Do not copy old runtime into it.

`run` explicitly authorizes the local baseline and the requested model-driven research. Baseline fits once and costs no research round/model call. If its training receipt survived, recovery verifies it and only finishes evaluation. Unknown partial baseline artifacts block; they are not retrained silently.

`--rounds` is a cumulative accepted research target. Success, duplicate and accepted model failure each consume one round. `continue` receives an existing returned result without replaying model calls or training. An unknown started call blocks. Ctrl+C requests a safe stop after the current research result is evaluated and settled. No background monitoring is installed.

Periodic expert review occurs before the next research once N accepted rounds have passed since the last review. The final target takes precedence: no implicit tail expert is charged. `review` explicitly requests one expert update (or receives its pending returned result). Expert and baseline do not increment research count.

All task paths inside C02 use the task directory as base; output descriptors include `path_base`, `required`, and `exists`. For saved-model export, use the exact successful task:

```powershell
uv run --locked python predict.py --task workspace/runtime/tasks/luna-REPLACE_WITH_ID --input workspace/data/raw/test.csv --output workspace/submissions/prediction.csv
```

Export verifies the task's saved candidate/model and ordered IDs, never fits, and refuses to overwrite an existing output. The output prediction column is `prediction`; adapt your submission format separately. No upload tool is included.

`workspace/runtime/`, `workspace/baseline/`, environments, caches, raw/prepared data and exports stay local and ignored. The source, dependency lock, templates and docs are intended for Git. Other providers require your own implementation preserving these contracts. `--authorized-full-access` is an explicit host execution option; even the default tool route is not a security sandbox for arbitrary Python.
