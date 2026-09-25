# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from typing import Any, Protocol

import numpy as np


class ScoringModel(Protocol):
    def fit(self, X: list[dict[str, Any]], y: list[int]) -> None: ...
    def predict_proba(self, X: list[dict[str, Any]]) -> np.ndarray: ...
    def feature_importance(self) -> dict[str, float]: ...


class LogisticRegressionScoringModel:
    """Default ScoringModel: logistic regression over a DictVectorizer-encoded feature
    map. Chosen deliberately over a fancier model (GBM etc.) for v1 — in a fraud-adjacent
    product, an analyst asking 'why was this flagged' needs an answer a coefficient can
    give directly; that explainability is a real product requirement here, not a nicety.
    GBM stays available behind this same ScoringModel protocol for later."""

    def __init__(self) -> None:
        from sklearn.feature_extraction import DictVectorizer
        from sklearn.linear_model import LogisticRegression

        self._vectorizer = DictVectorizer(sparse=False)
        self._model = LogisticRegression(max_iter=1000)

    def fit(self, X: list[dict[str, Any]], y: list[int]) -> None:
        X_vec = self._vectorizer.fit_transform(X)
        self._model.fit(X_vec, y)

    def predict_proba(self, X: list[dict[str, Any]]) -> np.ndarray:
        X_vec = self._vectorizer.transform(X)
        return self._model.predict_proba(X_vec)[:, 1]  # P(outcome == pay / "legitimate")

    def feature_importance(self) -> dict[str, float]:
        names = self._vectorizer.get_feature_names_out()
        coefficients = self._model.coef_[0]
        return dict(sorted(zip(names, (float(c) for c in coefficients)), key=lambda kv: -abs(kv[1])))
