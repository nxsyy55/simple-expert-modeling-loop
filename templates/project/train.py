"""Baseline candidate interface. Replace the model only with an evidence-backed hypothesis."""
from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


def fit_predict(train_x: pd.DataFrame, train_y, predict_x: pd.DataFrame, model_dir: Path):
    numeric = train_x.select_dtypes(include=["number"]).columns.tolist()
    categorical = [column for column in train_x.columns if column not in numeric]
    transform = ColumnTransformer([
        ("numeric", make_pipeline(SimpleImputer(strategy="median"), StandardScaler()), numeric),
        ("categorical", make_pipeline(SimpleImputer(strategy="most_frequent"),
                                       OneHotEncoder(handle_unknown="ignore")), categorical),
    ])
    model = make_pipeline(transform, LogisticRegression(max_iter=1000, random_state=42))
    model.fit(train_x, train_y)
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_dir / "model.joblib")
    return model.predict_proba(predict_x)[:, 1]


def predict_saved(model_dir: Path, frame: pd.DataFrame):
    model = joblib.load(Path(model_dir) / "model.joblib")
    return model.predict_proba(frame)[:, 1]
