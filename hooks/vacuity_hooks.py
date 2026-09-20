"""
vacuity_hooks.py — refuse evidence that was minted to order

THE FAILURE THIS EXISTS FOR
---------------------------
The integration gate told four tickets to fix 14 unreachable modules.
None of them owned an entry point, so they manufactured one:

    // tests/reachability.test.ts
    import "../src/components/feed/video-card";
    import "../src/lib/supabase/server";        // …16 of these
    test("all previously unreachable modules load cleanly", () => {
      expect(true).toBe(true);
    });

plus src/index.ts re-exporting the same modules. Reachability went
green. The app still had no page.tsx, `/` still returned 404, and not
one of those modules had become reachable by any user.

Nothing the agents did was dishonest — they solved the problem as
stated. The check asked "is this imported from an entry point", and a
test file is an entry point, so a test file was written. The defect was
in the question.

TWO LAYERS, AND THIS IS THE SECOND
----------------------------------
The first layer is permission: `TargetRepo.write_files(allow_new_tests=
False)` stops a repair from creating a new test at all. That is cheaper
and stricter, and it needs no parsing.

This module is for the tests a builder legitimately writes. It cannot
stop an agent from writing a bad test; it makes a bad test cost a turn
instead of buying a pass.

WHERE EACH CHECK BELONGS
------------------------
PRE_TOOL_USE, blocking — the vacuous barrel. A file that only
re-exports has nothing worth keeping, and once on disk it pollutes the
module graph for every later check. There is nothing to repair, so
refuse the write.

POST_TOOL_USE, feedback — trivial assertions and assertion-free test
files. Self-repair works better when the file exists and the agent can
see what it wrote. The feedback is spliced into its next turn.

DELIBERATELY SYNTACTIC FOR TYPESCRIPT
-------------------------------------
Python gets a real `ast` walk. TypeScript gets regexes, the same trade
`reachability.py` makes: a Node subprocess and a `typescript` dependency
is a lot of machinery for a cheap guard, and it fails closed when the
parser is missing. An exotic form may slip past; it fails SAFE (a file
is called fine when in doubt, never vacuous).
"""

import ast
import re
from pathlib import Path

try:
    from hooks.hook_bus import HookContext, HookDecision, HookEvent
except ImportError:  # launched from inside hooks/
    from hook_bus import HookContext, HookDecision, HookEvent

WRITE_TOOLS = ("write_file", "write_files", "edit_file", "create_file", "Write", "Edit")

_TEST_NAME = re.compile(r"(^|[./])(test_|.*\.test\.|.*\.spec\.)", re.IGNORECASE)
_BARREL_NAME = ("index.ts", "index.tsx", "index.js", "index.jsx", "__init__.py")


def _is_test_path(path: str) -> bool:
    name = Path(path).name
    return bool(_TEST_NAME.search(name)) or name.startswith("test_")


# ---------------------------------------------------------------- Python


def _const_eq(a: ast.expr, b: ast.expr) -> bool:
    """Both sides the same literal — `1 == 1`, `"a" == "a"`."""
    return (isinstance(a, ast.Constant) and isinstance(b, ast.Constant)
            and a.value == b.value)


def inspect_python(content: str, path: str) -> dict:
    """Trivial assertions, assertion-free tests, vacuous __init__."""
    out = {"trivial": [], "import_only_test": False, "vacuous_barrel": False}
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return out  # a syntax error is the linter's problem, not ours

    imports = assertions = 0

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports += 1

        elif isinstance(node, ast.Assert):
            assertions += 1
            test = node.test
            if isinstance(test, ast.Constant) and bool(test.value):
                out["trivial"].append(f"line {node.lineno}: `assert {test.value!r}` is always true")
            # NOTE: `comparators` is a LIST. Indexing it is the whole
            # check — `isinstance(test.comparators, ast.Constant)` is
            # always False, so a version that forgets the [0] reports
            # clean on `assert 1 == 1` forever.
            elif (isinstance(test, ast.Compare) and len(test.comparators) == 1
                    and _const_eq(test.left, test.comparators[0])):
                out["trivial"].append(
                    f"line {node.lineno}: `assert {ast.unparse(test)}` compares a literal to itself")

        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name = node.func.attr
            if not name.startswith("assert"):
                continue
            assertions += 1
            args = node.args  # also a list; same trap
            if (name in ("assertTrue", "assertIs", "assertIsNotNone") and args
                    and isinstance(args[0], ast.Constant) and bool(args[0].value)):
                out["trivial"].append(f"line {node.lineno}: `{name}({args[0].value!r})` is always true")
            elif name == "assertEqual" and len(args) == 2 and _const_eq(args[0], args[1]):
                out["trivial"].append(f"line {node.lineno}: `{name}` compares a literal to itself")

    if _is_test_path(path) and imports > 0 and assertions == 0:
        out["import_only_test"] = True

    if Path(path).name == "__init__.py":
        reexports = sum(isinstance(n, (ast.Import, ast.ImportFrom)) for n in tree.body)
        out["vacuous_barrel"] = reexports > 0 and not _does_work_py(tree.body)

    return out


def _does_work_py(body: list[ast.stmt]) -> bool:
    """Is there a statement here that is not an import or a stub?

    `__all__ = [...]` and `VERSION = "1"` are bookkeeping, not work. A
    barrel is vacuous when nothing else is present — NOT when every
    statement is a re-export, which is a rule one stray constant
    defeats.
    """
    for node in body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # docstring
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if value is None or isinstance(value, ast.Constant):
                continue
            if isinstance(value, (ast.List, ast.Tuple)) and all(
                    isinstance(e, ast.Constant) for e in value.elts):
                continue  # __all__ = ["a", "b"]
        return True
    return False


