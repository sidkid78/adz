# Zero-Downtime Database Migrations

## When to use this
Use this skill when deploying schema changes, index modifications, or data transformations to production relational databases without interrupting user traffic. Apply it whenever migrations require lock contention mitigation, concurrent index management, or automated abort controls.

## Instructions
1. **Analyze the migration script**: Inspect all pending DDL statements to identify blocking operations, such as adding non-null columns without defaults, table rewrites, unindexed foreign keys, or non-concurrent index operations.
2. **Apply session guardrails**: Prepend migration transactions with aggressive lock and statement timeouts (for example, `SET lock_timeout = '3s'; SET statement_timeout = '30s';`) to ensure locks are released immediately if contested.
3. **Isolate concurrent index tasks**: Extract all `CREATE INDEX` or `DROP INDEX` commands from multi-statement transactions and execute them individually using non-blocking syntax (such as `CREATE INDEX CONCURRENTLY` in PostgreSQL).
4. **Implement expand-and-contract patterns**: Decompose destructive or breaking schema updates into phased deployments (expand schema, backfill asynchronously in batches, switch application reads/writes, contract old columns/tables).
5. **Configure automated rollback triggers**: Instrument the execution harness to trap lock-acquisition timeouts (`55P03`), query timeouts, or deadlock exceptions (`40P01`), triggering an immediate `ROLLBACK` and alerting without retrying blindly.
6. **Pre-flight load check**: Inspect connection counts, replication lag, and current lock queues prior to running each phase; abort execution if the database exceeds predefined load thresholds.
7. **Post-migration verification**: Check the status of new objects (such as ensuring concurrent indexes are not marked `INVALID`), run `ANALYZE` on modified tables if necessary, and confirm application error rates remain baseline.