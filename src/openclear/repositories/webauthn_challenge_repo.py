# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.webauthn_challenge import WebauthnChallenge
from openclear.repositories.base import TenantScopedRepository


class WebauthnChallengeRepository(TenantScopedRepository[WebauthnChallenge]):
    model = WebauthnChallenge
