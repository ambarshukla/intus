"""Seeding is the foundation everything else assumes; it gets property tests.

These are universally quantified claims — "for *any* seed and *any* stream
name" — so hypothesis expresses them directly, where example-based tests would
only sample a handful of the cases that matter.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from intus_gen.seeds import Seeds, seed_int

# codec="utf-8" excludes lone surrogates, which are valid `str` values but
# cannot be encoded — and so are outside what a stream name can ever be. The
# unconstrained strategy found one immediately; widening `seed_int` to tolerate
# it would have been machinery for a case the function cannot receive.
_text = st.text(st.characters(codec="utf-8"), min_size=0, max_size=12)
_seeds = st.integers(min_value=-(2**40), max_value=2**40)
_labels = _text


@given(_seeds, _labels, _labels)
def test_same_stream_same_numbers(seed, first, second):
    """The same run seed and stream name always produce the same draws."""
    left = Seeds(seed).stream(first, second)
    right = Seeds(seed).stream(first, second)
    assert [left.random() for _ in range(8)] == [right.random() for _ in range(8)]


@given(_seeds, _labels, _labels)
def test_streams_are_independent(seed, first, second):
    """Drawing from one stream does not move another.

    This is the property that makes the generators composable: adding a domain,
    or one more employee, must not perturb what every other stream produces.
    """
    untouched = Seeds(seed).stream(first, second)
    expected = [untouched.random() for _ in range(5)]

    fresh = Seeds(seed)
    noisy = fresh.stream("noise")
    for _ in range(100):
        noisy.random()

    # One stream, five successive draws — not five fresh streams, which would
    # only ever compare the first draw against itself.
    target = fresh.stream(first, second)
    observed = [target.random() for _ in range(5)]

    assert observed == expected


@given(_seeds, _labels, _labels)
def test_different_run_seeds_diverge(seed, first, second):
    left = Seeds(seed).stream(first, second).random()
    right = Seeds(seed + 1).stream(first, second).random()
    assert left != right


_parts = st.lists(
    st.text(st.characters(codec="utf-8", exclude_characters="\x1f"), max_size=6),
    min_size=2,
    max_size=4,
)


@given(_parts, _parts)
def test_stream_names_do_not_collide_by_concatenation(left, right):
    """('ab', 'c') and ('a', 'bc') must be different streams.

    The separator exists for exactly this: without it, two unrelated streams
    would silently become one and their outputs would correlate perfectly for
    no discoverable reason. The alphabet excludes the separator itself, since
    a part containing it could legitimately alias.
    """
    if left == right:
        return
    assert seed_int(*left) != seed_int(*right)


def test_seed_int_is_pinned():
    """A hard-coded expected value, so the hashing scheme cannot change silently.

    Python's built-in ``hash()`` is salted per process, so a generator that
    drifted onto it would produce different data on every run. Pinning one
    known digest catches that, and catches any future "harmless" change to the
    separator or digest width — either of which would invalidate every dataset
    generated before it.
    """
    assert seed_int(20260723, "world", "person", "E00001") == (
        90796586215119674954580782133819535957475219020106149525676937902476575142564
    )


def test_part_order_matters():
    assert seed_int("a", "b") != seed_int("b", "a")
