# .NET Quality Enforcer

Installable, configuration-driven quality gates for C# and .NET repositories.

This repository contains the reusable quality tooling extracted from a .NET application repository. It owns the analysis engine and its tests; consuming repositories own their policies, baselines, source layout, and CI-specific paths.

The package provides checks for:

- architectural dependency boundaries
- code size and complexity
- source and namespace layout
- public API XML documentation
- test architecture and naming conventions
- Cobertura repository, diff, and branch coverage

The checks operate on an explicit repository working directory and policy file. Repository-specific paths, layer names, thresholds, and baseline suppressions belong in the consuming repository rather than in this package.

## Usage

```bash
python -m pip install .
dotnet-quality code-size --scope full --policy-path .quality/quality_policy.json
dotnet-quality public-api-documentation \
  --policy-path .quality/quality_policy.json \
  --baseline-path .quality/baselines/public_api_documentation_baseline.txt
```

Run `dotnet-quality --help` for all available checks.

## Development

```bash
python -m pip install -e .
python -m unittest discover -s tests -p "test_*.py"
```

The package targets Python 3.10 and newer and has no runtime dependencies outside the standard library.

## CI/CD

- Pull requests targeting `staging` or `main` run the test suite on Python 3.10 through 3.13.
- Successful pushes to `main` build the package and create a GitHub Release named `main-<commit-sha>` with the distributions attached.
- Pushing a version tag such as `v0.2.0` creates a versioned GitHub Release using the same build process.

Main releases are created only after the CI workflow succeeds. The repository's Actions settings must allow the workflow `GITHUB_TOKEN` to write repository contents so it can create releases.
