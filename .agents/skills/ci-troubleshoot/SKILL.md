---
name: ci-troubleshoot
description: Diagnose and fix local or GitHub Actions build, test, analysis, and quality failures.
---

# CI troubleshooting

1. Identify the exact workflow, job, command, and complete failure set.
2. Read the workflow and invoked scripts before changing product code.
3. Reproduce locally with the same SDK, configuration, and command sequence.
4. Separate infrastructure, setup, flaky, and product failures.
5. Fix the smallest complete root cause; do not weaken the gate to hide it.
6. Rerun all affected checks and report commands, results, skips, and blockers.

