# EngineeringFoundation adoption

This repository adopts `KaramTNC/EngineeringFoundation` at the release pinned
in [`.foundation/foundation.json`](../.foundation/foundation.json).

The adoption brings in the shared engineering contract and portable Codex
agents/skills/hooks. The local Python package, GitHub Action, Roslyn helper,
test suite, release packaging, PyPI publishing, and quality-enforcer behavior
remain owned by this repository.

The Foundation .NET quality-policy assets are intentionally not synchronized:
their effective policy is for consuming .NET repositories and is incompatible
with this package's own policy-validation contract.

This is intentionally not a consumer of
`reusable-dotnet.yml`: that Foundation workflow installs the released
`dotnet-quality-enforcer` package, so using it here would create a circular CI
dependency. Foundation updates are limited to the selected shared assets
through the dedicated update workflow.

Review the Foundation release notes and run the normal Python, package, and
Roslyn checks before merging an update pull request.
