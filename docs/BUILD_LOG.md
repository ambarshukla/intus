# Build log

Newest first. One entry per merged change; what was built and what was learned.

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
