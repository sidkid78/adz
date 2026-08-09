#!/bin/bash
# check.sh — STEP 1: deterministic gate
#
# This script has NO IDEA an LLM exists. It would produce the exact same
# output whether a human, Gemini, GPT, or a monkey with a keyboard wrote
# target_code.py. That's what makes it "deterministic" — same input file,
# same result, every time.
#
# Exit code 0 = pass. Anything else = fail.

set -o pipefail

echo "== ruff (lint) =="
ruff check target_code.py || exit 1

echo "== mypy (types) =="
mypy target_code.py || exit 1

echo "== pytest (behavior) =="
python -m pytest test_target_code.py -v || exit 1

echo "All checks passed."