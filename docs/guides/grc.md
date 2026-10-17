# Connecting your GRC tool

OpenClear tests its own controls continuously, and proposes attestations of the
*complementary user entity controls* (CUECs), the things OpenClear relies on you to do, from
that test history. This guide is for the team that runs a bank's GRC platform (Archer,
ServiceNow IRM, AuditBoard, Hyperproof, OneTrust, or an open-source one such as CISO Assistant
or a compliance-trestle repository). It shows how to map the CUECs onto your own controls once,
then have attestations arrive in your GRC tool each period without anyone copying them over.

## The short version

1. **Map** each CUEC to your own control, once: upload the filled-in `cuecs.csv`, or set them
   through the API.
2. Decide which mappings OpenClear may **accept automatically**. For those, an attestation is
   proposed, accepted and signed at the end of each period with no one involved.
3. Have your GRC tool **pull** the period's attestations by your control id (a scheduled job
   with a read-only API client), or **receive** them as webhooks as soon as they exist.
4. **Verify** each attestation's signature with your installation's public key, and keep the
   signed file as the evidence.
5. If your GRC tool reads **OSCAL**, import OpenClear's component definition once and each
   period's assessment results.

Everything below is under `/api/v1/compliance/` (OpenAPI: `openapi/openclear-v1.json`; the
Python, TypeScript and .NET SDKs have each call as `compliance.*`).

## Words

