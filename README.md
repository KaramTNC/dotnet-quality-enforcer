# .NET Quality Enforcer

[![CI](https://github.com/KaramTNC/dotnet-quality-enforcer/actions/workflows/ci.yml/badge.svg)](https://github.com/KaramTNC/dotnet-quality-enforcer/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/KaramTNC/dotnet-quality-enforcer)](LICENSE)
[![GitHub release downloads](https://img.shields.io/github/downloads/KaramTNC/dotnet-quality-enforcer/total.svg?label=GitHub%20release%20downloads)](https://github.com/KaramTNC/dotnet-quality-enforcer/releases)

Installable, configuration-driven quality gates for C# and .NET repositories.

## What it does

`.NET Quality Enforcer` provides reusable checks for:

- architectural dependency boundaries
- code size and complexity
- source and namespace layout
- public API XML documentation
- test architecture and naming conventions
- Cobertura repository, diff, and branch coverage

The package owns the analysis engine and its tests. Consuming repositories own their policies, baselines, source layout, layer names, thresholds, and CI-specific paths.

The checks run against an explicit repository working directory. A policy file is optional for commands that provide defaults, but it is recommended for repeatable CI configuration.

## Requirements

- Python 3.10 or newer
- A C#/.NET repository to analyze
- .NET 8 SDK only when using the optional Roslyn parser
- ReportGenerator only when using `coverage-report`

The package has no runtime Python dependencies outside the standard library.

## GitHub Action

This repository can be used directly as a cross-platform composite action. Pin consumers to a release tag or, preferably, an immutable commit SHA:

```yaml
steps:
  - uses: actions/checkout@v4
  - id: quality
    uses: KaramTNC/dotnet-quality-enforcer@v1
    with:
      command: code-size
      arguments: --scope full
      parser: auto
```

The action installs the package, runs the selected gate, and exposes `result`, `status`, `returncode`, `violations`, and `warnings` outputs. Set `install-roslyn: true` to install the .NET 8 SDK and build the bundled Roslyn helper before running a Roslyn-enabled gate. The `coverage-report` command still requires ReportGenerator to be available on the runner.

The action's `result` output uses the same `schema_version: 1` JSON envelope as the command-line interface:

```yaml
- name: Fail on quality violations
  if: steps.quality.outputs.status == 'failed'
  run: echo '${{ steps.quality.outputs.violations }}'
```

The release-download badge above counts downloads of GitHub Release assets. It does not count workflow executions that reference this repository with `uses:`. GitHub Actions usage is tracked separately through GitHub's Actions usage metrics; no telemetry is sent by this action.

## Installation

The current public distributions are attached to [GitHub Releases](https://github.com/KaramTNC/dotnet-quality-enforcer/releases). Download the wheel that matches the release you want, or install from a source checkout:

```bash
git clone https://github.com/KaramTNC/dotnet-quality-enforcer.git
cd dotnet-quality-enforcer
python -m pip install .
```

For local development, install the development tools as well:

```bash
python -m pip install -e ".[dev]"
```

## Usage

Run the top-level help to see every command and its options:

```bash
dotnet-quality --help
```

Commands can analyze the current directory or another repository with `--repo-root`:

```bash
dotnet-quality --repo-root path/to/repository code-size \
  --scope full \
  --policy-path .quality/quality_policy.json

dotnet-quality public-api-documentation \
  --policy-path .quality/quality_policy.json \
  --baseline-path .quality/baselines/public_api_documentation_baseline.txt
```

Available commands:

| Command | Purpose |
| --- | --- |
| `architectural-boundaries` | Validate project and namespace dependency boundaries. |
| `code-size` | Validate C# method, type, and file size. |
| `diff-complexity` | Validate changed-method complexity and CRAP scores. |
| `diff-coverage` | Validate changed-line and changed-branch coverage. |
| `namespace-layout` | Validate source namespaces against their paths. |
| `public-api-documentation` | Validate XML documentation for public C# APIs. |
| `repo-coverage` | Validate Cobertura repository and package coverage. |
| `source-type-layout` | Validate C# source type/file layout. |
| `test-architecture` | Validate source and test project placement. |
| `test-conventions` | Validate source-to-test naming and convention rules. |
| `coverage-report` | Generate a ReportGenerator coverage report. |

For automation, request a structured result envelope:

```bash
dotnet-quality --output json code-size --scope full
```

The JSON envelope has `schema_version: 1`, a `status`, `returncode`, `violations`, `warnings`, repository metadata, and the original `stdout`/`stderr` for compatibility.

Most commands use `.quality/quality_policy.json` by default when it exists. The top-level command validates known policy keys before starting a gate and reports the exact invalid key. Baseline files contain known violations that are intentionally accepted by the consuming repository; keep those files in the consuming repository rather than in this package.

The top-level options also support `--timeout SECONDS` for external tools and `--parser auto|python|roslyn`. The default `auto` mode uses Roslyn only when configured; `python` forces the dependency-free parser, and `roslyn` fails if the helper is unavailable or cannot analyze a file.

## Optional Roslyn parsing

The built-in parser has no .NET runtime dependency. For modern C# syntax, build the optional Roslyn helper and set its command before running a gate:

```bash
dotnet build tools/roslyn-analyzer/DotnetQualityRoslyn.csproj -c Release
export DOTNET_QUALITY_ROSLYN_COMMAND="dotnet tools/roslyn-analyzer/bin/Release/net8.0/DotnetQualityRoslyn.dll"
```

When configured, source-type and unit-test convention analysis uses Roslyn. If the helper is unavailable or returns an error, the built-in parser is used instead.

Versioned releases also include a framework-dependent Roslyn helper archive. It still requires the .NET 8 runtime, but avoids rebuilding the helper locally. Release assets include SHA-256 checksums, an SBOM, build metadata, and GitHub artifact provenance.

## Download tracking

The badge at the top of this page tracks downloads of the wheel and source-distribution assets attached to this repository's GitHub Releases. It does not include Git clones, source-archive downloads, or installations from other channels. See the [release download statistics](https://github.com/KaramTNC/dotnet-quality-enforcer/releases) for the individual assets and releases.

## Development and CI

Run the local checks with:

```bash
python -m unittest discover -s tests -p "test_*.py"
ruff check src tests action_runner.py
mypy src action_runner.py
pip-audit .
```

Pull requests targeting `staging` or `main` run the test suite on Python 3.10 through 3.13, plus static analysis and a Roslyn helper smoke test. Successful pushes to `main` build distributions and create a GitHub Release named `main-<commit-sha>`. Version tags such as `v0.2.0` create versioned releases. The release workflow requires GitHub Actions permission to write repository contents.

The package version is derived from Git tags with `setuptools-scm`: a tag such as `v0.2.0` produces version `0.2.0`. Source checkouts without package metadata use `0.0.0+unknown`.

## Contributing

1. Create a branch from `staging`.
2. Make the change and add or update tests.
3. Run the development checks locally.
4. Open a pull request targeting `staging` and describe the user-facing impact.

Issues and feature requests can be submitted through the [GitHub issue tracker](https://github.com/KaramTNC/dotnet-quality-enforcer/issues).

## License

This project is available under the [MIT License](LICENSE).
