# .NET Quality Enforcer

[![CI](https://github.com/KaramTNC/dotnet-quality-enforcer/actions/workflows/ci.yml/badge.svg)](https://github.com/KaramTNC/dotnet-quality-enforcer/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/KaramTNC/dotnet-quality-enforcer)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/KaramTNC/dotnet-quality-enforcer?sort=semver)](https://github.com/KaramTNC/dotnet-quality-enforcer/releases/latest)
[![GitHub release downloads](https://img.shields.io/github/downloads/KaramTNC/dotnet-quality-enforcer/total.svg?label=GitHub%20release%20downloads)](https://github.com/KaramTNC/dotnet-quality-enforcer/releases)

Installable, configuration-driven quality gates for C# and .NET repositories.

This project is pre-1.0. Feedback from teams using incremental quality enforcement is welcome.

## What it does

`.NET Quality Enforcer` provides reusable checks for:

- architectural dependency boundaries
- code size and complexity
- source and namespace layout
- public API XML documentation
- test architecture and naming conventions
- [Cobertura](https://cobertura.github.io/cobertura/) repository, diff, and branch coverage

The enforcer provides the analysis engine and tests. Consuming repositories provide the policies, baselines, source layout, layer names, thresholds, and CI paths.

Checks run against an explicit repository working directory. A policy file is optional for commands with defaults, but recommended for repeatable CI.

## Quality metrics and rules

The enforcer combines numeric maintainability metrics with structural quality rules. Thresholds below are built-in defaults and can be overridden in `.quality/quality_policy.json`. Configured expected coverage packages must be present in the merged report; missing aliases fail the repository coverage gate.

| Area | What is measured or enforced | Built-in default |
| --- | --- | --- |
| Code size | Physical lines in each method, type, and source file, excluding XML documentation comment lines from source-file totals; partial types are also aggregated across files. | Warn at 40/250/300 lines and fail at 60/350/450 lines for methods/types/files respectively. |
| Diff complexity | Changed production methods are checked for [cyclomatic complexity](https://docs.sonarsource.com/sonarqube-server/user-guide/code-metrics/metrics-definition#cyclomatic-complexity), [cognitive complexity](https://docs.sonarsource.com/sonarqube-server/user-guide/code-metrics/metrics-definition#cognitive-complexity), and [CRAP score](https://testing.googleblog.com/2011/02/this-code-is-crap.html). | Cyclomatic <= 10, cognitive <= 10, CRAP <= 30.00. No file-count limit by default. |
| CRAP score | Combines cyclomatic complexity with method coverage: `complexity² × (1 - coverage)³ + complexity`. Higher complexity and lower coverage produce a higher risk score. | Maximum 30.00. Coverage comes from the supplied Cobertura report. |
| Diff coverage | Executable changed-line coverage and, when configured, changed-branch coverage. | Line coverage >= 80%; branch coverage is optional. No file-count limit by default. |
| Repository coverage | [Cobertura](https://cobertura.github.io/cobertura/) line coverage, plus optional branch coverage, for configured packages and classes. | Line coverage defaults to 100% for configured expected packages; branch coverage is optional. |
| Structural rules | Architectural dependency boundaries, namespace-to-path alignment, source type/file layout, public API XML summaries, test project placement, and source-to-test naming/target conventions. | Repository policy defines the expected layers, roots, mappings, and exclusions. |

The `diff-complexity` gate evaluates changed production methods. It combines path complexity, control-flow readability, and test coverage so legacy complexity can be managed incrementally.

For example, the complexity and coverage limits can be configured as follows:

```json
{
  "diff_quality": {
    "cyclomatic_complexity_max": 10,
    "cognitive_complexity_max": 10,
    "crap_score_max": 30,
    "line_coverage_threshold": 0.8,
    "branch_coverage_threshold": 0.8,
    "max_files_for_gate": 40
  }
}
```

Diff complexity and diff coverage analyze the full changed production set by default. Set `max_files_for_gate` to a positive integer only when a repository explicitly wants a maintenance cap; the setting applies to both diff gates.

## Requirements

- [Python](https://www.python.org/) 3.10 or newer
- A C#/.NET repository to analyze
- [.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0) only when using the optional [Roslyn](https://learn.microsoft.com/en-us/dotnet/csharp/roslyn-sdk/) parser
- [ReportGenerator](https://github.com/danielpalme/ReportGenerator) only when using `coverage-report`

The package has no runtime Python dependencies outside the standard library.

## GitHub Action

This repository can be used directly as a cross-platform composite action. Pin consumers to a release tag or, preferably, an immutable commit SHA:

```yaml
steps:
  - uses: actions/checkout@v7
  - id: quality
    uses: KaramTNC/dotnet-quality-enforcer@v0
    with:
      command: code-size
      arguments: --scope full
      parser: auto
```

The `v0` compatibility tag tracks the latest 0.x release. For production workflows, replace it with the release you have reviewed or an immutable commit SHA.

The action installs the package, runs the selected gate, and exposes `result`, `status`, `returncode`, `violations`, `blocking-errors`, and `warnings` outputs. Each invocation also prints a compact status block and appends a Markdown section to `GITHUB_STEP_SUMMARY`. Calling the action once per gate therefore creates one centralized job summary containing the blocking errors from every gate. Set `install-roslyn: true` to install the .NET 8 SDK and build the bundled Roslyn helper before running a Roslyn-enabled gate. The `coverage-report` command still requires [ReportGenerator](https://github.com/danielpalme/ReportGenerator) on the runner.

The action's `result` output uses the same `schema_version: 1` JSON envelope as the command-line interface:

```yaml
- name: Fail on quality violations
  if: steps.quality.outputs.status == 'failed'
  run: echo '${{ steps.quality.outputs.blocking-errors }}'
```

## Installation

The current public distributions are attached to [GitHub Releases](https://github.com/KaramTNC/dotnet-quality-enforcer/releases). Download the wheel that matches the release you want, or install from a source checkout:

```bash
git clone https://github.com/KaramTNC/dotnet-quality-enforcer.git
cd dotnet-quality-enforcer
python -m pip install .
```

The versioned release workflow also supports publishing the package to PyPI through trusted publishing. Once the repository's `pypi` environment is connected to a PyPI trusted publisher, install the CLI with:

```bash
python -m pip install dotnet-quality-gates
```

The one-time PyPI trusted-publisher configuration should use owner `KaramTNC`, repository `dotnet-quality-enforcer`, workflow `package.yml`, and environment `pypi`. The workflow uses short-lived OIDC credentials; no PyPI token is stored in the repository.

For local development, install the development tools as well:

```bash
python -m pip install -e ".[dev]"
```

For a copyable GitHub Actions workflow and starter policy, see [`examples/starter`](examples/starter).

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

The JSON envelope has `schema_version: 1`, a `status`, `returncode`, `violations`, normalized `blocking_errors`, `warnings`, repository metadata, and the original `stdout`/`stderr` for compatibility. `blocking_errors` is the concise list to display when a gate blocks the build; `violations` remains available for consumers that need the legacy extracted detail list. Policy validation is strict: unknown sections and keys are rejected so a misspelled setting cannot silently fall back to a default.

Most commands use `.quality/quality_policy.json` by default when it exists. The top-level command validates policy structure and value types before starting a gate and reports the exact invalid key. Baseline files contain known violations that are intentionally accepted by the consuming repository; keep those files in the consuming repository rather than in this package.

The top-level options also support `--timeout SECONDS` for external tools and `--parser auto|python|roslyn`. The default `auto` mode uses Roslyn only when configured; `python` forces the dependency-free parser, and strict `roslyn` mode currently applies to `source-type-layout` and `test-conventions` and fails if the helper is unavailable or cannot analyze a file.

## Optional Roslyn parsing

The built-in parser has no .NET runtime dependency. For modern C# syntax, build the optional [Roslyn](https://learn.microsoft.com/en-us/dotnet/csharp/roslyn-sdk/) helper and set its command before running a gate:

```bash
dotnet build tools/roslyn-analyzer/DotnetQualityRoslyn.csproj -c Release
export DOTNET_QUALITY_ROSLYN_COMMAND="dotnet tools/roslyn-analyzer/bin/Release/net8.0/DotnetQualityRoslyn.dll"
```

When configured, source-type and unit-test convention analysis uses Roslyn. In `auto` mode, an unavailable helper can use the built-in parser; use `roslyn` when a gate must fail rather than degrade to the fallback parser. The fallback parser is dependency-free but should be treated as a compatibility mode for modern C# syntax.

Versioned releases also include a framework-dependent Roslyn helper archive. It requires the .NET 8 runtime but avoids rebuilding the helper locally.

## Download tracking

The badge at the top of this page tracks downloads of the wheel and source-distribution assets attached to this repository's GitHub Releases. It does not include Git clones, source-archive downloads, or installations from other channels. See the [release download statistics](https://github.com/KaramTNC/dotnet-quality-enforcer/releases) for the individual assets and releases.

## Development and CI

Run the local checks with:

```bash
python -m coverage run -m unittest discover -s tests -p "test_*.py"
python -m coverage report
ruff check src tests action_runner.py
mypy src action_runner.py
pip-audit .
```

The test suite also includes cross-platform action argument, policy-validation, coverage, XML-input, and JSON-output contract checks. Run the Roslyn build locally when changing the helper or parser integration.

Pull requests targeting `staging` or `main` run the test suite on Python 3.10 through 3.13, plus static analysis and a Roslyn helper smoke test. Successful pushes to `main` build distributions and create a GitHub Release named `main-<commit-sha>`. Version tags matching `vX.Y.Z` create versioned releases and publish the Python package when PyPI trusted publishing is configured. The release workflow requires GitHub Actions permission to write repository contents.

The package version is derived from Git tags with [`setuptools-scm`](https://setuptools-scm.readthedocs.io/); source checkouts without package metadata use `0.0.0+unknown`.

## Contributing

Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Issues and feature requests can be submitted through the [GitHub issue tracker](https://github.com/KaramTNC/dotnet-quality-enforcer/issues), and usage questions can be asked in [Discussions](https://github.com/KaramTNC/dotnet-quality-enforcer/discussions).

Security issues should follow the process in [SECURITY.md](SECURITY.md).

## License

This project is available under the [MIT License](LICENSE).
