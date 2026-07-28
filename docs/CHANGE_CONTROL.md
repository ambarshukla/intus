# Change control

Evidence that a change to the governance layer (or anything else in this
repo) went through review before it took effect, and that the record of
*why* survives the change itself. This isn't a separate process bolted onto
the project for Phase 4 — it's the PR workflow every phase has already used,
pointed at itself and made explicit because governance is the phase where a
reviewer is most likely to ask for it directly.

## The mechanism, as it actually exists today

1. **Every change is a named branch, never a direct commit to `main`.**
   `main` is the deployed state (`databricks.yml`'s job runs it via
   `git_source`); a branch is a proposal until merged.
2. **The design is written down before the code, in `docs/DECISIONS.md`**,
   including alternatives considered and why they were rejected — not a
   changelog of what changed, a record of what was decided and the reasoning
   available at decision time. D-029 through D-033 are this PR's: two design
   decisions (independent RLS/masking axes, GRANT-vs-row-filter choice) and
   three findings forced by the live platform (a dialect trap, a feature
   conflict, a propagation delay) that changed the design mid-build.
3. **Tests travel with the change, in the same commit.**
   `lakehouse/tests/test_governance_coverage.py` asserts every RESTRICTED
   column is masked by reading the generator's own classification, not by
   restating a list a future change could silently invalidate — the same
   drift-check discipline (D-010) as everywhere else in this project.
4. **A pull request is the approval gate.** CI (ruff + pytest, per
   `.github/workflows/ci.yml`) must pass before merge; branch protection on
   `main` requires it. The PR description and the DECISIONS entries it links
   are the reviewable record of what's being approved and why.
5. **`git log` is the immutable audit trail.** Who authored a change, when,
   and what it touched is never in question after the fact — this is true of
   every commit in this repository already, not something added for
   governance specifically.

## What this PR's own change-control record looks like

- Branch: `feat/phase4-governance`.
- Design decisions: D-029 (governance's two-axis model and the GRANT-vs-row-
  filter split), D-030 (a row-filter parameter-naming rule, forced by a live
  bug that silently permitted everything), D-031 (dropping
  `ck_dim_employee_span` to gain governance on that table — a real,
  documented trade-off, not a silent regression), D-032 (a governance-owned
  identity mapping table, forced by a platform restriction on nested
  policies), D-033 (group-membership propagation delay, an operational
  finding that changes how future access reviews must be read).
- Evidence the mechanism actually works, not merely compiles:
  `docs/ACCESS_REVIEW.md`'s 2026-07-26 review, performed against the live
  workspace by toggling real group membership and re-querying — the same
  "verify against live systems, record predictions before runs" discipline
  every phase in this project has followed (see CLAUDE.md's standing
  practices).
- Test coverage: `test_governance_coverage.py` (new), full suite re-run
  green before commit.

## Known gaps, named rather than assumed away

- **No independent approver.** This is a single-author repository; every PR
  here is self-merged after CI passes, not approved by a second reviewer.
  A real SOX environment requires the approver to be someone other than the
  author — flagged in `docs/ACCESS_REVIEW.md` as the same segregation-of-
  duties gap, because it's the same underlying limitation showing up in two
  places.
- **No emergency-change process.** Every change here goes through the full
  branch → PR → CI → merge cycle; there is no documented break-glass path
  for an urgent access revocation, which a real production access-control
  system needs (e.g. immediately disabling a departing employee's access
  ahead of the normal review cycle) — especially given D-033's finding that
  even a successful revocation call is not immediately effective. Named as
  an open item, not built here.
- **No automated diff between what a PR changes and what
  `docs/ACCESS_REVIEW.md`'s matrix claims.** The matrix is currently
  maintained by hand alongside the SQL, the same way `docs/data-catalog.md`
  *used* to be before D-004 made it generated — a natural next hardening
  step (generate the access matrix from `department_scope`/
  `capability_grant` directly) rather than something this PR builds
  speculatively ahead of a second real change to prove the pattern against.
