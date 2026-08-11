---
scope: "db/**"
---
- All queries must use parameterized statements — never string-format SQL.
- Every new table needs a migration file, never a hand-run ALTER.
- Foreign keys must have an explicit ON DELETE policy, no implicit defaults.