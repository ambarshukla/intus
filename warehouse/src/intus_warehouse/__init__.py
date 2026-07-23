"""The legacy Postgres warehouse for the Halcyon dataset.

Deliberately old-fashioned: plain SQL migrations, COPY-based bulk loading, and
a star schema built by SQL rather than by a transformation framework. This is
the "before" system in the modernization story, and it is only worth having if
it is a *credible* before — a straw man proves nothing about the migration that
replaces it.
"""

__version__ = "0.1.0"
