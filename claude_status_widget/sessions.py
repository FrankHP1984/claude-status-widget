"""Reglas sobre que sesiones se muestran y en que estado.

Toda la maquina de estados visible vive aqui, sin dependencias graficas,
para poder probarla. `widget/app.py` solo pinta lo que este modulo
decide.

Las tres reglas, y el porque de cada una:

1. FILTRAR - solo sesiones interactivas y vivas.
2. RECONCILIAR - si se cancela con Escape, Claude Code no emite ningun
   hook, asi que "esperando" se quedaria colgado para siempre.
3. CONFIRMAR - una espera solo es real si se sostiene en el tiempo. Es
   la red de seguridad frente a los fallos de prediccion de
   `permissions`: los falsos positivos se desmienten solos en decimas
   de segundo, antes de mostrarse o sonar.
"""
import os
from datetime import datetime, timezone
from pathlib import Path

from . import transcript

# Sin actividad durante mas de esto, la sesion se considera obsoleta.
STALE_SECONDS = 60 * 60 * 6

# Una espera solo se muestra (y suena) si se mantiene este tiempo.
PENDING_CONFIRM_SECONDS = 1.0

WAITING = "esperando"
WORKING = "trabajando"
DONE = "terminado"


def parse_dt(value):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def elapsed_label(started_at: str, now=None) -> str:
    """Duracion en formato corto: 45s, 12m, 2h30m."""
    started = parse_dt(started_at)
    if started is None:
        return ""
    now = now or datetime.now(timezone.utc)
    seconds = int((now - started).total_seconds())
    if seconds < 0:
        return ""
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h{minutes % 60}m"


def session_label(session_id: str, entry: dict) -> str:
    """Nombre visible. El puesto a mano manda sobre el automatico."""
    for key in ("custom_title", "title"):
        value = (entry.get(key) or "").strip()
        if value:
            return value
    cwd = entry.get("cwd") or ""
    if cwd:
        return Path(cwd).name or cwd
    return session_id[:8]


def transcript_for(session_id: str, entry: dict):
    """Transcript de la sesion, derivandolo si el hook no lo dejo escrito.

    Una sesion atascada no recibe eventos nuevos, asi que no se puede
    depender de que el hook haya guardado la ruta.
    """
    if entry.get("source") == "opencode":
        return None  # OpenCode no genera transcripts de Claude Code
    return transcript.transcript_path(
        session_id, entry.get("cwd") or "", entry.get("transcript_path") or ""
    )


def is_alive(entry: dict, pid_exists) -> bool:
    """False si la terminal que alojaba la sesion ya se cerro."""
    for key in ("shell_pid", "focus_pid"):
        pid = entry.get(key)
        if pid and not pid_exists(pid):
            return False
    return True


def is_stale(entry: dict, now=None) -> bool:
    updated = parse_dt(entry.get("updated_at", ""))
    if updated is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - updated).total_seconds() > STALE_SECONDS


def reconcile_pending(session_id: str, entry: dict, now=None) -> dict:
    """Corrige una espera que ya no lo es.

    Mientras se espera de verdad, el transcript no crece. Si el archivo
    cambia despues de haber marcado la espera, la espera termino: o se
    concedio el permiso, o se cancelo. Solo se afirma "terminado" con una
    interrupcion FECHADA posterior; ante la duda, "trabajando".
    """
    if entry.get("state") != WAITING:
        return entry

    updated = parse_dt(entry.get("updated_at", ""))
    path = transcript_for(session_id, entry)
    if path is None or updated is None:
        return entry

    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)
    except OSError:
        return entry

    if mtime <= updated:
        return entry  # sigue esperando de verdad

    fixed = dict(entry)
    if transcript.was_interrupted_after(path, updated):
        fixed["state"] = DONE
        fixed["detail"] = "Cancelado por el usuario"
    else:
        fixed["state"] = WORKING
        fixed["detail"] = "Permiso concedido"
    return fixed


def visible_sessions(data: dict, pid_exists, now=None) -> list:
    """Sesiones que deben aparecer en el panel, ya reconciliadas."""
    now = now or datetime.now(timezone.utc)
    result = []
    for session_id, entry in data.items():
        # Las sesiones internas (subagentes, resumenes) nunca tienen un
        # mensaje escrito a mano y no son terminales del usuario.
        if not entry.get("interactive"):
            continue
        if is_stale(entry, now) or not is_alive(entry, pid_exists):
            continue
        result.append((session_id, reconcile_pending(session_id, entry, now)))

    result.sort(key=lambda item: item[1].get("updated_at", ""), reverse=True)
    return result


def confirm_pending(sessions: list, pending_since: dict, monotonic_now: float) -> list:
    """Oculta las esperas que no se sostienen el tiempo minimo.

    `pending_since` es un diccionario de estado que el llamante conserva
    entre invocaciones; se muta aqui para registrar cuando empezo cada
    espera y limpiar las que ya no lo son.
    """
    confirmed = []
    seen = set()

    for session_id, entry in sessions:
        if entry.get("state") != WAITING:
            pending_since.pop(session_id, None)
            confirmed.append((session_id, entry))
            continue

        seen.add(session_id)
        since = pending_since.setdefault(session_id, monotonic_now)
        if monotonic_now - since >= PENDING_CONFIRM_SECONDS:
            confirmed.append((session_id, entry))
        else:
            provisional = dict(entry)
            provisional["state"] = WORKING
            confirmed.append((session_id, provisional))

    for session_id in [s for s in pending_since if s not in seen]:
        pending_since.pop(session_id, None)

    return confirmed
