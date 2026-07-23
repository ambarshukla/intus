"""Deterministic seeding — the property everything else in this package rests on.

Every random draw in intus traces back to a single integer, the *run seed*, via
a named **stream**: a tuple of labels identifying what is being generated
(``("hris", "person", "E004217", "comp")``). The stream is hashed with SHA-256
and the digest becomes a private ``random.Random``.

Two properties follow, and both are load-bearing:

**Reproducibility.** The same run seed produces byte-identical output, on any
machine, in any Python process. Python's built-in ``hash()`` cannot be used for
this: it is salted per process for str and bytes, so a generator built on it
produces different data on every run — a bug that hides on the machine that
wrote it and only appears in CI or on a reviewer's laptop.

**Independence.** Because each stream is hashed from its own name rather than
drawn from one shared generator, streams do not interfere. Adding a new domain,
or generating one extra employee, does not shift the numbers every *other*
domain produces. The obvious alternative — one ``Random(seed)`` threaded through
every generator in turn — makes the whole dataset order-dependent, so any
insertion upstream silently rewrites everything downstream. That turns
"regenerable byte-for-byte" into a claim that is technically true and
practically useless, since it only holds while nobody edits the code.

Independence also buys a debugging affordance: any single entity can be
regenerated in isolation, without replaying the run that produced it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from random import Random

# ASCII unit separator. Joining stream parts with a character that cannot
# occur in an identifier keeps the mapping from parts to digest injective:
# with a naive concatenation, ("ab", "c") and ("a", "bc") hash identically and
# two unrelated streams silently become one. The bug that produces is
# spectacularly hard to see — two fields correlate perfectly for no reason.
_SEPARATOR = "\x1f"


def seed_int(*parts: object) -> int:
    """Hash a stream name to an integer suitable for seeding ``Random``.

    The full 256-bit digest is used rather than a truncated prefix. ``Random``
    accepts arbitrary-width integers, so the extra bytes are free, and they put
    stream collisions comfortably out of reach for any dataset size.
    """
    key = _SEPARATOR.join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(key).digest(), "big")


@dataclass(frozen=True, slots=True)
class Seeds:
    """A run seed, plus the derivation of named streams from it.

    Threaded through the generators as a single value so no code has to
    remember to mix the run seed in by hand — forgetting that is the one
    mistake that would make ``--seed`` silently do nothing.
    """

    run_seed: int

    def stream(self, *parts: object) -> Random:
        """A private ``Random`` for the named stream under this run seed."""
        return Random(seed_int(self.run_seed, *parts))
