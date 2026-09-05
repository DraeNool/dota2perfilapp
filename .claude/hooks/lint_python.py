"""
Hook PostToolUse: corre ruff (y la suite de tests) tras editar código Python.

ruff tarda ~0.2s y la suite completa ~1.4s, así que el ciclo sigue siendo
interactivo. mypy queda fuera a propósito: hoy reporta errores preexistentes en
opendota.py y un hook que siempre falla termina desactivado.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.stderr.reconfigure(encoding="utf-8", errors="replace")

WATCHED_DIRS = ("dota_config_sync", "tests")


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", *args], cwd=repo, capture_output=True, text=True,
        errors="ignore", timeout=120,
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    if payload.get("tool_name") not in ("Edit", "Write", "MultiEdit"):
        return 0

    raw_path = payload.get("tool_input", {}).get("file_path", "")
    if not raw_path.endswith(".py"):
        return 0

    repo = Path(__file__).resolve().parents[2]
    try:
        rel = Path(raw_path).resolve().relative_to(repo)
    except ValueError:
        return 0  # archivo fuera del proyecto

    problems = []

    ruff = _run(repo, "ruff", "check", str(rel))
    if ruff.returncode not in (0, 1) and not ruff.stdout:
        return 0  # ruff no instalado: el hook no debe entorpecer
    if ruff.returncode == 1:
        problems.append("ruff:\n" + ruff.stdout.strip())

    if rel.parts and rel.parts[0] in WATCHED_DIRS:
        tests = _run(repo, "pytest", "-q", "--no-header", "-x")
        if tests.returncode != 0:
            problems.append("pytest:\n" + tests.stdout.strip()[-2000:])

    if not problems:
        return 0

    print("\n\n".join(problems), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
