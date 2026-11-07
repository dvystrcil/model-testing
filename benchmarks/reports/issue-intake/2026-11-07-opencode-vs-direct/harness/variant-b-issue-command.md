---
description: Turn an idea or todo into a researched homelab GitHub issue
agent: issue-writer
---
<!-- Variant B as run 2026-11-07 (the agent-loop version). Never committed to
homelab: replaced by the thin draft-issue.py wrapper before the first commit.
Agent: homelab/opencode/agents/issue-writer.md (same permissions as run). -->
Turn this into a GitHub issue (if it holds several separate ideas, do the
steps below once per idea):

$ARGUMENTS

Work through these steps in order. Do not skip any.

1. **Find the owning repo.** `~/Code/` holds one git clone per repo (all
   `dvystrcil/<dir name>`). Run `ls ~/Code` and pick the repo that owns the
   component (e.g. obico → `dvystrcil/obico`, a Grafana rule →
   `dvystrcil/prometheus`). Only use `dvystrcil/homelab` for cross-cutting
   work (bin/ scripts, architecture docs, cluster-wide ops) or when no
   component repo fits.

2. **Research before writing** (read-only, about 5-15 commands). Look at
   the real thing the idea is about:
   - `grep -rn <term> ~/Code/<repo> --include=*.yaml --include=*.py ...`
     and read the files that matter.
   - If it is about something running: `kubectl get`/`describe`/`logs`
     (read verbs only).
   - `ls ~/Code/homelab/architecture/` and read a doc if one matches.
   Write down each fact with where you read it. Do not invent tools,
   channels, metrics or file names. If you did not check something, label
   it **unverified**.

3. **Write the body to a file** with a heredoc (real newlines, never `\n`):
   `cat > /tmp/issue-<slug>.md <<'BODY'` … `BODY`. Use exactly these sections:
   ## Why / ## What we know / ## Acceptance criteria / ## Lane / ## Related
   (full template text as in homelab .github/ISSUE_TEMPLATE/idea.md)

   Lane rubric: `opencode` only if the work is in ONE repo, you can name the
   files to change, a similar example already exists to copy, and no design
   decision is needed. Everything else, and anything touching networking,
   storage, secrets, ArgoCD itself, or CoreDNS, is `claude`.

4. **File through the gate.** The title is specific (component + what),
   at least 15 characters:
   `~/Code/homelab/bin/new-issue.py --repo dvystrcil/<repo> --title "<title>" --body-file /tmp/issue-<slug>.md`
   Add `--search "<two words>"` for any synonym the title doesn't contain.
   - exit 1: the body failed validation. Fix what it lists and re-run.
   - exit 2: it lists possible duplicates. Run `gh issue view <url>` on
     EACH one. If one already covers this idea, STOP: do not file. Report
     that URL. Otherwise re-run the same command with `--ack <url>` for
     every candidate.
   - exit 0: it prints the new issue URL.

5. **Report** one line per idea: the issue URL (or the existing duplicate's
   URL and why), the repo, and the lane.
