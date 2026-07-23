# Glossary

Terms used in this project, expanded on first use in the docs and kept here.

- **Medallion architecture** — the bronze (raw) → silver (cleaned/conformed) → gold
  (business-ready) layering convention for a lakehouse.
- **Row-level security (RLS)** — access rules that filter which *rows* of a table a
  given principal can see (e.g., an HR analyst sees only their region's employees).
- **Column masking** — access rules that redact or transform specific *columns* for
  unauthorized principals (e.g., compensation shows as NULL or a band, not a number).
- **SOX (Sarbanes-Oxley Act)** — US financial-reporting law; for data platforms it
  implies auditable change control, separation of duties, access reviews, and evidence
  that controls actually operated.
- **Parity check** — during a platform migration, an automated comparison proving the
  new system reproduces the old system's numbers before cutover.
- **Sensitivity tier** — a label (here: public / internal / confidential / restricted)
  attached to data at generation time, driving which governance controls apply.
- **SCD2 (slowly changing dimension, type 2)** — a dimension table that keeps history by
  storing one row per period of validity, with effective-from/effective-to dates, rather
  than overwriting an attribute when it changes. `hr_employee_history` is generated in
  this shape so the warehouse phase builds its employee dimension from honestly
  effective-dated source data instead of inferring history from a snapshot.
- **ARR (annual recurring revenue)** — the annualised value of a subscription, net of
  discount; the headline revenue metric for a subscription business.
- **GL (general ledger) account** — the code an accounting entry is booked against
  (e.g. 6000 Salaries, 6700 Cloud Infrastructure), determining how it rolls up in
  financial reporting.
- **Cost centre** — the organisational unit a cost is charged to; here one per
  department, and the unit budgets and actuals are compared at.
- **Fiscal period** — the accounting month a transaction belongs to, which is not
  always the month it was posted in. The gap between the two is what period-close
  controls exist to police.
- **Segregation of duties** — the control principle that no single person both initiates
  and approves a transaction. Modelled here by keeping budget approvers and ledger
  posters distinct populations.
- **ITGC (IT general controls)** — the baseline IT controls a financial audit relies on:
  access provisioning and removal, change management, and operations. "A terminated
  employee's account still logging in" is a classic ITGC exception.
- **Data steward** — the business function accountable for a dataset; the approver an
  access review routes to, recorded per dataset in the catalog.
- **Retention window** — how long a dataset keeps history. Security logs here retain 180
  days while every other domain keeps full history, as they do in practice.
- **Run manifest** — the file written alongside generated data recording the inputs
  (seed, scale, as-of date), each output file's SHA-256 and row count, and the
  ground-truth list of injected defects.
- **Defect manifest / ground truth** — the record of exactly which rows were
  deliberately corrupted, against which a data-quality framework's detection rate can be
  scored rather than merely demonstrated.
- **Property-based testing** — testing a universally quantified claim ("for *any* seed,
  two runs agree") by having the framework generate many inputs, rather than asserting
  against a handful of hand-picked examples. Done here with `hypothesis`.
- **Impossible travel** — two authentications from locations too far apart to have been
  travelled between in the elapsed time; a standard credential-sharing signal.
- **Shadow AI** — use of AI models outside the approved catalog, invisible to the
  governance and cost-allocation processes built around the approved ones.
- **uv workspace** — a set of Python packages sharing one lockfile, so every member
  resolves identical dependency versions and a skew between them is unrepresentable.
