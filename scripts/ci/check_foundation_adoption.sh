#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
import json
import re
from pathlib import Path

manifest_path = Path('.foundation/foundation.json')
manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
required = {'repository', 'version', 'template', 'adopted', 'localExtensions'}
missing = required.difference(manifest)
if missing:
    raise SystemExit(f'Missing foundation manifest fields: {", ".join(sorted(missing))}')
if manifest['repository'] != 'KaramTNC/EngineeringFoundation':
    raise SystemExit('Unexpected foundation repository.')
if not re.fullmatch(r'v\d+\.\d+\.\d+', manifest['version']):
    raise SystemExit('Foundation version must be a semantic release tag.')
if not manifest['adopted'] or not manifest['localExtensions']:
    raise SystemExit('Manifest must list adopted capabilities and local extensions.')

workflow_dir = Path('.github/workflows')
update_workflow = workflow_dir / 'engineering-foundation-update.yml'
workflow = update_workflow.read_text(encoding='utf-8')
if 'repository: KaramTNC/EngineeringFoundation' not in workflow:
    raise SystemExit('Foundation update workflow does not check out the expected repository.')
if '--manifest .foundation/foundation-assets.json' not in workflow:
    raise SystemExit('Foundation update workflow does not use the selected asset manifest.')

all_workflows = '\n'.join(path.read_text(encoding='utf-8') for path in workflow_dir.glob('*.yml'))
if 'KaramTNC/EngineeringFoundation/.github/workflows/reusable-dotnet.yml@' in all_workflows:
    raise SystemExit('This package must not call Foundation reusable-dotnet.yml; CI would be circular.')

print(f'EngineeringFoundation adoption is valid at {manifest["version"]}.')
PY
