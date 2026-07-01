# Task 04: Manifest authoring

- **Cluster:** code & cluster
- **Fixtures:** none
- **Dimensions:** correctness, faithfulness, format, memory

## Prompt

Write a Kubernetes CronJob that runs `bin/seed-homelab-memory.py` once a day at
06:00. It needs a ServiceAccount, the Postgres connection secret mounted from
Infisical, resource requests/limits, and the annotations the homelab uses so
ArgoCD and image-updater treat it correctly. Give me the full manifest(s).

## Ground truth / rubric

Homelab-specific conventions the manifest must honor (only in memory/repos):
- **Secret** via **InfisicalSecret CR** — never a literal `stringData:`/`data:`
  Secret in the repo (`secrets-hygiene` / `homelab-secrets`).
- **Resource requests AND limits** present — unbounded pods are node-killers
  (`feedback_unbounded_pods_are_node_killers`); `resources: {}` is a fail.
- **Image reference** managed by an **ImageUpdater CRD**, not raw annotations
  (`feedback_image_updater_crd_not_annotations`) — if it pins an image, it should
  note the IU CR governs the tag.
- Runs in the app's namespace with a dedicated ServiceAccount.
- Schedule `0 6 * * *`.

Traps:
- A literal `Secret` with base64 data → hard faithfulness fail.
- `resources: {}` or missing limits → correctness fail.
- `imagePullPolicy: Always` on `:latest` with legacy IU annotations → convention fail.

## Scoring

- **correctness (0–3):** 3 = valid CronJob, right schedule, SA + secret mount + resources all present; 1 = missing one required piece; 0 = invalid.
- **faithfulness (0–3):** 3 = InfisicalSecret + bounded resources + IU-aware; 1 = one convention violated; 0 = literal secret in repo.
- **format (auto):** parses as YAML; contains `kind: CronJob`, `schedule:`, `resources:` with both `requests` and `limits`.
- **memory (0–3):** 3 = ≥3 homelab conventions applied; 0 = generic k8s manifest.
