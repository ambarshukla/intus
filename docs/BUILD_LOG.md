# Build log

Newest first. One entry per merged change; what was built and what was learned.

## 2026-07-23 — Phase 1: the Halcyon synthetic data generators

First code in the repo, and with it the whole Python toolchain: a uv workspace
(one `uv.lock` for every member), a shared `ruff.toml` set strict from commit one
(`E,F,W,I,UP,B,SIM,RUF`, line length 100), a `Makefile`, and a GitHub Actions job
running format check, lint, tests, an end-to-end generation smoke test, and a
check that the generated data catalog is not stale.

`gen/` holds `intus_gen`: deterministic generators for six internal data domains —
HR (employee history, compensation, performance), CRM (accounts, subscriptions,
pipeline, invoices), product telemetry, internal AI usage and cost, finance
(budgets and actuals), and systems access logs. Twelve datasets, ~1.8M rows at
full scale in about ninety seconds, written as UTF-8 CSV with a run manifest.

Four things are worth calling out, because they were the design, not the coding:

**The world is built before any domain.** Employees, org, accounts, products and
subscriptions are generated once and handed to every domain generator. Domains
that cannot join are not a dataset, they are six unrelated files — and the
warehouse phase needs the same `employee_id` to appear in HR records, AI-usage
logs, security logs and as the owner of a CRM account.

**Seeding is per-stream, not global.** Every draw comes from
`sha256(run_seed : stream : entity)`. The obvious alternative — one `Random(seed)`
threaded through every generator — makes the dataset order-dependent, so
inserting a generator anywhere upstream silently rewrites everything downstream.
That reduces "regenerable byte-for-byte" to a claim that holds only while nobody
edits the code.

**Sensitivity tiers are declared beside the schema and enforced at import.** A
`Dataset` validates that its declared columns match its record type's fields
exactly, in order. A column added without a classification is an ImportError, not
a review comment — and `docs/data-catalog.md` is generated from those
declarations rather than maintained by hand.

**Defects carry ground truth.** Nineteen deliberate corruptions, each recorded in
the manifest with the key it landed on. The tests do not assert that injection
*ran*; they re-derive each defect from the data and check the manifest describes
what actually happened. A defect that silently no-opped while still reporting
itself would make every future detection metric a lie and would look exactly like
a passing suite.

Two bugs the tests caught that review had not. A subscription could be emitted
with `end_date` before `start_date`, for customers who churned before buying an
add-on — visible only for accounts that churn early in their life. And the
property test for stream independence was itself wrong: it built a fresh stream
per iteration, so it compared the first draw against itself five times and would
have passed against a completely broken implementation.

Money is `Decimal`, never `float`. Binary floating point cannot represent most
decimal fractions exactly, and the error surfaces precisely where it is least
welcome — a budget variance off by a cent, or a legacy-versus-lakehouse parity
check that fails on rounding rather than on logic.

## 2026-07-23 — Repo hygiene: line endings and editor settings

Added `.gitattributes` (`* text=auto eol=lf`, plus binary markers for png/pdf/pbix) and
`.editorconfig` before any code exists, deliberately. Both are cheap now and annoying
later: normalising line endings after files are committed produces a churn commit that
touches every line of every file, and the Power BI artifact extension (`.pbix`) needs
the binary marker in place *before* the first one is added, or Git will try to
line-ending-normalise a binary file and corrupt it.

## 2026-07-23 — Scaffold

Repo created: README (project thesis: internal data + governance-first + legacy-SQL →
Databricks modernization), docs skeleton (this file, DECISIONS, GLOSSARY), .gitignore.
No code yet. First real phase: the synthetic data generators for Halcyon's internal
domains, with sensitivity tiers labeled at generation time.
