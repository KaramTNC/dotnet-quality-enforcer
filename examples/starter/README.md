# Starter example

This directory is a small, copyable example of adding .NET Quality Enforcer to a C# repository.

Copy `.quality/quality_policy.json` and `.github/workflows/dotnet-quality.yml` into a repository, then adjust the policy roots and thresholds to match its layout. The sample workflow runs the full code-size gate on pull requests and pushes to `main`.

The sample policy also demonstrates a custom architecture unit. For repositories with
multiple units, add the remaining source roots, namespace prefixes, and allowed dependency
edges; see the main README for custom test-project mappings.

The workflow uses the `v0` compatibility tag, which tracks the latest 0.x release. For production use, replace the tag with the release you have reviewed or an immutable commit SHA.
