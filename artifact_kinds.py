"""
artifact_kinds.py — what a deliverable can be, and how code decides it passed

The factory used to be able to build exactly one thing: a single Python
module graded by pytest. That is a demo's constraint, not a factory's.
Real tickets ask for integration schemas, field mappings, runbooks,
webhook contracts, KPI definitions — deliverables that are still files,
still reviewable, and still mechanically checkable, just not Python.

So the unit of work is an ARTIFACT (one file) and the unit of judgment
is a CHECK (a deterministic function over that file). A contract is a
kind plus a list of checks. Nothing here calls a model, and no check is
allowed to ask one for an opinion — that is the whole point. A check
returns (passed, detail) and the detail is what gets fed back to the
build agent on failure.

Checks are DATA (`{"type": "...", ...params}`), matching the blueprint
philosophy already in blueprints.py: the workflow is a list of steps to
be interpreted, not hardcoded control flow.

Adding a kind means adding its checks to CHECKS and listing it in KINDS.
Nothing else in the factory needs to know it exists.
"""

import ast
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import yaml

# Text that means "I did not actually do this part". A deliverable
# containing any of it is not done, whatever else it contains.
PLACEHOLDER_PATTERNS = [
    r"\bTODO\b", r"\bTBD\b", r"\bFIXME\b", r"\bXXX\b",
    r"lorem ipsum", r"\[insert[^\]]*\]", r"<placeholder[^>]*>",
    r"\byour[_ ](?:value|key|id)[_ ]here\b", r"\.\.\.\s*$",
]


def _read(artifact: Path) -> str:
    return artifact.read_text(encoding="utf-8", errors="replace")


def _resolve(data: object, dotted: str) -> tuple[bool, object]:
    """Walk a dotted path through parsed JSON/YAML. `a.b` descends keys;
    `a[].b` requires a non-empty list at `a` and descends into every
    element, so a mapping contract can be asserted across a collection."""
    node = data
    for part in dotted.split("."):
        if part.endswith("[]"):
            key = part[:-2]
            if key:
                if not isinstance(node, dict) or key not in node:
                    return False, None
                node = node[key]
            if not isinstance(node, list) or not node:
                return False, None
            node = node[0]  # representative element
            continue
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


# ---- Universal checks -------------------------------------------------
def check_non_empty(artifact: Path, params: dict) -> tuple[bool, str]:
    minimum = params.get("min_bytes", 1)
    size = artifact.stat().st_size if artifact.exists() else 0
    if size < minimum:
        return False, f"artifact is {size} bytes, needs at least {minimum}"
    return True, f"{size} bytes"


def check_min_words(artifact: Path, params: dict) -> tuple[bool, str]:
    count = len(_read(artifact).split())
    minimum = params.get("count", 100)
    if count < minimum:
        return False, f"{count} words, needs at least {minimum}"
    return True, f"{count} words"


def check_no_placeholders(artifact: Path, params: dict) -> tuple[bool, str]:
    text = _read(artifact)
    hits = []
    for pattern in PLACEHOLDER_PATTERNS + params.get("extra", []):
        for m in re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE):
            line = text[: m.start()].count("\n") + 1
            hits.append(f"line {line}: {m.group(0)!r}")
    if hits:
        return False, "unfilled placeholders: " + "; ".join(hits[:6])
    return True, "no placeholder text"


def check_regex_present(artifact: Path, params: dict) -> tuple[bool, str]:
    pattern = params["pattern"]
    if not re.search(pattern, _read(artifact), re.IGNORECASE | re.MULTILINE):
        return False, f"required pattern not found: {params.get('description') or pattern}"
    return True, f"found {params.get('description') or pattern}"


def check_regex_absent(artifact: Path, params: dict) -> tuple[bool, str]:
    pattern = params["pattern"]
    if re.search(pattern, _read(artifact), re.IGNORECASE | re.MULTILINE):
        return False, f"forbidden pattern present: {params.get('description') or pattern}"
    return True, "forbidden pattern absent"


# ---- Markdown checks --------------------------------------------------
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.MULTILINE)


def _headings(text: str) -> list[tuple[int, str]]:
    return [(len(m.group(1)), m.group(2)) for m in _HEADING_RE.finditer(text)]


def check_required_sections(artifact: Path, params: dict) -> tuple[bool, str]:
    """Every named section must exist as a heading. Matching is on
    normalised substring so the model isn't punished for 'Data Flow
    Mapping' vs '3. Data Flow Mapping'."""
    text = _read(artifact)
    present = [h.lower() for _, h in _headings(text)]
    missing = [
        want for want in params["sections"]
        if not any(want.lower() in got for got in present)
    ]
    if missing:
        return False, f"missing required section(s): {', '.join(missing)}"
    return True, f"all {len(params['sections'])} required sections present"


