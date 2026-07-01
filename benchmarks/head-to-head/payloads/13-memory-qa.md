# Task 13: Memory-grounded Q&A

- **Cluster:** memory & verification
- **Fixtures:** none
- **Dimensions:** correctness, faithfulness, memory

## Prompt

What's the current XNACK status on the Stable Diffusion WebUI deployment? Be
specific about the kernel parameters involved and what's still unresolved.

## Ground truth / rubric

The answer only exists in the memory store (`project_sd_webui_xnack` and
`feedback_verify_kernel_param_name`). Ground truth:
- SD WebUI is **scaled to 0** — XNACK alone was insufficient; PyTorch on gfx1151
  **still segfaults** even with XNACK enabled.
- The canonical kernel parameter is **`amdgpu.noretry=0`** (Linux silently
  accepts unknown param names, so the exact spelling matters).
- Several angles remain unverified — the fix isn't found; it's a known-open gap.

A correct answer demonstrably **uses the injected memory** — it states the
scaled-to-0 status and the still-segfaults conclusion, not a generic "XNACK
enables page faulting" textbook answer.

This is the core test of whether memory injection changes the answer. A response
that could have been written without the memory store scores 0 on memory even if
it's technically true in general.

## Scoring

- **correctness (0–3):** 3 = scaled-to-0 + still-segfaults + right kernel param; 1 = partial; 0 = generic/wrong.
- **faithfulness (0–3):** 3 = matches the memory specifics, no invented resolution; 0 = claims it's fixed.
- **memory (0–3):** 3 = clearly references homelab-only facts (deployment state, `amdgpu.noretry=0`, unresolved angles); 0 = generic XNACK explanation.
