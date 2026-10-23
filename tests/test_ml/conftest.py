# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import pytest

from openclear.config import get_settings


@pytest.fixture(autouse=True)
def _small_banks_can_train(monkeypatch):
    """These tests train on a dozen decisions; the 200-decision floor before a model is activated
    (ml_bank_model_min_decisions) is tested on its own, with the real figure."""
    monkeypatch.setattr(get_settings(), "ml_bank_model_min_decisions", 10)
