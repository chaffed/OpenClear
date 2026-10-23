> The original specification for OpenClear's compliance and control testing. It describes a Go
> layout (`pkg/`, `cmd/`); OpenClear is Python. [COMPLIANCE_PLAN.md](../../plans/COMPLIANCE_PLAN.md) and
> [CONTROLS_PLAN.md](../../plans/CONTROLS_PLAN.md) map it onto the code and say where they differ.

# Technical Specification: OpenClear Automated Compliance, Control & Model Risk Testing Engine

## 1. Overview & Objective
This specification guides the implementation of an automated compliance, continuous control testing, and audit framework within OpenClear. The goal is to enforce **Controls-as-Code** and satisfy **SR 11-7 (Model Risk Management)**, **Interagency TPRM Guidance (2023)**, and **FDIC/NCUA Internal Control** requirements out of the box, minimizing operational burden for deploying financial institutions.

---

## 2. Feature Architecture & Core Components

```
openclear/
├── pkg/
│   ├── audit/          # Immutable logging, evidence packaging, & regulatory exports
│   ├── controls/       # Real-time policy assertion & RBAC / SoD enforcement
│   ├── mrm/            # Model Risk Management: Replay engine & synthetic generators
│   └── ccm/            # Continuous Control Monitoring & system health checks
├── cmd/
│   └── openclear-cli/   # CLI commands for audit exports and dry-run tests
└── docs/
    └── compliance/     # Standard regulatory crosswalks & TPRM artifacts
```

---

## 3. Detailed Component Requirements

### Component A: Programmatic Segregation of Duties (SoD) & Policy Assertions
*   **Regulatory Reference:** FDIC Internal Control Guidelines / NCUA Part 715.
*   **Requirements:**
    1. Implement a strict Role-Based Access Control (RBAC) middleware for API endpoints and UI actions.
    2. Enforce Dual-Control and SoD constraints in code:
       * `FileIngestor` role CANNOT execute `ExceptionOverride` or `PayDecision`.
       * `RuleAuthor` role CANNOT execute `RuleDeploy` to production.
    3. If an SoD breach attempt occurs, the API must return `HTTP 403 Forbidden` with error payload `ERR_SOD_VIOLATION` and emit a critical audit event to the immutable log queue immediately.

### Component B: Model Risk Management (MRM) - Replay Engine & Synthetic Generator
*   **Regulatory Reference:** OCC Bulletin 2011-12 / Federal Reserve SR 11-7.
*   **Requirements:**
    1. **Historical File Replay ("Shadow Match") Engine:**
       * Create an engine endpoint (`/api/v1/mrm/replay`) that accepts historical file payloads (Check X9.37, NACHA ACH, ISO 20022 RTP XML).
       * Execute matching rules in **Dry-Run Mode** against target account histories.
       * Output a **Confidence & Variance Report** comparing historical results vs. proposed rule output (calculating match rate, false positive rate, unhandled exception rate).
    2. **Synthetic Data & Anomaly Generator (`openclear-testgen`):**
       * Build a package capable of generating synthetic batches with injected fraud vectors:
         * Duplicated check serial numbers & altered ACH amounts.
         * Mismatched account numbers and malformed ISO 20022 XML fields.
       * Automate daily execution against active rules to verify 100% anomaly capture.

### Component C: Automated Evidence Engine & Audit Export
*   **Regulatory Reference:** 2023 Interagency Guidance on Third-Party Relationships.
*   **Requirements:**
    1. **Immutable Audit Queue:** Log all administrative actions, rule modifications, exception overrides, and file digests with cryptographic signatures (HMAC SHA-256) and precise timestamps (ISO 8601).
    2. **CLI & API Export:**
       * Implement command: `openclear-cli audit export --start <DATE> --end <DATE> --format json|pdf`
       * The generated export package must bundle:
         * System version & deployed rule signatures.
         * SoD violation logs.
         * Exception decision histories with user attribution.
         * Live Control Status Snapshot.

### Component D: Continuous Control Monitoring (CCM) Dashboard
*   **Requirements:**
    1. Build an internal API endpoint `/api/v1/controls/status` that executes real-time assertions:
       * **Data at Rest Security:** Verify encryption status of sensitive database columns (AES-256).
       * **SLA Timeliness:** Verify batch ingestion queues against bank cut-off schedules.
       * **Parser Hardening:** Execute input validation checks to prevent buffer overflow/injection attacks on file parsers.
    2. Expose health statuses: `PASS`, `WARN`, or `FAIL` for each control item.

---

## 4. Implementation Steps for Coding Agent

1. **Step 1: Enforce SoD Policy Checks**
   * Update RBAC middleware in `pkg/controls/rbac.go` to assert SoD rules prior to processing exception overrides or rule changes. Write unit tests asserting that unauthorized overrides fail.

2. **Step 2: Build Replay Engine (`pkg/mrm/replay.go`)**
   * Implement dry-run evaluation logic that bypasses actual database ledger commits and returns structured validation scorecards (`ReplayResult struct`).

3. **Step 3: Implement Audit Export CLI (`cmd/openclear-cli/audit.go`)**
   * Query the audit log store, verify signature chains, and format output into machine-readable JSON or standardized compliance PDF reports.

4. **Step 4: Create Compliance Artifacts Directory (`docs/compliance/`)**
   * Add markdown templates mapping OpenClear features to FFIEC IT booklets, NACHA framework security rules, and SR 11-7 model validation templates.