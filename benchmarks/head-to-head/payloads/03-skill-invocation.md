# Task 03: Skill invocation

- **Cluster:** code & cluster
- **Fixtures:** none
- **Dimensions:** correctness, faithfulness, memory, tool_coherence

## Prompt

Back up the Infisical Postgres before its 1.7.2 → 1.8.0 bump. Pick the right
approach and produce the exact command(s) you'd run, with the correct namespace,
cluster name, and any label selectors. Don't actually run anything — show me the
plan and the commands.

## Ground truth / rubric

The correct move is the `pgo-pre-upgrade-backup` skill (verified, durable PGO
backup before an upgrade) — NOT a plain `pgbackrest backup`, because the skill
confirms the backup landed on NFS and is restorable before returning.

Faithfulness / homelab-specific facts (only in memory + cluster):
- Infisical runs a Crunchy PGO PostgresCluster (SQLite→PGO default pattern).
- The command targets the PGO cluster CR / repo-host pod in Infisical's namespace,
  using the PGO label selectors (`postgres-operator.crunchydata.com/cluster=...`,
  `.../role=pgha-repo-host` or a `pgbackrest backup` annotation on the CR).
- Should mention verifying restorability, not just triggering the backup.

Traps:
- Recommending a generic `pg_dump` (wrong tool for a PGO cluster) → low correctness.
- Inventing a namespace/cluster name without hedging → low faithfulness (bonus if
  it says "confirm the exact cluster name with `kubectl get postgrescluster -A`").

## Scoring

- **correctness (0–3):** 3 = names `pgo-pre-upgrade-backup` and the PGO-native path; 1 = generic backup; 0 = wrong/destructive.
- **faithfulness (0–3):** 3 = PGO/Infisical-accurate, hedges unknown names; 0 = confidently wrong cluster facts.
- **memory (0–3):** 3 = cites the skill + PGO-default + NFS-verify rationale; 0 = none of the homelab specifics.
- **tool_coherence (0–3):** 3 = a sensible ordered plan (identify cluster → trigger → verify restorable); 0 = incoherent.
