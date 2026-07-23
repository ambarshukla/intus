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