def check_min_words_per_section(artifact: Path, params: dict) -> tuple[bool, str]:
    """Catches the outline-instead-of-content failure: every heading is
    there, but the body under each is one sentence."""
    text = _read(artifact)
    minimum = params.get("count", 40)
    level = params.get("level", 2)
    matches = [m for m in _HEADING_RE.finditer(text) if len(m.group(1)) == level]
    if not matches:
        return False, f"no level-{level} headings found"

    thin = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end]
        words = len(body.split())
        if words < minimum:
            thin.append(f"{m.group(2)!r} ({words}w)")
    if thin:
        return False, f"section(s) below {minimum} words: {'; '.join(thin[:6])}"
    return True, f"all {len(matches)} sections >= {minimum} words"


_FENCE_RE = re.compile(r"^```([a-zA-Z0-9_+-]*)\n(.*?)^```", re.MULTILINE | re.DOTALL)


def check_fenced_blocks_parse(artifact: Path, params: dict) -> tuple[bool, str]:
    """A json/yaml/python code fence that doesn't parse is a defect, and
    a very common one in model-written documents."""
    text = _read(artifact)
    checked = 0
    for m in _FENCE_RE.finditer(text):
        lang, body = m.group(1).lower(), m.group(2)
        line = text[: m.start()].count("\n") + 1
        try:
            if lang == "json":
                json.loads(body); checked += 1
            elif lang in ("yaml", "yml"):
                yaml.safe_load(body); checked += 1
            elif lang in ("python", "py"):
                ast.parse(body); checked += 1
        except Exception as exc:  # noqa: BLE001 - any parse failure is the finding
            return False, f"{lang} block at line {line} does not parse: {exc}"
    return True, f"{checked} parseable code block(s)"


def check_min_table_rows(artifact: Path, params: dict) -> tuple[bool, str]:
    """Mapping/field-definition tickets are worthless without the rows."""
    rows = [ln for ln in _read(artifact).splitlines() if ln.strip().startswith("|")]
    body = max(0, len(rows) - 2)  # header + separator
    minimum = params.get("count", 3)
    if body < minimum:
        return False, f"{body} table row(s), needs at least {minimum}"
    return True, f"{body} table rows"


# ---- Structured-data checks -------------------------------------------
def _load_structured(artifact: Path, as_yaml: bool):
    text = _read(artifact)
    return yaml.safe_load(text) if as_yaml else json.loads(text)


def check_json_parses(artifact: Path, params: dict) -> tuple[bool, str]:
    try:
        json.loads(_read(artifact))
    except json.JSONDecodeError as exc:
        return False, f"invalid JSON: {exc}"
    return True, "valid JSON"


def check_yaml_parses(artifact: Path, params: dict) -> tuple[bool, str]:
    try:
        yaml.safe_load(_read(artifact))
    except yaml.YAMLError as exc:
        return False, f"invalid YAML: {exc}"
    return True, "valid YAML"


def _required_keys(artifact: Path, params: dict, as_yaml: bool) -> tuple[bool, str]:
    try:
        data = _load_structured(artifact, as_yaml)
    except Exception as exc:  # noqa: BLE001 - parse failure reported as the finding
        return False, f"could not parse: {exc}"
    missing = [k for k in params["keys"] if not _resolve(data, k)[0]]
    if missing:
        return False, f"missing required key(s): {', '.join(missing)}"
    return True, f"all {len(params['keys'])} required keys present"


def check_json_required_keys(artifact: Path, params: dict) -> tuple[bool, str]:
    return _required_keys(artifact, params, as_yaml=False)


def check_yaml_required_keys(artifact: Path, params: dict) -> tuple[bool, str]:
    return _required_keys(artifact, params, as_yaml=True)


def check_min_collection_size(artifact: Path, params: dict) -> tuple[bool, str]:
    """The structured-data equivalent of min_table_rows: a schema with an
    empty `fields` list has not been filled in."""
    as_yaml = params.get("format", "json") in ("yaml", "yml")
    try:
        data = _load_structured(artifact, as_yaml)
    except Exception as exc:  # noqa: BLE001
        return False, f"could not parse: {exc}"
    path = params["path"]
    found, node = _resolve(data, path) if path else (True, data)
    if not found:
        return False, f"path not found: {path}"
    if not isinstance(node, (list, dict)):
        return False, f"{path} is {type(node).__name__}, expected list or object"
    minimum = params.get("count", 3)
    if len(node) < minimum:
        return False, f"{path} has {len(node)} entries, needs at least {minimum}"
    return True, f"{path} has {len(node)} entries"


