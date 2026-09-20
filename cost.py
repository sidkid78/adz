"""
cost.py — what a ticket cost, and which number changes a decision

WHY TOKENS AND NOT DOLLARS (MOSTLY)
-----------------------------------
Token counts come from the API and are exact. Prices are a table
somebody has to maintain, and this repo has already been bitten twice by
hardcoded facts that aged — an architecture specifying gemini-1.5, and
another specifying Inngest v3. A stale price table produces confident
wrong dollars, which is worse than no dollars.

So tokens are always recorded and dollars are derived only when a price
is configured for that model. An unpriced model reports its tokens and
no cost, rather than a plausible fiction.

THE FOUR NUMBERS WORTH RECORDING
--------------------------------
Raw per-call token totals are log volume. These four change what the
factory does:

  cost per attempt     — repair has diminishing returns, and this is
                         the evidence for where to cap it. This factory
                         watched a ticket produce the same ten errors on
                         attempts 1, 2 and 3 before burning two more.
  cost by model tier   — if the cheap tier is 0% of spend, the router
                         is not routing. ROUTES maps complexity to
                         model; this says whether that mapping is real.
  failed vs passed     — tokens spent on tickets that never passed. A
                         high ratio is an upstream planning failure, not
                         a model failure: the builder was sent in
                         underspecified.
  cache hit rate       — ChangesetAgent threads previous_interaction_id
                         through a ticket's attempts, so its context
                         should be largely cached. A collapse means the
                         harness is invalidating it between turns.

THOUGHT TOKENS ARE NOT FREE
---------------------------
`total_thought_tokens` is billed and is frequently the largest single
component — a trivial call in this repo measured 3 input, 1 output and
61 thought. Folding them into output hides where the money goes.
"""

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

# USD per 1M tokens. Deliberately empty of guesses: fill in what you are
# actually billed and dated so a stale entry is visible. A model absent
# from this table reports tokens and no cost, which is the honest
# output — an invented price is a confident wrong number.
PRICES: dict[str, dict[str, float]] = {
    # "gemini-3.8-flash": {"input": 0.0, "output": 0.0, "as_of": "YYYY-MM-DD"},
}


@dataclass
class Usage:
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    cached_tokens: int = 0

    @classmethod
    def from_interaction(cls, interaction, model: str) -> "Usage":
        """Read usage off an Interactions response. Never raises: a
        missing counter costs a measurement, not a build."""
        u = getattr(interaction, "usage", None)
        if u is None:
            return cls(model=model)
        def n(name: str) -> int:
            try:
                return int(getattr(u, name, 0) or 0)
            except (TypeError, ValueError):
                return 0
        return cls(
            model=model,
            input_tokens=n("total_input_tokens"),
            output_tokens=n("total_output_tokens"),
            thought_tokens=n("total_thought_tokens"),
            cached_tokens=n("total_cached_tokens"),
        )

    @property
    def billable(self) -> int:
        return self.input_tokens + self.output_tokens + self.thought_tokens

    @property
    def cache_rate(self) -> float:
        return self.cached_tokens / self.input_tokens if self.input_tokens else 0.0

    def cost_usd(self) -> float | None:
        price = PRICES.get(self.model)
        if not price:
            return None
        return round(
            (self.input_tokens - self.cached_tokens) / 1e6 * price.get("input", 0.0)
            + self.cached_tokens / 1e6 * price.get("cached", price.get("input", 0.0)) * 0.1
            + (self.output_tokens + self.thought_tokens) / 1e6 * price.get("output", 0.0),
            6,
        )

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            model=self.model if self.model == other.model else "mixed",
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            thought_tokens=self.thought_tokens + other.thought_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
        )


@dataclass
class Ledger:
    """Per-ticket, per-attempt usage for one run."""
    attempts: list[dict] = field(default_factory=list)
    outcomes: dict[str, bool] = field(default_factory=dict)

    def record(self, ticket: str, attempt: int, phase: str, usage: Usage) -> None:
        entry = {"ticket": ticket, "attempt": attempt, "phase": phase, **asdict(usage)}
        entry["cost_usd"] = usage.cost_usd()
        self.attempts.append(entry)

    def settle(self, ticket: str, passed: bool) -> None:
        self.outcomes[ticket] = passed

    def _sum(self, rows: list[dict]) -> Usage:
        total = Usage()
        for r in rows:
            total = total + Usage(r["model"], r["input_tokens"], r["output_tokens"],
                                  r["thought_tokens"], r["cached_tokens"])
        return total

    def summary(self) -> dict:
        by_ticket: dict[str, list[dict]] = defaultdict(list)
        for row in self.attempts:
            by_ticket[row["ticket"]].append(row)

        by_model: dict[str, Usage] = defaultdict(Usage)
        for row in self.attempts:
            by_model[row["model"]] = by_model[row["model"]] + Usage(
                row["model"], row["input_tokens"], row["output_tokens"],
                row["thought_tokens"], row["cached_tokens"])

        passed_rows = [r for r in self.attempts if self.outcomes.get(r["ticket"])]
        failed_rows = [r for r in self.attempts if self.outcomes.get(r["ticket"]) is False]
        passed, failed = self._sum(passed_rows), self._sum(failed_rows)
        total = passed + failed
        wasted = (failed.billable / total.billable) if total.billable else 0.0

        # Attempt 1 is generation; everything after is repair. The gap
        # between them is the diminishing-returns curve.
        first = self._sum([r for r in self.attempts if r["attempt"] == 1])
        later = self._sum([r for r in self.attempts if r["attempt"] > 1])

        return {
            "tickets": len(by_ticket),
            "attempts": len(self.attempts),
            "total_tokens": total.billable,
            "wasted_ratio": round(wasted, 3),
            "cache_rate": round(total.cache_rate, 3),
            "first_attempt_tokens": first.billable,
            "repair_tokens": later.billable,
            "by_model": {m: {"tokens": u.billable, "cache_rate": round(u.cache_rate, 3),
                             "cost_usd": u.cost_usd()}
                         for m, u in sorted(by_model.items())},
            "by_ticket": {t: {"attempts": len(rows),
                              "tokens": self._sum(rows).billable,
                              "passed": self.outcomes.get(t)}
                          for t, rows in sorted(by_ticket.items())},
        }

    def report(self) -> str:
        s = self.summary()
        if not s["attempts"]:
            return "cost     : no model calls recorded"
        ratio = (f"  ({s['repair_tokens'] / s['first_attempt_tokens']:.1f}x)"
                 if s["first_attempt_tokens"] else "")
        lines = [
            (f"cost     : {s['total_tokens']:,} tokens over {s['attempts']} "
             f"attempt(s), {s['tickets']} ticket(s)"),
            (f"           first attempts {s['first_attempt_tokens']:,} | "
             f"repairs {s['repair_tokens']:,}{ratio}"),
            (f"           cache {s['cache_rate']:.0%} | spent on tickets that "
             f"never passed: {s['wasted_ratio']:.0%}"),
        ]
        for model, m in s["by_model"].items():
            cost = f" (${m['cost_usd']:.2f})" if m["cost_usd"] is not None else ""
            lines.append(f"           {model:26} {m['tokens']:>9,} tokens{cost}")
        return "\n".join(lines)

    def write(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(
                {"summary": self.summary(), "attempts": self.attempts},
                indent=1), encoding="utf-8", newline="\n")
        except OSError:
            pass
