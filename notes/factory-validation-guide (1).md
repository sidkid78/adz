# Factory Validation Gate CI/CD Setup Guide

This guide details how to implement a **deterministic validation gate** using GitHub Actions to complete the closed-loop autonomous engineering cycle.

When your Software Factory Router agent (running via `factory_router.py`) opens a Pull Request, this workflow intercepts the code changes, runs strict validation scripts (linters, type-checkers, and unit tests), and—crucially—**posts failure logs back to the Factory Router on failure** so the agent can autonomously resolve any issues.

---

## 1. The Validation Gate Workflow (`.github/workflows/factory-validation.yml`)

Save the following YAML file inside your repository at `.github/workflows/factory-validation.yml`:

```yaml
name: Factory Validation Gate

on:
  pull_request:
    branches: [ main, master, dev ]
    types: [ opened, synchronize, reopened ]

jobs:
  validate-agent-pr:
    name: Lint, Type-Check, and Test
    runs-on: ubuntu-latest
    # Focus only on branches created by autonomous factory agents
    if: startsWith(github.head_ref, 'agent/') || startsWith(github.head_ref, 'factory/')

    steps:
      - name: Checkout Code Base
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up Python Runtime
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install Astral UV & Synchronize Dependencies
        run: |
          curl -LsSf https://astral.sh/uv/install.sh | sh
          echo "$HOME/.local/bin" >> $GITHUB_PATH
          uv sync

      - name: Run Linter (Ruff)
        id: lint
        run: |
          uv run ruff check .

      - name: Run Static Type Checker (Mypy)
        id: typecheck
        run: |
          uv run mypy .

      - name: Run Test Suite (Pytest)
        id: test
        run: |
          uv run pytest --junitxml=test-results.xml

      - name: Notify Factory Router on CI Failure
        if: failure()
        env:
          ROUTER_WEBHOOK_URL: ${{ secrets.FACTORY_ROUTER_WEBHOOK_URL }}
          ROUTER_SECRET: ${{ secrets.FACTORY_ROUTER_SECRET }}
        run: |
          PR_NUMBER="${{ github.event.pull_request.number }}"
          REPO="${{ github.repository }}"
          COMMIT_SHA="${{ github.event.pull_request.head.sha }}"
          BRANCH_NAME="${{ github.head_ref }}"
          RUN_ID="${{ github.run_id }}"
          
          echo "⚠️ CI Validation failed for Agent PR #$PR_NUMBER ($BRANCH_NAME)"
          echo "Sending execution context and logs back to Factory Router..."

          # Payload maps directly back to the autonomous agent's loop to trigger self-repair
          curl -X POST "$ROUTER_WEBHOOK_URL/webhook/ci-failure" \
            -H "Content-Type: application/json" \
            -H "X-Factory-Secret: $ROUTER_SECRET" \
            -d @- <<EOF
            {
              "event": "ci_failure",
              "repository": "$REPO",
              "pr_number": $PR_NUMBER,
              "commit_sha": "$COMMIT_SHA",
              "branch_name": "$BRANCH_NAME",
              "run_id": "$RUN_ID",
              "failed_step": "validation_gate"
            }
          EOF
```

---

## 2. Setting Up the Secrets in GitHub

To allow your repository to talk back to your hosted Factory Router, configure these two secrets in your repository settings (**Settings > Secrets and variables > Actions > Repository secrets**):

1.  `FACTORY_ROUTER_WEBHOOK_URL`: The public-facing endpoint where your router is listening (e.g., `https://router.yourdomain.com`).
2.  `FACTORY_ROUTER_SECRET`: The shared security handshake string matched against the `X-Factory-Secret` header to prevent spoofing.

---

## 3. Completing the "Request, Validate, Resolve" Loop

When a CI run fails, your Factory Router intercepts the payload on `/webhook/ci-failure`, which triggers the following automated cycle:

1.  **Retrieve Logs:** The router uses the GitHub API and the `run_id` to download the specific test failure or linting stdout logs.
2.  **Spin Up Sandbox:** The router provisions an isolated E2B container, checks out the agent's branch (`branch_name`), and writes the failure logs into a scratch workspace.
3.  **Deploy Agent:** A specialized "Self-Improvement/Repair" agent boots up with the instruction: *"Fix the linter/test errors detailed in these logs, run `pytest` locally to confirm the fix, and push the resolved changes back to the origin branch."*
4.  **Re-Run Gate:** The new commit is pushed, automatically triggering the GitHub Actions workflow once again. The loop repeats autonomously until the gate passes!
