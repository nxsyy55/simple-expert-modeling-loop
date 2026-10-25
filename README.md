# Simple Expert-Guided Modeling Loop

Simple Expert-Guided Modeling Loop is an experimental, supervised framework for serial machine-learning research with the Codex CLI. It keeps execution state, candidate code, training receipts, saved artifacts, evaluation results, and expert knowledge in separate files. It supports one binary-classification task template and an optional file-only expert review. Here, "expert" means a separate review agent, not a claim of human-expert performance.

The framework grew out of modeling experiments that did not meet their research goals, and further research investment was stopped. A small number of successful training/evaluation runs did not demonstrate a research gain or reliable autonomous operation. This is a practical example, not evidence that every AI research loop is ineffective. Expect to supervise runs and debug candidate code.

## Setup

1. Put a binary-labeled `train.csv` at `workspace/data/raw/train.csv`. Configure the target and optional ID in `project.toml`.
2. Run `./setup.ps1` in PowerShell. It prepares Python 3.11 with `uv`, installs the locked dependencies, and checks Codex CLI availability. It does not fit, call a model, or alter project knowledge.
3. Run `uv run python loop_cli.py prepare` to create or verify the fixed stratified holdout. Existing split files are checked by content hash and are never silently replaced.
4. Run `uv run python loop_cli.py run --rounds 10` to train the shipped baseline once and then start serial research. This command may call Codex. The baseline is idempotent: a saved receipt is verified and recovered, never retrained.

`status` and `audit` are read-only. `continue` resumes the saved task without replaying an unknown Codex call. `--rounds` is a cumulative accepted-round target. `review` explicitly requests one expert update. Periodic reviews require a configured expert model and a positive cadence, for example:

```powershell
uv run python loop_cli.py configure --expert-model gpt-6.1-sol --review-every 5
uv run python loop_cli.py run --rounds 10
```

The default research model is `gpt-6-luna`; research model names and reasoning effort are configurable in `project.toml`; expert cadence is configured through `loop_cli.py configure`. Other providers are not included.

## Outputs

Runtime state and task packets are under `workspace/runtime/`. Prepared data is under `workspace/data/prepared/`. Candidate training artifacts and receipts stay with each task. Formal evaluation runs in a separate process and loads the saved candidate through its `predict_saved` entry point. Predictions can be exported with the project template in `templates/project/predict.py`; prediction export never trains.

The default Codex route uses a read-only Codex sandbox plus a task-local JSON/argv tool transport. `--authorized-full-access` selects the host full-access route and should be used only when the operator has already authorized it. A CLI permission setting is not an OS isolation boundary.

## Scope and license

This release is limited to binary tabular classification, a fixed stratified holdout, one candidate training command per research task, and file-based expert updates. It does not promise autonomous discovery, reliable unattended operation, or general support for other task types. It has not been validated through a newly run end-to-end Codex research/expert cycle.

Released under the [MIT License](LICENSE).

## Actual flow and verification

Start with [the offline flow diagram](docs/flow.html) (download and open locally), [files/fields/contracts and failure boundaries](docs/file-flow.md), [source function map](docs/source-map.md), and [verification record](docs/validation.md). Detailed commands are in [usage](docs/usage.md).

This is a root-script project, not a packaged SDK: controller/backend/contracts/storage govern execution; transport and worker scripts are separate subprocess entries; templates supply candidate/knowledge/tools. There is intentionally no public test suite, GUI, provider abstraction, or experiment archive. The structure is coherent for this narrow workflow, but contracts span multiple modules and maintenance still requires integration checks. Do not describe it as a mature general research platform.

Publication preparation is recorded in [PUBLISH_CHECKLIST.md](PUBLISH_CHECKLIST.md).
