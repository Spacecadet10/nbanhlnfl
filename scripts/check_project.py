"""Read-only structure guard. --ready also runs required application checks."""
from pathlib import Path
import argparse
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SKIP = {'.git', '.omc', '.venv', 'venv', 'node_modules', '__pycache__', '.pytest_cache', 'dist', 'data', '.mypy_cache', '.ruff_cache'}


def inspect(root):
    errors = []
    files = [p for p in root.rglob('*') if p.is_file() and not any(x in SKIP for x in p.relative_to(root).parts)]
    allowed = {'frontend', 'backend', 'tests', 'docs', 'scripts'}
    for p in root.iterdir():
        if p.is_dir() and p.name not in allowed | SKIP:
            errors.append(f'Unexpected top-level directory: {p.name}; use the canonical layout.')
    for p in files:
        rel = p.relative_to(root).as_posix()
        if re.search(r'(?:[ _-](?:copy|backup|old|final|v[2-9])| \(\d+\))(?=\.|$)', p.name, re.I):
            errors.append(f'Possible duplicate/backup file: {rel}; modify the original instead.')
        if p.name == 'package.json' and rel != 'frontend/package.json':
            errors.append(f'Extra JavaScript package: {rel}; one frontend only.')
        if p.name == 'pyproject.toml' and rel != 'backend/pyproject.toml':
            errors.append(f'Extra Python project: {rel}; one backend only.')
        if p.name in {'yarn.lock', 'pnpm-lock.yaml', 'bun.lock', 'bun.lockb'}:
            errors.append(f'Unexpected package-manager lock: {rel}; use npm/package-lock.json.')
        if p.name == 'package-lock.json' and rel != 'frontend/package-lock.json':
            errors.append(f'Extra npm lockfile: {rel}.')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ready', action='store_true')
    args = parser.parse_args()
    errors = inspect(ROOT)
    if args.ready:
        for name in ('frontend/package.json', 'frontend/package-lock.json', 'backend/pyproject.toml', 'docs/SMOKE_TEST.md'):
            if not (ROOT / name).is_file():
                errors.append(f'Missing readiness file: {name}')
    if errors:
        print('\n'.join('FAIL: ' + x for x in errors))
        return 1
    print('Structure guard passed. This does not prove semantic uniqueness or app correctness.', flush=True)
    if args.ready:
        checks = [(['npm', 'run', 'typecheck'], ROOT / 'frontend'),
                  (['npm', 'run', 'build'], ROOT / 'frontend'),
                  ([sys.executable, '-m', 'pytest', '-q'], ROOT / 'backend')]
        for cmd, cwd in checks:
            try:
                result = subprocess.run(cmd, cwd=cwd, check=False)
            except OSError as exc:
                print(f'FAIL: {exc}')
                return 1
            if result.returncode:
                return result.returncode
        print('Automated readiness checks passed. Review actual browser/API evidence in docs/SMOKE_TEST.md.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
