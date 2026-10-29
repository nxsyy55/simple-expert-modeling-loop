# Numerical appendix

These compact extracts support the report's tables and charts. They contain counts, statuses and scores, not raw model-call text, session identifiers, private correspondence, datasets, trained models, or the complete historical evidence archive. They are historical summaries, not a fresh rerun or independent verification of the experiments.

- [Stage counts and maxima](stages.json): 306 entries across different evaluation protocols. Do not compare maxima across stages as a continuous curve or treat entries as independent research ideas.
- [Controller progress](controller-progress.csv): 133 formal slots. `success` means accepted scoring, not improvement. A failed slot may later have a separately documented recovery; its original status remains unchanged.
- [Public scores](public-scores.csv): 90 historical official submission scores ordered by date and submission ID in the retained source. Identifiers and descriptions are omitted. No current leaderboard or final private-score claim is made.
- [OOF comparisons](oof-comparisons.csv): four paired groups sharing the same holdout and some models. Differences are in AUC units, not percentage points. Construction-time ratios include saving/reloading work. Tiny learned-versus-fixed differences are not significance claims.

The article's numbered source notes describe reports retained by the author. Their full text, source captures and raw call records are not part of this public draft. Readers can inspect these numerical summaries and report calculations, but cannot reproduce every historical model from this package. The code release is an extracted framework, not the complete evolving experimental setup.
