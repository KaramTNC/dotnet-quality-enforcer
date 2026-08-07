---
name: testing
description: Write, update, and validate deterministic, behavior-focused tests without changing product intent.
---

# Testing

1. Identify the behavior, inputs, outputs, and failure modes before asserting.
2. Prefer the smallest test at the boundary that owns the behavior.
3. Use fixtures, fakes, mocks, and seeded data instead of live services.
4. Cover meaningful error, empty, null, ordering, retry, and idempotence cases.
5. Add a regression test for every defect fix.
6. Assert observable outcomes rather than incidental implementation details.
7. Run the narrowest relevant suite, then broaden validation for affected layers.
8. Do not rewrite expected results to hide a product bug or flaky setup.

