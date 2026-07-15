# Contributing

Thanks for helping improve .NET Quality Enforcer. Issues, documentation improvements, bug fixes, tests, and new quality rules are welcome.

## Development setup

The project requires Python 3.10 or newer. Install the development dependencies from the repository root:

```bash
python -m pip install -e ".[dev]"
```

Run the same checks used by CI before opening a pull request:

```bash
python -m unittest discover -s tests -p "test_*.py"
ruff check src tests action_runner.py
mypy src action_runner.py
```

The optional Roslyn helper requires the .NET 8 SDK:

```bash
dotnet build tools/roslyn-analyzer/DotnetQualityRoslyn.csproj -c Release
```

## Pull requests

1. Create a branch from `staging`.
2. Keep changes focused and add or update tests.
3. Update the README or examples when user-facing behavior changes.
4. Run the checks above and describe the user-facing impact in the pull request.
5. Target `staging`; changes are promoted to `main` for releases.

Please do not include generated `build/`, `dist/`, `bin/`, or `obj/` output in commits.

## Adding a quality rule

Add the implementation under the relevant `src/dotnet_quality_gates` package, cover normal and invalid-input behavior in `tests/`, and document policy keys and defaults in the README. Preserve the JSON result envelope and exit-code behavior unless the change explicitly updates that contract.
