"""Prediccion de si Claude Code va a pedir confirmacion.

Por que existe: Claude Code emite el evento `Notification` (la senal
oficial de "estoy pidiendo permiso") con varios segundos de retraso,
mientras que `PreToolUse` llega al instante, justo antes de que aparezca
el dialogo. Adelantarse exige *predecir* la decision.

Limite asumido: no se puede replicar con exactitud la logica interna de
Claude Code (modos, limites del espacio de trabajo, "permitir siempre",
herramientas MCP). Por eso esta prediccion es deliberadamente
CONSERVADORA y quien la consume debe confirmarla observando que la
espera se sostiene en el tiempo (ver `sessions.confirm_pending`). Aqui
se prefieren los falsos negativos a los falsos positivos: no avisar es
molesto, avisar en falso destruye la confianza en el widget.
"""
import json
import re
from pathlib import Path

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

# Modos en los que Claude Code no interrumpe para pedir permiso.
NON_INTERACTIVE_MODES = ("dontask", "bypasspermissions", "acceptedits")

# Campo de cada herramienta que se compara con las reglas.
TOOL_SUBJECT_KEYS = {
    "Bash": "command",
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
}

# Ordenes de shell que escriben en disco.
WRITE_COMMANDS = (
    "cp", "copy", "mv", "move", "rm", "del", "rmdir", "mkdir", "touch",
    "tee", "sed", "chmod", "chown", "ren", "rename",
)
WRITE_REDIRECTS = (">", ">>")

WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s\"'|&;]+")


def load_permissions(settings_path=None) -> dict:
    path = Path(settings_path) if settings_path else SETTINGS_PATH
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("permissions", {})
    except (OSError, json.JSONDecodeError):
        return {}


def rule_matches(rule: str, tool_name: str, subject: str) -> bool:
    """Compara una regla tipo `Bash(git push*)` con la llamada actual."""
    if "(" not in rule:
        return rule == tool_name
    rule_tool, _, rest = rule.partition("(")
    if rule_tool != tool_name:
        return False
    pattern = rest.rstrip(")")
    if pattern.endswith("*"):
        return subject.startswith(pattern[:-1])
    return subject == pattern


def _is_inside(target: str, base: str) -> bool:
    try:
        return Path(target).resolve().is_relative_to(Path(base).resolve())
    except (OSError, ValueError):
        return True  # ante la duda, se considera dentro (conservador)


def bash_writes_outside(command: str, cwd: str, safe_dirs=()) -> bool:
    """Detecta escrituras a rutas ajenas al directorio de trabajo.

    Claude Code confirma este tipo de escrituras aunque `Bash` este
    permitido en general. Se excluyen los directorios de trabajo
    temporales, que la propia herramienta autoriza sin preguntar.
    """
    if not command or not cwd:
        return False

    lowered = command.lower()
    words = {w.strip("\"'") for w in re.split(r"[\s|&;]+", lowered) if w}
    if not (words & set(WRITE_COMMANDS) or any(r in command for r in WRITE_REDIRECTS)):
        return False

    for raw in WINDOWS_PATH_RE.findall(command):
        target = raw.strip("\"'")
        if _is_inside(target, cwd):
            continue
        if any(safe and _is_inside(target, safe) for safe in safe_dirs):
            continue
        return True
    return False


def needs_permission(
    tool_name: str,
    tool_input: dict,
    cwd: str = "",
    settings_path=None,
    safe_dirs=(),
) -> bool:
    """Predice si esta llamada va a provocar un dialogo de confirmacion."""
    perms = load_permissions(settings_path)
    if not perms:
        return False

    subject = str(tool_input.get(TOOL_SUBJECT_KEYS.get(tool_name, ""), ""))

    # `deny` bloquea siempre, en cualquier modo.
    for rule in perms.get("deny", []):
        if rule_matches(rule, tool_name, subject):
            return True

    # En modo automatico no hay dialogos por reglas `ask`.
    mode = str(perms.get("defaultMode", "")).strip().lower()
    if mode not in NON_INTERACTIVE_MODES:
        for rule in perms.get("ask", []):
            if rule_matches(rule, tool_name, subject):
                return True

    # Escrituras fuera del espacio de trabajo: se confirman incluso en
    # modo automatico. Solo se evalua Bash; para Edit/Write da demasiados
    # falsos positivos cuando la sesion se abrio fuera del proyecto.
    if tool_name == "Bash" and bash_writes_outside(subject, cwd, safe_dirs):
        return True

    return False
