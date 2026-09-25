# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.webauthn_credential import WebauthnCredential
from openclear.repositories.base import TenantScopedRepository


class WebauthnCredentialRepository(TenantScopedRepository[WebauthnCredential]):
    model = WebauthnCredential
