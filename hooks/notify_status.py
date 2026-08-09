"""Hook de Claude Code: traduce eventos de sesion a estado del widget.

Se registra en ~/.claude/settings.json para SessionStart,
UserPromptSubmit, PreToolUse, PostToolUse, PostToolUseFailure,
Notification y Stop. Claude Code entrega el payload por stdin en JSON.

Este archivo es deliberadamente delgado: solo traduce eventos a estados.
Toda la logica con sustancia vive en el paquete `claude_status_widget`,
donde esta cubierta por tests.

Rendimiento: se ejecuta en CADA accion, asi que debe costar poco. Por eso
`window_focus` (que arrastra psutil y pywin32, ~150 ms) se importa de
forma perezosa, y el titulo solo se relee cada cierto tiempo.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_status_widget import permissions, state_store, transcript  # noqa: E402

# Diario de invocaciones, para diagnosticar que dispara el cliente
# (CLI vs app de escritorio) y que no. Solo se escribe si existe o si
# la variable CLAUDE_STATUS_DEBUG lo pide: en produccion el hook no
# debe costar nada mas que un stat.
DEBUG_LOG = state_store.STATE_DIR / "hooks-debug.log"
DEBUG_LOG_MAX = 1024 * 1024


def log_call(payload: dict, result: str) -> None:
    try:
        if not DEBUG_LOG.exists() and not os.environ.get("CLAUDE_STATUS_DEBUG"):
            return
        if DEBUG_LOG.exists() and DEBUG_LOG.stat().st_size > DEBUG_LOG_MAX:
            DEBUG_LOG.unlink()
        cw = payload.get("context_window") or {}
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "at": datetime.now(timezone.utc).isoformat(),
                "event": payload.get("hook_event_name", ""),
                "session": payload.get("session_id", "")[:8],
                "used": cw.get("used_percentage"),
                "result": result,
            }) + "\n")
    except OSError:
        pass

# Cada cuanto se relee el transcript para refrescar el titulo. Releerlo
# entero cuesta cientos de ms en sesiones largas.
TITLE_REFRESH_SECONDS = 120

# El statusline de Claude Code entrega el medidor de contexto en este
# evento, que se dispara en cada turno. Solo se usa para el porcentaje
# de contexto: el estado de la sesion no se toca.
STATUS_EVENT = "Status"

# Directorios que Claude Code autoriza a escribir sin preguntar.
SAFE_WRITE_DIRS = (
    os.environ.get("TEMP", ""),
    os.environ.get("TMP", ""),
)

STATE_BY_EVENT = {
    "SessionStart": "iniciado",
    "UserPromptSubmit": "trabajando",
    "PreToolUse": "trabajando",
    # PostToolUse confirma que la herramienta corrio: sin el, un estado
    # "esperando" se quedaria colgado tras conceder el permiso.
    "PostToolUse": "trabajando",
    # Al cancelar un permiso la herramienta nunca corre, asi que
    # PostToolUse no llega y hace falta esta otra salida.
    "PostToolUseFailure": "trabajando",
    "Notification": "esperando",
    # Stop dispara al final de CADA turno, y eso es exactamente lo que
    # el panel llama "terminado": tarea completada, no sesion cerrada.
    # El siguiente UserPromptSubmit la devuelve a "trabajando".
    "Stop": "terminado",
    # SessionEnd es el cierre real de la terminal. Comparte estado con
    # Stop; los distingue el detalle.
    "SessionEnd": "terminado",
}


def build_detail(event_name: str, payload: dict) -> str:
    tool = payload.get("tool_name", "?")
    if event_name == "UserPromptSubmit":
        prompt = payload.get("prompt", "")
        return prompt[:80] + ("..." if len(prompt) > 80 else "")
    if event_name == "PreToolUse":
        return f"Usando {tool}"
    if event_name == "PostToolUse":
        return f"{tool} completado"
    if event_name == "PostToolUseFailure":
        return f"{tool} cancelado o fallido"
    if event_name == "Notification":
        return payload.get("message", "Esperando confirmacion")
    if event_name == "Stop":
        return "Tarea completada"
    if event_name == "SessionEnd":
        return "Sesion cerrada"
    if event_name == "SessionStart":
        return "Sesion iniciada"
    return event_name


def read_payload() -> dict:
    """Lee el evento de stdin.

    Se decodifica UTF-8 explicitamente: con la codificacion por defecto
    de la consola de Windows los acentos llegan corrompidos.
    """
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def title_is_stale(existing: dict, now: datetime) -> bool:
    checked = existing.get("title_checked_at", "")
    if not checked:
        return True
    parsed = transcript.parse_timestamp(checked)
    if parsed is None:
        return True
    return (now - parsed).total_seconds() > TITLE_REFRESH_SECONDS


def resolve_pids(existing: dict) -> dict:
    """PIDs de la ventana y la pestaña, para poder saltar a ellas.

    Solo se calculan una vez por sesion: importar psutil y pywin32 es lo
    mas caro de este hook.
    """
    if existing.get("focus_pid") and existing.get("shell_pid"):
        return {}
    try:
        from claude_status_widget import window_focus

        own = os.getpid()
        focus_pid = window_focus.find_focusable_ancestor_pid(own)
        shell_pid = window_focus.find_shell_ancestor_pid(own, focus_pid)
    except Exception:
        return {}

    found = {}
    if focus_pid:
        found["focus_pid"] = focus_pid
    if shell_pid:
        found["shell_pid"] = shell_pid
    return found


def update_context(payload: dict, session_id: str) -> str:
    """Escribe el medidor de contexto que entrega el statusline.

    El statusline corre en cada turno, asi que solo se reescribe
    status.json si algun porcentaje realmente cambio. Devuelve que
    se hizo, para el diario de depuracion.
    """
    context_window = payload.get("context_window") or {}
    used = context_window.get("used_percentage")
    remaining = context_window.get("remaining_percentage")
    model = (payload.get("model") or {}).get("display_name") or ""

    existing = state_store.load().get(session_id, {})
    fields = {}
    # El modelo se escribe aunque aun no haya medidor: `used` es null
    # hasta la primera respuesta, y la fila ya puede mostrarlo.
    if model and existing.get("model") != model:
        fields["model"] = model
    if used is not None and (existing.get("context_used_pct") != used
                             or existing.get("context_remaining_pct") != remaining):
        fields["context_used_pct"] = used
        fields["context_remaining_pct"] = remaining
        fields["context_checked_at"] = datetime.now(timezone.utc).isoformat()

    if not fields:
        return "contexto-null" if used is None else "contexto-sin-cambios"
    state_store.update_session(session_id, fields)
    return f"contexto-escrito:{used}"


def update_rate_limits(payload: dict) -> str:
    """Consumo del limite de uso: la bolsa que se resetea cada 5 horas.

    Ojo, no confundir con `context_window`: aquello es cuanto ocupa UNA
    conversacion, esto es cuanto has gastado de tu cuota, sumando todas.
    Es un dato de CUENTA, identico en todas las sesiones, asi que vive
    en una entrada aparte y no en la de cada sesion.

    `resets_at` viene en epoch (segundos UTC).
    """
    limits = payload.get("rate_limits") or {}
    five = limits.get("five_hour") or {}
    seven = limits.get("seven_day") or {}
    used = five.get("used_percentage")
    if used is None:
        return "limites-ausentes"

    fields = {
        "five_hour_pct": used,
        "five_hour_resets_at": five.get("resets_at"),
        "seven_day_pct": seven.get("used_percentage"),
        "seven_day_resets_at": seven.get("resets_at"),
    }
    existing = state_store.load().get(state_store.ACCOUNT_KEY, {})
    if all(existing.get(k) == v for k, v in fields.items()):
        return "limites-sin-cambios"
    # updated_at mantiene la entrada a salvo del recorte por antiguedad.
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    state_store.update_session(state_store.ACCOUNT_KEY, fields)
    return f"limites-escritos:{used}"


def statusline_output(payload: dict) -> str:
    """Linea de estado que se muestra en la terminal de Claude Code.

    Sin los porcentajes (sesion aun sin respuesta), solo el modelo.
    """
    model = (payload.get("model") or {}).get("display_name", "")
    used = (payload.get("context_window") or {}).get("used_percentage")
    prefix = f"[{model}] " if model else ""
    if used is None:
        return prefix.strip()
    return f"{prefix}{int(used)}% usado"


def is_statusline(payload: dict) -> bool:
    """El statusline no documenta `hook_event_name` en el payload actual.

    Solo trae `context_window`; los hooks de eventos normales nunca lo
    incluyen, asi que su presencia basta para distinguirlo.
    """
    return payload.get("hook_event_name") == STATUS_EVENT or "context_window" in payload


def main() -> None:
    payload = read_payload()
    session_id = payload.get("session_id")
    if not session_id:
        log_call(payload, "sin-sesion")
        return

    if is_statusline(payload):
        update_rate_limits(payload)
        result = update_context(payload, session_id)
        log_call(payload, result)
        print(statusline_output(payload))
        return

    event_name = payload.get("hook_event_name", "")
    state = STATE_BY_EVENT.get(event_name)
    if state is None:
        log_call(payload, f"ignorado:{event_name}")
        return
    log_call(payload, event_name)

    existing = state_store.load().get(session_id, {})

    # Claude Code tambien manda Notification cuando lleva un rato inactivo
    # tras terminar; eso no es una peticion de permiso real.
    if event_name == "Notification" and existing.get("state") == "terminado":
        return

    cwd = payload.get("cwd", "")
    detail = build_detail(event_name, payload)

    # Adelantarse al evento Notification, que Claude Code emite con varios
    # segundos de retraso. El widget confirma esta prediccion observando
    # que la espera se sostenga, asi que un falso positivo es inocuo.
    if event_name == "PreToolUse":
        tool_name = payload.get("tool_name", "?")
        tool_input = payload.get("tool_input") or {}
        if permissions.needs_permission(tool_name, tool_input, cwd,
                                        safe_dirs=SAFE_WRITE_DIRS):
            state = "esperando"
            subject = str(tool_input.get(
                permissions.TOOL_SUBJECT_KEYS.get(tool_name, ""), ""))
            detail = f"Permiso para {tool_name}"
            if subject:
                detail += f": {subject[:40]}"

    now = datetime.now(timezone.utc)
    stamp = now.isoformat()
    fields = {
        "state": state,
        "detail": detail,
        "cwd": cwd,
        "updated_at": stamp,
        # Distingue estas sesiones de las de OpenCode, que escriben en el
        # mismo status.json desde su propio plugin.
        "source": "claude",
    }
    only_if_absent = {"started_at": stamp}

    path = transcript.transcript_path(session_id, cwd,
                                      payload.get("transcript_path", ""))
    if path:
        # El widget lo usa para detectar cancelaciones con Escape, que no
        # generan ningun evento de hook.
        fields["transcript_path"] = str(path)

    if not existing.get("interactive") and transcript.read_first_typed_prompt(path):
        # Solo las sesiones con un mensaje escrito a mano son terminales
        # reales; las internas (subagentes, resumenes) se descartan.
        fields["interactive"] = True

    if (existing.get("interactive") or fields.get("interactive")) and title_is_stale(existing, now):
        title = transcript.session_title(path)
        if title:
            fields["title"] = transcript.build_title(title)
        fields["title_checked_at"] = stamp

    only_if_absent.update(resolve_pids(existing))
    state_store.update_session(session_id, fields, only_if_absent)


if __name__ == "__main__":
    main()
