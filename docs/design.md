# Structure and design

The released directory uses root-level scripts. It is deliberately not a generic SDK or a provider/plugin framework. Separate worker files are subprocess boundaries, not an additional orchestration layer. There is no GUI or shipped development test suite.

```text
simple-expert-modeling-loop/
  README.md  LICENSE  AGENTS.md  .gitignore
  .python-version  pyproject.toml  uv.lock  setup.ps1
  project.toml  project_config.py
  loop_cli.py  loop_controller.py  loop_backend.py
  loop_contracts.py  loop_storage.py  expert_review.py
  production_session.py  research_transport.py
  session_train.py  candidate_worker.py  inspection_worker.py
  prepare.py  evaluate.py  predict.py
  templates/project/
    project.toml  program.md  train.py
    prepare.py  evaluate.py  predict.py      # thin entry examples
    knowledge/research.md
    tools/README.md  tools/example.py
  workspace/                               # user inputs/output, ignored
    data/raw/.gitkeep
    data/prepared/.gitkeep
    submissions/.gitkeep
    baseline/                              # created by explicit run
    runtime/                               # created by configure/run
      control.json
      knowledge/research.md  accumulated.json
      inputs/history.json  objects/<sha>.txt
      tasks/<task_id>/input.json  feedback.json  delta.json
        workspace/train.py  history/  data/  output/  training/
        publication/                       # expert tasks only
  docs/
    design.md  usage.md  extension.md  research.md
    file-flow.md  framework-flow.mmd  flow.html
    source-map.md  validation.md
```

Only the `.gitkeep` files in workspace are shipped. The depicted runtime/baseline tree is generated at execution. Models remain under each task's `workspace/training/artifacts`, formal predictions under `workspace/output/evaluation`, and exported CSV files under submissions. There is no second models directory disconnected from the actual runtime.

`loop_controller.py` owns control state and scheduling. Backend prepares packets and receives model deliveries. Storage freezes evidence; contracts reconcile C01/C02/C03. Training and prediction execute in child processes; Eva owns the fixed holdout and only calls saved prediction. Expert reviews write two staged files, which the publisher validates and applies through a recoverable manifest.

This layout is appropriate for a narrow script-based workflow, but it is not proof of engineering maturity: the same identity fields cross several modules, and a change must update all producers and consumers. The remaining failure boundaries are explicitly listed in [file-flow.md](file-flow.md), with actual function lines in [source-map.md](source-map.md).

MCP file permissions limit the intended agent interface. Arbitrary candidate or inspection Python is not OS-isolated; the project is intended for supervised trusted code. Having complete history and parent references does not guarantee efficient search or scientific judgment.