| Word | Meaning |
|---|---|
| **Control** | Something OpenClear does (`OC-AC-05`, segregation of duties), filed under NIST SP 800-53 Rev. 5 controls, with the NIST CSF 2.0 subcategories NIST maps to them. See [OpenClear's controls](../compliance/controls.md). |
| **Check** | A test OpenClear runs on a control (hourly, or nightly over the previous day). Every scheduled run is kept, with a SHA-256 of its evidence. |
| **Deviation** | A stretch of time a check was failing, with the cause and the bank's response. |
| **CUEC** | Something you must do for OpenClear's controls to work (`B-AC-5-01`). See the [CUEC inventory](../compliance/cuecs.md). |
| **Mapping** | Your own control (`BANK-SOD-1`) that covers a CUEC. |
| **Attestation** | A signed statement that a CUEC operated over a period. *Automated* ones are proposed by OpenClear from its checks; *manual* ones are written by a person. |

## Credentials

1. **Settings → Security groups**: a group with `compliance:read`. Add `compliance:manage` if
   your GRC tool will set mappings, and `compliance:attest` if it will accept attestations
   (most banks keep accepting in OpenClear, with a person).
2. **Settings → API clients**: a client in that group. For pulling only, tick **Read only**:
   a read-only client keeps `compliance:read` and can never write, whatever its group allows.

A business customer that attests its own CUECs can do the same with its own API client: it
sees only its own CUECs, mappings and attestations.

## 1. Map the CUECs to your controls

Download [`cuecs.csv`](../compliance/cuecs.csv) (or `GET /api/v1/compliance/cuecs`), fill in
`your_control_id` and `your_control_owner` (and, if you like, add `your_control_name` and
`your_control_link` columns), and upload it:

```
curl -H "Authorization: Bearer $TOKEN" -F file=@cuecs.csv \
  https://openclear.yourbank.example/api/v1/compliance/mappings/import
```

Rows left blank are skipped, and loading the file again doesn't add a mapping twice. To set one
CUEC's mappings exactly, use `PUT /api/v1/compliance/cuecs/{cuec_id}/mappings`:

```json
{"mappings": [{"control_ref": "BANK-SOD-1", "owner": "Operational risk",
               "link": "https://grc.yourbank.example/controls/BANK-SOD-1", "auto_accept": true}]}
```

Mappings not in the list are removed. Turning `auto_accept` on is recorded in the action log
and raised as a security event, because from then on OpenClear signs that CUEC's attestations
without a person.

## 2. How an automated attestation is decided

At the end of each period (monthly, quarterly or yearly: Settings → CUECs), OpenClear proposes
an attestation for each automated CUEC from its check's scheduled results:

- **operated**: the check ran at least 95% of its scheduled times, no deviation lasted longer
  than the bank's grace period (24 hours by default), and every population test passed;
- **operated with exceptions**: covered, but with such deviations or failed population tests,
  each listed;
- **not tested**: under 95% coverage.

The numbers always go with it, so your auditors can apply their own threshold. To see them for
any period before it's attested:

```
GET /api/v1/compliance/cuecs/B-AC-5-01/evidence?start=2026-01-01&end=2026-03-31
```

`proposed_result` is what OpenClear would propose, and `evidence` has the runs expected and
recorded, the statuses, each deviation and the digests of the results it rests on.

Assisted and attested CUECs (training, risk assessment, backups) need a person. Record those in
OpenClear (Settings → CUECs), or from your GRC tool with
`POST /api/v1/compliance/cuecs/{cuec_id}/attestations` (a form with `start`, `end`, `result`,
`statement` and an optional `evidence_file`; several files go in a zip). Someone with
`compliance:attest` then accepts it (`POST /api/v1/compliance/attestations/{id}/accept`), and if
the bank asks for a second person, that's someone other than whoever recorded it.

## 3a. Pull: a scheduled job

The usual pattern: once a day, your GRC tool asks for what changed since it last asked, and
files each attestation under the control it's mapped to.

```
GET /api/v1/compliance/attestations?status=accepted&updated_since=2026-04-01T00:00:00Z
```

Each row has `cuec_id`, `mappings` (your control ids), the period, `result`, `method`,
`auto_accepted`, `sha256` and `verified`. To page, pass the last row's `updated_at` as
`updated_since` and its `id` as `after_id` (the `Link: rel="next"` header does this for you).
To ask only about one of your controls, add `control=BANK-SOD-1`.

For the signed body, `GET /api/v1/compliance/attestations/{id}` (`signed`), or the whole period
at once:

```
openclear compliance attestations --start 2026-01-01 --end 2026-03-31 --format json -o q1.json
```

`--format csv` gives a row each, for a spreadsheet; `--format oscal` the OSCAL assessment
results below.

A bank's own job can read a customer's attestations with `customer_id=...`.

## 3b. Push: webhooks

Subscribe a webhook endpoint (Settings → Webhooks) to:

| Event | When | Data |
|---|---|---|
| `compliance.attestation.prepared` | OpenClear proposed an attestation; it waits for acceptance unless a mapping accepts it | `attestation_id`, `cuec_id`, `period_start`, `period_end`, `result`, `status` |
| `compliance.attestation.accepted` | An attestation was accepted and signed | the same, and `sha256` |

Webhooks are signed (see the API reference). On `accepted`, fetch
`GET /api/v1/compliance/attestations/{attestation_id}` for the signed body.

## 4. Verify a signature

An accepted attestation is canonical JSON (sorted keys, no spaces, UTF-8) signed with your
installation's file-signing key: ECDSA P-256 over its SHA-256. Pin the public key once, from
your OpenClear administrator, rather than trusting the copy that comes with each file. Then, in
Python:

```python
import hashlib, json
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

def verify(signed: dict, public_key_pem: str) -> bool:
    body = json.dumps(signed["attestation"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(body).digest()
    if digest.hex() != signed["signature"]["sha256"]:
        return False
    key = serialization.load_pem_public_key(public_key_pem.encode())
    try:
        key.verify(bytes.fromhex(signed["signature"]["value"]), digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        return True
    except Exception:
        return False
```

An attestation is never edited: a correction is a new one that supersedes it, and the old one
stays readable with `status: superseded`.

## 5. OSCAL

OpenClear writes OSCAL 1.2.1, checked in its test suite against NIST's published schemas and
read by compliance-trestle.

- **Component definition** (`GET /api/v1/compliance/oscal/component-definition`): OpenClear as a
  software component implementing NIST SP 800-53 Rev. 5, one implemented requirement per
  control. OSCAL's component definition has no place for a customer's responsibility, so each
  OpenClear control and each CUEC on a control is a property in OpenClear's namespace
  `https://github.com/chaffed/OpenClear/ns/oscal`: `openclear-control`, or `cuec` with the party
  (`bank`, `business-customer`, `host`) as its `class`, each with its statement as `remarks`.
  Each NIST CSF 2.0 subcategory NIST maps to the control is a `csf-subcategory` property.
  Import it once, and again when OpenClear's inventories change (the changelog says when).
- **Assessment results** (`GET /api/v1/compliance/oscal/assessment-results?start=&end=`): your
  period's control testing. An observation for each check that ran (its results' counts and
  the SHA-256 over their evidence digests), a finding for each deviation (not satisfied,
  against the objective of the check's control), and each accepted attestation. OpenClear's
  checks run on their own schedule rather than from an assessment plan, so `import-ap` points
  at a back-matter resource that says so.

With compliance-trestle:

```
trestle import -f component-definition.json -o openclear
trestle import -f assessment-results-q1.json -o openclear-2026-q1
trestle validate -a
```

## Results and deviations directly

If your GRC tool keeps its own test history, it can take OpenClear's raw results instead:

```
GET /api/v1/compliance/controls                                   # each control, its checks and status now
GET /api/v1/compliance/controls/OC-AC-05/results?start=&end=      # every recorded result, oldest first
GET /api/v1/compliance/controls/OC-AC-05/deviations?start=&end=   # each deviation, with cause and response
```

Results never change once recorded; `updated_since` on results means recorded since. The
signed evidence export (`openclear audit export`) also carries the period's checks,
deviations and attestations.
