"""
visual_review.py — the one question no exit code can answer

WHY THIS EXISTS WHEN EVERYTHING ELSE IS DETERMINISTIC
-----------------------------------------------------
This repo's rule is that code decides pass/fail, never the agent, and
nothing here changes that. Every defect worth catching mechanically
should be caught mechanically: a linter, a typechecker, an AST hook, an
HTTP status code. Spending tokens on a model's opinion about something
`ruff` already knows is slower, costlier and less reliable.

But one class of defect is structurally invisible to all of it, and this
factory has shipped it:

    Tailwind was never installed while every ticket wrote
    Tailwind-classed components. tsc, vitest and `next build` were all
    perfectly happy — a class name that resolves to nothing is valid
    TypeScript. The app rendered as unstyled HTML with page-sized SVG
    icons, and a HUMAN noticed.

`runtime_proof.py` narrowed that gap: it boots the server and asserts
200 with a non-trivial body. It still cannot tell you the text is white
on white, that a button covers the heading, or that the layout collapses
into a column of giant icons. Those are true of a page that returns 200
and contains every element a selector could ask for.

So: a vision model looks at the screenshot.

ADVISORY, NEVER AN OVERRIDE
---------------------------
This returns findings. It cannot pass a build that a gate failed, and
`run_review()` never reports success on behalf of another step. The
moment a model's opinion can overturn an exit code, "code decides
pass/fail" is gone — and that rule is the only reason the failures this
module exists for were ever visible.

The inverse is allowed: a CRITICAL finding here can fail a build that
every mechanical gate passed. That asymmetry is the point. A model
saying "looks fine" proves nothing; a model saying "the text is
unreadable" is worth acting on, and a human can check the screenshot in
one glance.

CONTEXT ECONOMY
---------------
It reads the ticket and the image. It never reads application source.
Guessing at CSS from a screenshot is exactly the failure mode that makes
model review noisy, and the source is what the mechanical gates already
cover.
"""

import base64
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

VISION_MODEL = "gemini-3.8-flash"

SYSTEM_INSTRUCTION = """\
You are a visual proof-of-work auditor for generated web applications.

You judge ONE thing: does this screenshot show a page a real user could
use? You are the last check after the compiler, the tests, the build and
an HTTP 200 have all passed, so assume the code is syntactically fine.
What you are looking for is what those cannot see.

Mark a finding CRITICAL only for defects that make the page unusable:
  - text that cannot be read (white on white, dark on dark)
  - elements overlapping so that content or controls are obscured
  - content clipped out of the viewport with no way to reach it
  - a slot that rendered nothing, or broken-image placeholders
  - an obviously unstyled page: raw HTML with default serif text,
    bullet lists where cards were intended, or icons rendered at
    hundreds of pixels because a CSS framework never loaded

Mark WARNING for real but survivable problems: cramped spacing,
misaligned columns, inconsistent sizing.

Do NOT report: sub-pixel alignment, anti-aliasing, font-rendering
differences, subjective colour preference, or anything you would have to
inspect the CSS to be sure about. A noisy reviewer gets ignored, and an
ignored reviewer is worse than none.

Placeholder or empty-state content is NOT a defect. Generated apps often
run against an empty database; an empty feed that says so is working
correctly.

Reply with a single JSON object and nothing else:

{"status": "PASS" | "FAIL",
 "summary": "one sentence",
 "findings": [
   {"severity": "CRITICAL" | "WARNING",
    "defect": "CONTRAST | OVERLAP | TRUNCATION | MISSING | UNSTYLED | LAYOUT",
    "description": "what is wrong, and where on the page",
    "hint": "what to change"}
 ]}

status is FAIL if and only if there is at least one CRITICAL finding.
"""


