# Extending a project

Set the target, row ID (empty string disables IDs), data paths, split fraction, and seed in `project.toml`. The metric is fixed to roc_auc; a different metric needs evaluator and contract changes. Provide a candidate module with:

- `fit_predict(train_x, train_y, predict_x, model_dir)`, which fits and writes its saved model to `model_dir`;
- `predict_saved(model_dir, frame)`, which loads only those saved artifacts and returns one finite score per row.

The worker calls `fit_predict` once per research task, enforces a wall-clock budget, and compares its predictions with a fresh-process reload. Do not hide multiple parameter-search or cross-validation fits inside the candidate. The evaluator imports the saved-prediction entry point in a separate process and checks actual artifact hashes before and after prediction.

Inspection helpers belong in `workspace/scripts/` and are called with JSON argument arrays. Fitting must go through the training tool. Other model providers are outside this release; users who add one must preserve the frozen task and receipt contracts.
