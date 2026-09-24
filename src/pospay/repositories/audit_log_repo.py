# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from collections.abc import Iterator

from pospay.domain.audit_log_entry import AuditLogEntry
from pospay.repositories.base import TenantScopedRepository


class AuditLogRepository(TenantScopedRepository[AuditLogEntry]):
    model = AuditLogEntry

    def iter_in_chain_order(self, batch_size: int = 1000) -> Iterator[AuditLogEntry]:
        """Oldest first, the order the hash chain was built in, in batches by sequence so
        memory stays flat however long the log is. list()/the UI's newest-first view is a
        display concern, not this."""
        after = 0
        while True:
            stmt = self.query().where(AuditLogEntry.sequence > after).order_by(AuditLogEntry.sequence).limit(batch_size)
            batch = list(self.session.execute(stmt).scalars().all())
            yield from batch
            # Walked; let them go, or the session's identity map would hold the whole log.
            for entry in batch:
                self.session.expunge(entry)
            if len(batch) < batch_size:
                return
            after = batch[-1].sequence

    def latest(self) -> AuditLogEntry | None:
        stmt = self.query().order_by(AuditLogEntry.sequence.desc()).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()
