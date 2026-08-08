"""Resuelve focus_pid / shell_pid para un PID dado y los imprime como JSON.

Lo usa el plugin de OpenCode (JavaScript), que no puede recorrer el arbol de
procesos de Windows por si mismo. Reutiliza la misma logica que el hook de
Claude Code para que el clic en el widget lleve a la pestaña correcta.

Uso:  python hooks/resolve_pids.py <pid>
Sale: {"focus_pid": 24492, "shell_pid": 25036}   (campos ausentes si no hay)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from claude_status_widget import window_focus  # noqa: E402


def main() -> None:
    result = {}
    try:
        pid = int(sys.argv[1])
    except (IndexError, ValueError):
        print(json.dumps(result))
        return

    try:
        focus_pid = window_focus.find_focusable_ancestor_pid(pid)
        shell_pid = window_focus.find_shell_ancestor_pid(pid, focus_pid)
    except Exception:
        focus_pid = shell_pid = None

    if focus_pid:
        result["focus_pid"] = focus_pid
    if shell_pid:
        result["shell_pid"] = shell_pid
    print(json.dumps(result))


if __name__ == "__main__":
    main()
