"""warden.yamlio returns the pure loader's value or refusal, message and all,
on every input, and libyaml really is in play."""

import ast
import functools
import random
import re
from pathlib import Path

import pytest
import yaml

from conftest import tracked
from warden import yamlio

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_memo(monkeypatch):
    # These tests judge the PARSER, and several load one text twice under two
    # loader states; the per-process memo would answer the
    # second load from the first and the no-libyaml leg would parse nothing.
    # tests/test_yaml_cache.py proves the memo returns what the parser does.
    monkeypatch.setenv("WARDEN_NO_YAML_CACHE", "1")


def _outcome(fn, text):    # repr keeps 1 and True apart
    try:
        return ("ok", repr(fn(text)))
    except yaml.YAMLError as e:
        return ("err", type(e).__name__, str(e))
    except UnicodeError:    # libyaml on a lone surrogate or a bad `%` escape
        return ("escaped",)


def _pure(text):
    return yaml.load(text, Loader=yaml.SafeLoader)  # noqa: S506


def _shortcut(text):
    if yamlio._PURE_ONLY.search(text):    # the libyaml path alone, or None
        return None
    try:
        return _outcome(lambda t: yamlio._run(yamlio._Fast, t, check=True), text)
    except yamlio._Diverges:
        return None


@functools.cache
def _corpus():
    """Tracked .yaml/.yml, Markdown frontmatter and yaml fences, test literals."""
    docs = {}
    for path in tracked("*"):         # what the commit ships, not the disk
        name, rel = path.name, str(path.relative_to(ROOT))
        try:
            if name.endswith((".yaml", ".yml")):
                docs[rel] = path.read_text(encoding="utf-8-sig")
            elif name.endswith(".md"):
                text = path.read_text(encoding="utf-8")
                if text.startswith("---\n") and "\n---" in text[4:]:
                    docs[rel + "#front"] = text[4:text.index("\n---", 4) + 1]
                for i, m in enumerate(re.finditer(r"```ya?ml\n(.*?)```", text, re.S)):
                    docs[f"{rel}#yaml{i}"] = m.group(1)
            elif name.endswith(".py") and rel.startswith("tests/"):
                for n in ast.walk(ast.parse(path.read_text())):
                    if (isinstance(n, ast.Constant) and isinstance(n.value, str)
                            and ":" in n.value and "\n" in n.value):
                        docs[f"{rel}:{n.lineno}:{n.col_offset}"] = n.value
        except (UnicodeDecodeError, OSError, SyntaxError):
            continue
    return docs


def test_the_process_is_on_libyaml_and_the_corpus_is_the_tree():
    # Without libyaml every comparison here is SafeLoader against itself.
    assert yaml.__with_libyaml__ and yamlio.ACCELERATED
    assert {"repo.yaml", "graph.yaml", ".warden/memory/tags.yaml", ".warden/catalog-answers.yaml",
            "warden/guardrails/catalog.yaml"} <= _corpus().keys()
    for prefix, floor in ((".warden/rules/", 5), ("examples/", 3), ("tests/", 200)):
        assert sum(k.startswith(prefix) for k in _corpus()) >= floor, prefix


def test_every_document_in_the_tree_loads_identically():
    diverged = []
    for name, text in _corpus().items():
        pure = _outcome(_pure, text)
        if _outcome(yamlio.load, text) != pure:
            diverged.append(("load", name))
        # The shortcut's premise on its own: what it accepts, pure accepts.
        fast = _shortcut(text)
        if fast is not None and fast[0] == "ok" and fast != pure:
            diverged.append(("shortcut", name))
    assert not diverged, diverged[:20]


