"""
Hook PreToolUse: bloquea `git commit` / `git push` si el diff agrega credenciales.

Motivación: una Steam Web API key quedó hardcodeada en un archivo que no estaba
cubierto por .gitignore, a un `git add .` de volverse pública para siempre.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

sys.stderr.reconfigure(encoding="utf-8", errors="replace")

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
MAX_DIFF_BYTES = 20_000_000

PATTERNS = [
    ("clave privada", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("token de GitHub", re.compile(r"\bgh[posur]_[A-Za-z0-9]{36}\b")),
    ("token de GitHub (PAT)", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("access key de AWS", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("API key de OpenAI", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("token de Slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "credencial asignada a una variable",
        # Sin \b inicial a propósito: en STEAM_API_KEY no hay frontera de palabra
        # entre "_" y "API", y esa es justamente la forma que se filtró.
        re.compile(
            r"(?i)(?:api[_-]?key|secret|token|password|passwd|auth[_-]?key)\w*"
            r"""\s*[:=]\s*["']([^"']{8,})["']"""
        ),
    ),
]

PLACEHOLDER_HINTS = (
    "your", "placeholder", "example", "changeme", "replace", "todo", "dummy",
    "fake", "sample", "<", "${", "os.environ", "getenv", "xxx", "...",
)


def _is_placeholder(value: str) -> bool:
    low = value.lower()
    if any(hint in low for hint in PLACEHOLDER_HINTS):
        return True
    return len(set(value)) <= 2  # "00000000", "aaaaaaaa"


def _git(repo: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True,
            errors="ignore", timeout=45,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout[:MAX_DIFF_BYTES] if out.returncode == 0 else ""


def _target_diff(repo: Path, command: str) -> tuple[str, str]:
    """Devuelve (diff, descripción) según se esté por commitear o pushear."""
    if _matches(command, "commit"):
        return _git(repo, "diff", "--cached", "-U0"), "los cambios en stage"
    upstream = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}").strip()
    if upstream:
        return _git(repo, "diff", "-U0", f"{upstream}..HEAD"), f"los commits por delante de {upstream}"
    return _git(repo, "diff", "-U0", EMPTY_TREE, "HEAD"), "todo el historial (primer push)"


def _matches(command: str, subcommand: str) -> bool:
    for segment in re.split(r"&&|\|\||;|\|", command):
        if re.search(rf"\bgit\b[^\n]*\b{subcommand}\b", segment):
            return True
    return False


def _scan(diff: str) -> list[str]:
    findings = []
    current_file = ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        content = line[1:]
        for label, pattern in PATTERNS:
            m = pattern.search(content)
            if not m:
                continue
            value = m.group(1) if m.groups() else m.group(0)
            if m.groups() and _is_placeholder(value):
                continue
            snippet = content.strip()[:80]
            findings.append(f"  {current_file or '?'}: {label} -> {snippet}")
            break
    return findings


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    if payload.get("tool_name") != "Bash":
        return 0
    command = payload.get("tool_input", {}).get("command", "")
    if not (_matches(command, "commit") or _matches(command, "push")):
        return 0

    repo = Path(payload.get("cwd") or Path(__file__).resolve().parents[2])
    diff, what = _target_diff(repo, command)
    if not diff:
        return 0

    findings = _scan(diff)
    if not findings:
        return 0

    print(
        "Posibles credenciales en " + what + ":\n"
        + "\n".join(findings[:10])
        + "\n\nRevisá cada línea antes de continuar. Si es un secreto real: sacalo del "
        "archivo, rotá la credencial y agregá el archivo a .gitignore. Si es un falso "
        "positivo, avisale al usuario y pedí confirmación explícita para seguir.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