@dataclass
class VisualReview:
    status: str = "SKIPPED"
    summary: str = ""
    findings: list[dict] = field(default_factory=list)
    raw: str = ""

    @property
    def critical(self) -> list[dict]:
        return [f for f in self.findings if f.get("severity") == "CRITICAL"]

    @property
    def failed(self) -> bool:
        """Only a CRITICAL finding fails. A model's PASS means nothing."""
        return bool(self.critical)

    def report(self) -> str:
        if self.status == "SKIPPED":
            return f"visual review skipped: {self.summary}"
        lines = [f"visual review: {self.status} — {self.summary}"]
        for f in self.findings:
            lines.append(f"  [{f.get('severity', '?')}] {f.get('defect', '?')}: "
                         f"{f.get('description', '')}")
            if f.get("hint"):
                lines.append(f"      fix: {f['hint']}")
        return "\n".join(lines)


def _extract_json(text: str) -> dict | None:
    """Models fence JSON even when told not to."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(candidate[start:end + 1])
    except json.JSONDecodeError:
        return None


def review_screenshot(image_path: Path, intent: str,
                      model: str = VISION_MODEL) -> VisualReview:
    """Ask a vision model whether this page looks usable.

    `intent` is the ticket's own description of what the page is for —
    the only context needed to tell "empty feed, correctly showing an
    empty state" from "feed failed to render".
    """
    if not image_path.exists():
        return VisualReview(summary=f"no screenshot at {image_path}")
    if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
        return VisualReview(summary="no API key; visual review needs one")

    try:
        from google import genai
    except ImportError:
        return VisualReview(summary="google-genai not installed")

    client = genai.Client(
        api_key=os.environ.get("GOOGLE_API_KEY") or os.environ["GEMINI_API_KEY"])
    # The Interactions API takes multimodal input as plain dicts with
    # base64 payloads — NOT types.Part objects, which it rejects with a
    # hundred-line pydantic union error that names every branch except
    # the one you want.
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")

    try:
        interaction = client.interactions.create(
            model=model,
            system_instruction=SYSTEM_INSTRUCTION,
            input=[
                {"type": "image", "data": encoded, "mime_type": "image/png"},
                {"type": "text",
                 "text": f"This page is meant to be:\n{intent.strip()[:2000]}\n\n"
                         f"Audit the screenshot."},
            ],
        )
        raw = interaction.output_text or ""
    except Exception as exc:  # noqa: BLE001 - review must never fail a build by erroring
        return VisualReview(summary=f"visual review errored: {type(exc).__name__}: {exc}"[:300])

    parsed = _extract_json(raw)
    if not parsed:
        return VisualReview(summary="model returned no parseable JSON", raw=raw[:2000])

    findings = [f for f in parsed.get("findings", []) if isinstance(f, dict)]
    # The model's own status is not trusted: recompute from the findings
    # so "PASS" with a CRITICAL attached cannot slip through.
    status = "FAIL" if any(f.get("severity") == "CRITICAL" for f in findings) else "PASS"
    return VisualReview(
        status=status,
        summary=str(parsed.get("summary", ""))[:400],
        findings=findings,
        raw=raw[:4000],
    )


def review_repo(repo_path: Path, intent: str = "", model: str = VISION_MODEL) -> VisualReview:
    """Review the proof-of-work screenshot runtime_proof.py leaves behind."""
    shot = repo_path / "artifacts" / "proof-of-work.png"
    if not shot.exists():
        return VisualReview(summary="no artifacts/proof-of-work.png (runtime proof did not capture one)")
    if not intent:
        readme = repo_path / "README.md"
        intent = readme.read_text(encoding="utf-8", errors="replace")[:1500] if readme.exists() \
            else "a working web application home page"
    return review_screenshot(shot, intent, model=model)


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    result = review_repo(target, " ".join(sys.argv[2:]))
    print(result.report())
    # Exit non-zero only on a CRITICAL finding. A skipped or errored
    # review is not a failure: this is advisory, and an advisory check
    # that breaks the build when the API is down is a liability.
    raise SystemExit(1 if result.failed else 0)