# name: (input, does raw libyaml's verdict differ from the pure one?)
@pytest.mark.parametrize("text,raw_diverges", [
    pytest.param("id:\tfoo-bar\n", True, id="tab after colon (bead)"),
    pytest.param("%YAML 1.4\n---\na: 1\n", True, id="%YAML 1.4 (bead)"),
    pytest.param("a: [1,\t2]\n", True, id="tab in flow"),
    pytest.param("a\t: 1\n", True, id="tab before indicator"),
    pytest.param("a:\n\tb: 1\n", False, id="tab indentation"),
    pytest.param("%FOO bar\n---\na: 1\n", True, id="unknown directive"),
    pytest.param("a: [b?c]\n", True, id="? in flow scalar"),
    pytest.param("{a: b?}\n", True, id="? ending flow value"),
    pytest.param("a: b?c\n", False, id="? in block scalar"),
    pytest.param("a: |#\n  x\n", True, id="block header then comment"),
    pytest.param("a: !\n", True, id="empty tag"),
    pytest.param("a: ! x\n", False, id="non-specific tag"),
    pytest.param("\ufeffa: 1\n", False, id="leading bom"),
    pytest.param("a: 1\n\ufeffb: 2\n", True, id="bom mid-stream"),
    pytest.param("a: 1\x00\x9f\n", False, id="nul and c1 control"),
    pytest.param("a: \ud800\n", True, id="lone surrogate"),
    pytest.param("a: 1\na: 2\nb: 123456789012345678901234567890\nc: yes\nd: No\n"
                 "e: on\nf: 1:30\ng: 0777\n", False, id="dupes, big int, 1.1 scalars"),
    pytest.param("a: 1\n---\nb: 2\n", False, id="two documents"),
    pytest.param("a: 1\x85b: 2\n", False, id="nel line break, libyaml path"),
    pytest.param("k" * 1100 + ": 1\n", False, id="long simple key, libyaml path"),
    pytest.param("a: !<%C0%80> x\n", True, id="invalid utf-8 tag escape"),
])
def test_documented_divergence_resolves_to_the_pure_verdict(text, raw_diverges, monkeypatch):
    pure = _outcome(_pure, text)
    assert _outcome(yamlio.load, text) == pure
    # Pinned both ways: an upgrade that moves the divergence set fails here.
    fast = _outcome(lambda t: yaml.load(t, Loader=yaml.CSafeLoader), text)
    assert ((fast[0], fast[1] if fast[0] == "ok" else "") !=
            (pure[0], pure[1] if pure[0] == "ok" else "")) is raw_diverges
    monkeypatch.setattr(yamlio, "_Fast", None)      # a PyYAML without libyaml
    assert _outcome(yamlio.load, text) == pure


def test_the_no_libyaml_leg_parses_again_after_a_libyaml_load(monkeypatch):
    text = "a: b?c\n"
    assert yamlio.load(text) == {"a": "b?c"}
    loaders = []
    real = yamlio._run
    monkeypatch.setattr(yamlio, "_run",
                        lambda cls, *a, **k: loaders.append(cls) or real(cls, *a, **k))
    monkeypatch.setattr(yamlio, "_Fast", None)
    assert yamlio.load(text) == {"a": "b?c"}
    assert loaders == [yaml.SafeLoader]


def test_duplicate_keys_refused_on_both_paths(monkeypatch):
    text = "ceiling: 1\nceiling: 2\n"
    assert yamlio.load(text) == {"ceiling": 2}
    for fast in (yamlio._FastUnique, None):
        monkeypatch.setattr(yamlio, "_FastUnique", fast)
        with pytest.raises(yaml.YAMLError, match="duplicate key 'ceiling'"):
            yamlio.load(text, unique_keys=True)


def test_nesting_past_the_pure_loaders_reach_fails_as_it_did():
    deep = "[" * 600 + "]" * 600
    assert yaml.load(deep, Loader=yaml.CSafeLoader)
    for fn in (_pure, yamlio.load):
        with pytest.raises(RecursionError):
            fn(deep)
    anchored = "".join(f"&a{i} [" if i % 50 == 0 else "[" for i in range(600))   # aliases
    anchored = f"- {anchored}{']' * 600}\n- [{', '.join(f'*a{i}' for i in range(50, 600, 50))}]\n"
    assert yaml.load(anchored, Loader=yaml.CSafeLoader)
    with pytest.raises(RecursionError):
        yamlio.load(anchored)
    cycle = yamlio.load("a: &x [1, *x]\n")        # the depth walk terminates
    assert cycle["a"][1] is cycle["a"]
    assert yamlio.load("a: 1\n".encode("utf-16")) == {"a": 1}   # bytes: pure


def test_seeded_mutation_sweep_agrees_with_the_pure_loader():
    rng = random.Random(20260928)
    seeds = [t[:600] for t in _corpus().values() if len(t) > 20]
    alphabet = " \n:-#[]{},'\"|>?&*!.\t%\ufeffab01"
    for _ in range(3000):
        text = list(rng.choice(seeds))
        for _ in range(rng.randint(1, 4)):
            i = rng.randrange(len(text) + 1)
            op = rng.random()
            if op < 0.4:
                text.insert(i, rng.choice(alphabet))
            elif i < len(text):
                text[i:i + 1] = [] if op < 0.8 else [rng.choice(alphabet)]
        text = "".join(text)
        assert _outcome(yamlio.load, text) == _outcome(_pure, text), repr(text)


def test_no_warden_or_cage_module_parses_yaml_around_the_loader():
    offenders = [f"{p}:{n}" for p in [*ROOT.glob("warden/**/*.py"), *ROOT.glob("cage/**/*.py")]
                 if p.name != "yamlio.py"
                 for n, line in enumerate(p.read_text().splitlines(), 1)
                 if re.search(r"\byaml\.(\w*load\w*|compose\w*|parse|\w*Loader)\b|getattr\(yaml"
                              r"|from yaml[.\w]* import (?!YAMLError\b)|import yaml as", line.split("#")[0])]
    assert not offenders, offenders
