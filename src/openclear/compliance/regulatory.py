# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The pages that map OpenClear onto a law, rule or piece of guidance (docs/compliance/), and when
each was last checked against its sources (REGULATORY_PLAN.md RG-D5, RG7). Each page says the same
date in its "Current as of" line; tests/test_compliance/test_regulatory_currency.py keeps the two
in step. The control check `regulatory_currency` warns when one is more than a year old."""

from dataclasses import dataclass
from datetime import date

REVIEW_DAYS = 365


@dataclass(frozen=True)
class Page:
    path: str  # under docs/compliance/
    title: str
    current_as_of: date


PAGES: tuple[Page, ...] = (
    Page("check-law.md", "Check law: UCC 3 and 4, Regulation CC, Check 21, ECCHO", date(2026, 10, 22)),
    Page("ffiec.md", "FFIEC IT Examination Handbook", date(2026, 10, 22)),
    Page("glba.md", "GLBA: Interagency Guidelines (12 CFR 364 App. B)", date(2026, 10, 23)),
    Page("cri-profile.md", "The CRI Profile", date(2026, 10, 22)),
    Page("incident-notification.md", "Incident notification", date(2026, 10, 22)),
    Page("nydfs.md", "New York DFS Part 500", date(2026, 10, 22)),
    Page("state-rules.md", "State rules: Massachusetts and CSBS", date(2026, 10, 22)),
    Page("fincen-ofac.md", "Check fraud, SARs and OFAC", date(2026, 10, 22)),
    Page("nacha.md", "Nacha Operating Rules and Regulation E", date(2026, 10, 22)),
    Page("sr-11-7.md", "Model risk management (SR 11-7)", date(2026, 10, 22)),
    Page("ai-rmf.md", "AI risk management (NIST AI RMF)", date(2026, 10, 22)),
    Page("accessibility.md", "Accessibility", date(2026, 10, 22)),
    Page("cfpb-1033.md", "CFPB Section 1033", date(2026, 10, 22)),
    Page("tprm.md", "Third-party risk management", date(2026, 10, 21)),
    Page("csf.md", "NIST CSF 2.0", date(2026, 10, 22)),
)


def stale(today: date) -> list[Page]:
    """The pages not checked against their sources within REVIEW_DAYS of `today`."""
    return [page for page in PAGES if (today - page.current_as_of).days > REVIEW_DAYS]
