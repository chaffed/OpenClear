# AI risk management (NIST AI RMF)

*Current as of 2026-10-22. Checked against its sources at least once a year (control check `regulatory_currency`).*

OpenClear has one AI system: each bank's own fraud-scoring model, a logistic regression trained
only on the decisions the bank's people made. It's off until the bank turns it on, and its score
is advice: a person always decides. This page maps it onto the NIST AI Risk Management Framework
(AI 100-1, January 2023), which is voluntary. [Model risk management](sr-11-7.md) covers
validation in more depth. It isn't legal advice ([Compliance](README.md)).

NIST's Generative AI Profile (AI 600-1, July 2024) doesn't apply: OpenClear has no generative model.
NIST says the framework is being revised; this page follows version 1.0.

## The model

| | |
|---|---|
| Purpose | Rank exceptions for review by how likely each is to be returned. It never pays or returns an item, and a default applied at the deadline never uses it. |
| Learns from | The bank's own final decisions made by a person, on live items. A default applied because nobody decided in time isn't a label: it says what the account's setting is, not whether the item was fraud. |
| Features | The item's own facts: the amount against the issued amount, days since issue, duplicate, stopped, the OCR and payee match, and for ACH the SEC code, originator and limits. No personal or protected attributes. |
| Shown as | A High, Medium or Low badge with the chance it should be returned. |
| Activated | After at least 200 of the bank's decisions, the first model as much as a replacement; a replacement must also score at least as well as the active model on the same recent decisions. |
| Out of scope | Deciding items; judging customers; any use outside the bank that trained it. |

## The framework's functions

| Function | What OpenClear does | Evidence | The bank's part |
|---|---|---|---|
| Govern | Each model is inventoried with its SHA-256; turning scoring on and changing models are protected changes; separate permissions manage the model and decide items; a signed action log | Settings → ML models; the evidence export | B-RA-3-05, B-CM-3-01 |
| Map | Purpose, limits and features above; advisory only | This page; the exception's help | Decide where it's used (B-RA-3-05) |
| Measure | Holdout AUC, precision and recall; champion/challenger on the same recent decisions; replay of past files; the daily anomaly-capture test | Settings → ML models, Model risk | B-CM-4-01, B-CA-7-02 |
| Manage | A person decides; dual control and segregation of duties apply; scoring can be switched off at any time | Settings → Fraud scoring | Turn it off if it degrades (B-RA-3-05) |

**Not yet:** monitoring in production of how the score agrees with final decisions over time
(drift). The yearly review (B-RA-3-05) covers it until then.

## Sources

Read 22 October 2026: NIST AI 100-1, *Artificial Intelligence Risk Management Framework (AI RMF
1.0)* (January 2023), and its Playbook; NIST AI 600-1, *Generative Artificial Intelligence Profile*
(July 2024); NIST's AI RMF page.
