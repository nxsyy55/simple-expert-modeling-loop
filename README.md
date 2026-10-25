# Simple Expert-Guided Modeling Loop

Simple Expert-Guided Modeling Loop is an experimental, supervised framework for serial machine-learning research with the Codex CLI. It keeps execution state, candidate code, training receipts, saved artifacts, evaluation results, and expert knowledge in separate files. It supports one binary-classification task template and an optional file-only expert review. Here, "expert" means a separate review agent, not a claim of human-expert performance.

The framework grew out of modeling experiments that did not meet their research goals, and further research investment was stopped. A small number of successful training/evaluation runs did not demonstrate a research gain or reliable autonomous operation. This is a practical example, not evidence that every AI research loop is ineffective. Expect to supervise runs and debug candidate code.

## Setup

Install `uv` and a compatible, authenticated Codex CLI first. Run these commands from the repository root in PowerShell, bash, or zsh:

```text
uv python install 3.11
uv sync --locked
uv run --locked python production_session.py check-host
```

`setup.ps1` is an optional PowerShell wrapper, not a required Windows dependency. The Python core targets Windows, Linux and macOS; only Windows has local execution evidence. Linux/macOS and a real post-extraction Codex/MCP cycle remain unverified. `check-host` checks CLI bootstrap only, not login, model access or MCP execution. See [prerequisites and compatibility](docs/usage.md).

Before preparing data, add any required ML dependencies with `uv add PACKAGE`, configure `project.toml`, and fill in `templates/project/program.md` and `templates/project/knowledge/research.md`. Put your binary-labeled CSV at `workspace/data/raw/train.csv`. Set the target and optional ID (`id = ""` disables IDs). Then:

```text
uv run --locked python loop_cli.py prepare
uv run --locked python loop_cli.py run --rounds 1
uv run --locked python loop_cli.py status
uv run --locked python loop_cli.py audit
```

`run` trains the baseline once and may make a real model call. Inspect the accepted result before increasing the cumulative target. A model-failed round also consumes the target; completion alone does not demonstrate a working modeling cycle.

`status` and `audit` are read-only. `continue` resumes the saved task without replaying an unknown Codex call. `--rounds` is a cumulative accepted-round target. `review` explicitly requests one expert update. Periodic reviews require a configured expert model and a positive cadence, for example:

```text
uv run --locked python loop_cli.py configure --expert-model gpt-6.1-sol --review-every 5
uv run --locked python loop_cli.py run --rounds 10
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
