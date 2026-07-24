"""Self-evolution runner — Layer 2 of the SELF.md self-evolution roadmap.

This package assembles the **trajectory-collection** layer described in
``minimax_code/SELF.md`` §4: it collects git history, lint/test
output, and the working-tree state into durable reports under
``progress/self-evolution-reports/``. It is the prerequisite for any
later Layer-3 (SFT) work — without trajectories, there is nothing to
train on.

Scope of this version (v0)
--------------------------

* Read-only over the codebase. Never runs ``git commit`` / ``git push``.
* No LLM calls in the default flow. The trajectory is **facts** (git
  log + ruff output + pytest output + file diff stats), not
  interpretations. An optional LLM summarisation step is provided
  separately and is off by default.
* Idempotent: re-running the same day's report overwrites it
  in-place. Past days are never overwritten.
* Fits into ``scheduler.PayloadFn`` so ``JobScheduler`` can fire it
  on cron, and is also runnable as a one-shot CLI
  (``python -m minimax_code.agent.self_evolution --once``).

What this is NOT
----------------

* Not a training pipeline.
* Not an agent-driven roadmap.
* Not a code-modification loop. It **observes**; it does not edit
  source. Any "fix the lints" loop belongs to a separate, future
  module that reads these reports and proposes patches through
  ``permission.*`` — never auto-applied.
"""

from __future__ import annotations

from .payload import build_payload_runner
from .runner import SelfEvolutionReport, run_once

__all__ = [
    "SelfEvolutionReport",
    "build_payload_runner",
    "run_once",
]
