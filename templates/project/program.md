# Research objective

Describe the binary prediction problem, the metric, and the practical constraints.

## Data and evaluation

- Identify the target and optional row ID in `project.toml`.
- The framework creates one fixed, stratified holdout from the training CSV.
- The research session receives only the training partition. The evaluator owns the holdout.
- Keep candidate changes tied to a hypothesis and compare only results with the same split identity.

## Stopping conditions

State what evidence would support continuing a direction and what would stop it.
