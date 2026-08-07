<!-- BEGIN ENGINEERINGFOUNDATION MANAGED -->
# Shared project agent contract

## Engineering

- Identify the owning layer and namespace before implementing behavior.
- Keep Domain independent from Application, Infrastructure, and Presentation.
- Prefer small, composable types and explicit dependencies.
- Preserve existing behavior unless the task explicitly changes the contract.

## Scope and safety

- Inspect status before editing and preserve unrelated changes.
- Do not expose `.env`, credentials, private keys, or production data.
- Do not commit, push, deploy, or publish without explicit authorization.
- Use mocks, fixtures, and paper/sandbox integrations for external services.

## Testing and completion

- Start from the behavior contract and test observable outcomes.
- Keep tests deterministic and cover meaningful branches and failures.
- Run targeted validation first, then the complete affected checks.
- Review the final diff, skipped checks, and remaining risks before completion.
<!-- END ENGINEERINGFOUNDATION MANAGED -->

# .NET Quality Enforcer repository guide

This repository is a Python package and GitHub Action that analyzes consuming
C#/.NET repositories. Keep the analysis engine under `src`, its Python tests
under `tests`, and the optional Roslyn helper under `tools/roslyn-analyzer`.

## Validation

```bash
python -m unittest discover -s tests -p "test_*.py"
ruff check src tests action_runner.py
mypy src action_runner.py
dotnet build tools/roslyn-analyzer/DotnetQualityRoslyn.csproj --configuration Release --nologo
```

The repository retains its local Python/package CI. It does not call
EngineeringFoundation's reusable .NET workflow because that workflow installs
this package as its quality enforcer; making this repository depend on that
workflow would create a circular CI dependency.
