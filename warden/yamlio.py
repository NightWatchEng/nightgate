"""The one YAML loader every warden read goes through.

libyaml (`yaml.CSafeLoader`) is several times faster than PyYAML's pure
`SafeLoader` but accepts a different language: it takes tabs as separators,
`[b?c]` and `|#`, and reads a bare `!` as ''; it refuses `%YAML 1.4`, unknown
directives and a mid-stream byte-order mark. So the pure loader's verdict is
the only verdict and libyaml is a shortcut to it. Text holding a lexical
trigger (_PURE_ONLY) and bytes go straight to the pure loader; otherwise the
pure loader re-parses whenever libyaml refuses (so every refusal carries the
pure message and caret) or the composed tree shows a divergence (_Diverges).
tests/test_yamlio.py proves the two agree over this tree's YAML and mutations.

Each document is parsed once per process: `certify` alone
asked for the same 31 documents ~11,500 times, mostly rule frontmatter
re-read per review shard. The memo is keyed by the TEXT itself, not by
(path, mtime_ns, size): a same-size rewrite inside one mtime tick cannot
serve a stale parse, and the callers that parse git blobs, frontmatter slices
or subprocess output (no path at all) are covered by the same key. Each hit
returns a deep copy, so no caller can mutate another's document. Refusals
are not memoized; they re-parse and raise afresh. `cli.main` clears the memo
per command; nothing is written anywhere. WARDEN_NO_YAML_CACHE=1 turns it off,
which is how tests/test_yaml_cache.py proves the outputs identical.
"""
from __future__ import annotations

import copy
import os
import re

import yaml

try:
    from yaml import CSafeLoader as _Fast
except ImportError:  # a PyYAML built without libyaml headers
    _Fast = None

ACCELERATED = _Fast is not None

_PURE_ONLY = re.compile(r"[\t\ufeff]|^%|(?<!\S)[|>][-+0-9]*#|"
                        + yaml.reader.Reader.NON_PRINTABLE.pattern, re.MULTILINE)
# The pure parser recurses per level and dies of RecursionError near depth
# 490 (sooner under a deep caller); libyaml does not. Deeper than this, the
# pure loader re-parses, so the document succeeds or dies as it always did.
_MAX_FAST_DEPTH = 64


class _Diverges(Exception):
    """libyaml read this document in a way the pure loader would not."""


def _check_tree(root: yaml.Node) -> None:
    # Document order: a node is first met at its anchor's textual depth, which
    # the pure composer recurses over; `seen` stops alias re-walks and cycles.
    stack, seen = [(root, 1, False)], set()
    while stack:
        node, depth, in_flow = stack.pop()
        if isinstance(node, yaml.ScalarNode):
            # The pure scanner ends a flow-context plain scalar at `?`.
            if in_flow and not node.style and "?" in node.value:
                raise _Diverges
            continue
        if id(node) in seen:
            continue
        if depth > _MAX_FAST_DEPTH:
            raise _Diverges
        seen.add(id(node))
        children = (node.value if isinstance(node, yaml.SequenceNode)
                    else [n for pair in node.value for n in pair])
        stack.extend((c, depth + 1, bool(node.flow_style)) for c in reversed(children))


class _UniqueKeys:
    """Refuse a duplicate mapping key instead of keeping the last one."""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in seen
            except TypeError as e:    # `? [a, b]`: a YAMLError, as before
                raise yaml.YAMLError(f"unhashable mapping key {key!r}") from e
            if duplicate:
                raise yaml.YAMLError(f"duplicate key {key!r} — the second "
                                     f"silently replaces the first")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


class _FastChecked:
    def resolve(self, kind, value, implicit):
        # Only libyaml resolves a scalar so: a bare `!` on an empty one,
        # which it reads as '' where the pure parser reads None.
        if kind is yaml.ScalarNode and implicit == (False, False):
            raise _Diverges
        return super().resolve(kind, value, implicit)


_PureUnique = type("_PureUnique", (_UniqueKeys, yaml.SafeLoader), {})
if _Fast is not None:
    _Fast = type("_FastSafe", (_FastChecked, _Fast), {})
_FastUnique = type("_FastUnique", (_UniqueKeys, _Fast), {}) if _Fast else None


def _run(loader_cls: type, stream: str | bytes, check: bool = False) -> object:
    # yaml.load's own body, over SafeConstructor loaders only.
    loader = loader_cls(stream)
    try:
        node = loader.get_single_node()
        if node is not None and check:
            _check_tree(node)
        return None if node is None else loader.construct_document(node)
    finally:
        loader.dispose()


_MEMO: dict[tuple[bool, str], object] = {}
# A bound, not a tuning knob: one command reads a few dozen documents; a
# long-lived importer (the test suite) must not grow without limit.
_MEMO_MAX = 1024


def clear_cache() -> None:
    """Forget every memoized document (one command's worth of state)."""
    _MEMO.clear()


def load(stream: str | bytes, *, unique_keys: bool = False) -> object:
    """yaml.safe_load, pure acceptance and errors; unique_keys refuses dupes.

    Memoized by content for str input (see the module docstring)."""
    if not isinstance(stream, str) or os.environ.get("WARDEN_NO_YAML_CACHE") == "1":
        return _load(stream, unique_keys)
    key = (unique_keys, stream)
    try:
        doc = _MEMO[key]
    except KeyError:
        doc = _load(stream, unique_keys)
        if len(_MEMO) >= _MEMO_MAX:
            _MEMO.clear()
        _MEMO[key] = doc
    return copy.deepcopy(doc)


def _load(stream: str | bytes, unique_keys: bool) -> object:
    fast = _FastUnique if unique_keys else _Fast
    if fast is not None and isinstance(stream, str) and not _PURE_ONLY.search(stream):
        try:
            return _run(fast, stream, check=True)
        # warden:allow(except-pass): not swallowed — the pure parse below re-reads the same text and raises its own verdict
        except Exception:  # any libyaml failure: `!<%C0%80>` is a UnicodeDecodeError
            pass
    return _run(_PureUnique if unique_keys else yaml.SafeLoader, stream)
