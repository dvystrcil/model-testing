# feat: per-user spend caps for Open WebUI

## Background

Anthropic and other paid model providers are billed per token. As more household
accounts use the OWUI instance against paid connections, a single runaway chat
can spend disproportionately. We want a per-user monthly budget so cost stays
bounded and predictable — part of the broader optionality thesis (homelab#292).

## Approach

Implement enforcement as a **patch to OWUI core** (`open-webui` fork): add a
budget check in the request-handling path in `backend/apps/openai/main.py`, plus
a migration for a `user_budget` table. Rebuild the core image on each change.

## Pieces

1. **Budget store** — a `user_budget` table (user_id, month, limit_usd, spent_usd).
2. **Request-time enforcement** — reject (HTTP 402) when the user is over budget.
3. **Admin usage view** — a new admin page rendering per-user spend for the month.

## Acceptance Criteria

- [ ] AC1: `user_budget` table + migration; spend accrues per request.
- [ ] AC2: Requests over the monthly cap are rejected with a clear 402.
- [ ] AC3: Admin usage page renders per-user monthly spend, sortable by spend.

## Notes

Core-patch approach chosen for directness; revisit if it complicates rebases
against upstream.