# ------------------------------------------------------------ TypeScript

_TS_TRIVIAL = re.compile(
    r"""expect\(\s*(?P<a>true|false|1|0|null|undefined|(['"])[^'"]*\2)\s*\)\s*\.\s*"""
    r"""(?:to(?:Be|Equal|StrictEqual)\(\s*(?P<b>true|false|1|0|null|undefined|(['"])[^'"]*\4)\s*\)"""
    r"""|toBeTruthy\(\s*\)|toBeDefined\(\s*\))""",
    re.IGNORECASE,
)
_TS_IMPORT = re.compile(r"^\s*import\s", re.MULTILINE)
_TS_EXPECT = re.compile(r"\bexpect\s*\(|\bassert\s*[.(]", re.IGNORECASE)
_TS_REEXPORT = re.compile(r"^\s*export\s+(?:\*|\{[^}]*\})\s+from\s", re.MULTILINE)


def inspect_typescript(content: str, path: str) -> dict:
    out = {"trivial": [], "import_only_test": False, "vacuous_barrel": False}

    for m in _TS_TRIVIAL.finditer(content):
        a, b = m.group("a"), m.group("b")
        # expect(true).toBe(true) / expect(1).toBe(1): only trivial when
        # both sides are the same literal, or there is no argument at all
        # (toBeTruthy on a literal).
        if b is None or a == b:
            line = content[: m.start()].count("\n") + 1
            out["trivial"].append(f"line {line}: `{m.group(0)}` asserts a literal against itself")

    if _is_test_path(path) and _TS_IMPORT.search(content) and not _TS_EXPECT.search(content):
        out["import_only_test"] = True

    if (Path(path).name in _BARREL_NAME and _TS_REEXPORT.search(content)
            and not _does_work_ts(content)):
        out["vacuous_barrel"] = True

    return out


def _does_work_ts(content: str) -> bool:
    """Any statement that is not a re-export, an import, or a stub const.

    `export const PLACEHOLDER = true;` is not work. The real barrel that
    slipped through carried exactly that one line beside fifteen
    re-exports, which is enough to defeat any "all statements are
    re-exports" rule.
    """
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith(("//", "/*", "*", "*/")):
            continue
        if _TS_REEXPORT.match(raw) or line.startswith("import "):
            continue
        if re.match(r"^export\s+(?:const|let|var)\s+\w+\s*(?::[^=]+)?=\s*"
                    r"(?:true|false|\d+|null|undefined|['\"][^'\"]*['\"])\s*;?$", line):
            continue  # stub constant
        if line in ("export {};", "export {}"):
            continue
        return True
    return False


def inspect(content: str, path: str) -> dict:
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return inspect_python(content, path)
    if suffix in (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts"):
        return inspect_typescript(content, path)
    return {"trivial": [], "import_only_test": False, "vacuous_barrel": False}


# ------------------------------------------------------------------ hooks


def _payload(ctx: HookContext) -> tuple[str, str] | None:
    if ctx.tool_name not in WRITE_TOOLS:
        return None
    args = ctx.tool_args or {}
    path = args.get("path") or args.get("file_path") or args.get("rel")
    content = args.get("content") or args.get("text")
    return (path, content) if path and content else None


def block_vacuous_barrel(ctx: HookContext) -> HookDecision | None:
    """PRE_TOOL_USE: a file that only re-exports never reaches disk."""
    if ctx.event is not HookEvent.PRE_TOOL_USE:
        return None
    payload = _payload(ctx)
    if not payload:
        return None
    path, content = payload
    if Path(path).name not in _BARREL_NAME:
        return None

    if inspect(content, path)["vacuous_barrel"]:
        return HookDecision(
            block=True,
            reason=(
                f"refusing to write {path}: it only re-exports other modules. "
                "A barrel does not make anything reachable — whatever imports "
                "the barrel is what matters, and nothing imports this. If these "
                "modules need to be used, import them where they are used; if "
                "the entry point that should own them does not exist yet, that "
                "missing entry point is the actual work."
            ),
        )
    return None


def reject_vacuous_tests(ctx: HookContext) -> HookDecision | None:
    """POST_TOOL_USE: a test that asserts nothing costs a turn."""
    if ctx.event is not HookEvent.POST_TOOL_USE:
        return None
    payload = _payload(ctx)
    if not payload:
        return None
    path, content = payload

    found = inspect(content, path)
    notes: list[str] = []

    if found["trivial"]:
        notes.append(
            f"{path} contains assertions that cannot fail:\n  "
            + "\n  ".join(found["trivial"])
            + "\nAssert on a real return value instead. An assertion that holds "
              "for every possible implementation tests nothing."
        )
    if found["import_only_test"]:
        notes.append(
            f"{path} imports modules and asserts nothing. Importing a module "
            "proves it parses; it does not test it, and it does not make it "
            "reachable by anything a user can run. Either assert on the "
            "behaviour of what you imported, or delete this file and wire "
            "those modules into the entry point that should own them."
        )

    return HookDecision(feedback="\n\n".join(notes)) if notes else None


HOOKS = {
    HookEvent.PRE_TOOL_USE: [block_vacuous_barrel],
    HookEvent.POST_TOOL_USE: [reject_vacuous_tests],
}


if __name__ == "__main__":
    import sys

    for target in sys.argv[1:]:
        p = Path(target)
        found = inspect(p.read_text(encoding="utf-8", errors="replace"), target)
        flags = [k for k in ("import_only_test", "vacuous_barrel") if found[k]]
        if found["trivial"]:
            flags.append(f"{len(found['trivial'])} trivial assertion(s)")
        print(f"{target}: {', '.join(flags) if flags else 'ok'}")
        for line in found["trivial"]:
            print(f"    {line}")