# ---- Python checks ----------------------------------------------------
def _run_tool(args: list[str], cwd: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, *args], cwd=cwd, capture_output=True, text=True, check=False,
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def check_ruff(artifact: Path, params: dict) -> tuple[bool, str]:
    return _run_tool(["-m", "ruff", "check", artifact.name], artifact.parent)


def check_mypy(artifact: Path, params: dict) -> tuple[bool, str]:
    return _run_tool(["-m", "mypy", artifact.name], artifact.parent)


def check_pytest_suite(artifact: Path, params: dict) -> tuple[bool, str]:
    """The test file is written next to the artifact by the executor, from
    the frozen contract, immediately before this runs."""
    return _run_tool(["-m", "pytest", params["test_filename"], "-v"], artifact.parent)


# ---- Registry ---------------------------------------------------------
CheckFn = Callable[[Path, dict], tuple[bool, str]]

CHECKS: dict[str, CheckFn] = {
    "non_empty": check_non_empty,
    "min_words": check_min_words,
    "no_placeholders": check_no_placeholders,
    "regex_present": check_regex_present,
    "regex_absent": check_regex_absent,
    "required_sections": check_required_sections,
    "min_words_per_section": check_min_words_per_section,
    "fenced_blocks_parse": check_fenced_blocks_parse,
    "min_table_rows": check_min_table_rows,
    "json_parses": check_json_parses,
    "yaml_parses": check_yaml_parses,
    "json_required_keys": check_json_required_keys,
    "yaml_required_keys": check_yaml_required_keys,
    "min_collection_size": check_min_collection_size,
    "ruff": check_ruff,
    "mypy": check_mypy,
    "pytest_suite": check_pytest_suite,
}

# Per-kind metadata: the file extension, which checks the contract author
# may use, and the checks always applied whether it asks for them or not.
KINDS: dict[str, dict] = {
    "python_module": {
        "extension": ".py",
        "description": "a single self-contained Python module",
        "allowed": ["ruff", "mypy", "pytest_suite", "regex_present", "regex_absent"],
        "mandatory": [{"type": "non_empty", "min_bytes": 40}],
        "needs_test_code": True,
    },
    "markdown_document": {
        "extension": ".md",
        "description": (
            "a written deliverable: integration architecture, runbook, SOP, "
            "field-mapping document, rollout plan, escalation policy"
        ),
        "allowed": [
            "required_sections", "min_words_per_section", "min_words",
            "fenced_blocks_parse", "min_table_rows", "regex_present", "regex_absent",
        ],
        "mandatory": [
            {"type": "non_empty", "min_bytes": 400},
            {"type": "no_placeholders"},
            {"type": "fenced_blocks_parse"},
        ],
        "needs_test_code": False,
    },
    "json_document": {
        "extension": ".json",
        "description": (
            "a machine-readable definition: webhook payload contract, field map, "
            "KPI definition set, API request/response schema, config export"
        ),
        "allowed": ["json_required_keys", "min_collection_size", "regex_present", "regex_absent"],
        "mandatory": [{"type": "non_empty", "min_bytes": 80}, {"type": "json_parses"}, {"type": "no_placeholders"}],
        "needs_test_code": False,
    },
    "yaml_config": {
        "extension": ".yaml",
        "description": "a configuration file: pipeline definition, workflow config, deployment manifest",
        "allowed": ["yaml_required_keys", "min_collection_size", "regex_present", "regex_absent"],
        "mandatory": [{"type": "non_empty", "min_bytes": 80}, {"type": "yaml_parses"}, {"type": "no_placeholders"}],
        "needs_test_code": False,
    },
}


def run_checks(artifact: Path, checks: list[dict]) -> tuple[bool, str]:
    """Run checks in order, short-circuiting on the first failure. The
    transcript is what the build agent sees, so it names the check that
    failed and why, not just 'failed'."""
    transcript = []
    for spec in checks:
        name = spec["type"]
        fn = CHECKS.get(name)
        if fn is None:
            transcript.append(f"== {name} ==\nUNKNOWN CHECK (ignored)")
            continue
        try:
            passed, detail = fn(artifact, spec)
        except Exception as exc:  # noqa: BLE001 - a crashing check is a failure, not a pass
            passed, detail = False, f"check raised {type(exc).__name__}: {exc}"
        transcript.append(f"== {name} ==\n{detail}")
        if not passed:
            transcript.append(f"\nFAILED on '{name}'. Fix this and return the corrected artifact.")
            return False, "\n".join(transcript)
    transcript.append("\nAll checks passed.")
    return True, "\n".join(transcript)
