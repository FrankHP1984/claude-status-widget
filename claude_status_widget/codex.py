"""Sesiones de Codex (OpenAI), leidas de sus archivos de sesion.

Codex no ofrece hooks como Claude Code. Su unico enganche, `notify`,
admite un solo comando y suele estar ya ocupado (en una instalacion con
computer-use lo usa el propio Codex), asi que pisarlo romperia
funcionalidad ajena. En cambio Codex escribe cada conversacion en
`~/.codex/sessions/AAAA/MM/DD/rollout-*.jsonl`, y eso si se puede leer
sin molestar a nadie.

De ahi sale menos informacion que de los hooks:

- Hay estado de turno (`task_started` / `task_complete`), asi que se
  distingue trabajando de terminado.
- NO hay ninguna senal de permiso pendiente, asi que las filas de Codex
  nunca se pondran en ambar.

El precio es sondear: hay que mirar las fechas de los archivos cada
pocos segundos. Para que salga barato solo se miran los rollouts
recientes y se cachea lo ya parseado por (ruta, mtime), de modo que un
refresco sin cambios cuesta un `stat` por archivo.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Prefijo de los identificadores, para no chocar con las sesiones de
# Claude Code ni con las de OpenCode dentro del mismo estado.
PREFIJO = "codex:"

# Igual que las sesiones de Claude: pasado este tiempo sin tocarse, la
# conversacion se considera abandonada y deja de mostrarse.
STALE_SECONDS = 60 * 60 * 6

# Cola del archivo que se lee para averiguar el estado. Los rollouts
# crecen hasta varios MB y solo interesa el ultimo evento de turno.
COLA_BYTES = 64 * 1024

# Tope de lineas que se leen desde el principio buscando el titulo. El
# primer mensaje del usuario aparece siempre al inicio, detras de la
# metadata y las instrucciones del sistema.
LINEAS_TITULO = 400

TRABAJANDO = "trabajando"
TERMINADO = "terminado"

_cache: dict = {}


def directorio_sesiones() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")) / "sessions"


def _json(linea: str):
    try:
        return json.loads(linea)
    except (json.JSONDecodeError, ValueError):
        return None


def _texto(payload: dict) -> str:
    """Saca el texto de un mensaje, que Codex escribe de varias formas."""
    for clave in ("message", "text", "content"):
        valor = payload.get(clave)
        if isinstance(valor, str) and valor.strip():
            return valor
        if isinstance(valor, list):
            partes = [p.get("text", "") for p in valor if isinstance(p, dict)]
            junto = " ".join(t for t in partes if t)
            if junto.strip():
                return junto
    return ""


def leer_cabecera(ruta: Path) -> dict:
    """Metadata y titulo: primera linea y primeros mensajes del usuario."""
    datos = {"cwd": "", "origen": "", "title": ""}
    try:
        with open(ruta, encoding="utf-8", errors="replace") as f:
            for i, linea in enumerate(f):
                if i > LINEAS_TITULO:
                    break
                d = _json(linea)
                if not d:
                    continue
                payload = d.get("payload") or {}
                if d.get("type") == "session_meta":
                    datos["cwd"] = payload.get("cwd", "") or ""
                    datos["origen"] = payload.get("originator", "") or payload.get("source", "") or ""
                elif not datos["title"] and payload.get("type") == "user_message":
                    texto = _texto(payload).strip()
                    # Los mensajes que abren con '<' son plantillas del
                    # sistema (instrucciones, skills), no lo que escribio
                    # la persona.
                    if texto and not texto.startswith("<"):
                        datos["title"] = " ".join(texto.split())[:80]
    except OSError:
        pass
    return datos


def leer_estado(ruta: Path) -> str:
    """Ultimo evento de turno del archivo: dice si sigue trabajando."""
    try:
        with open(ruta, "rb") as f:
            f.seek(0, os.SEEK_END)
            inicio = max(0, f.tell() - COLA_BYTES)
            f.seek(inicio)
            cola = f.read().decode("utf-8", errors="replace")
    except OSError:
        return TERMINADO

    estado = TERMINADO
    for linea in cola.splitlines():
        d = _json(linea)
        if not d or d.get("type") != "event_msg":
            continue
        tipo = (d.get("payload") or {}).get("type")
        if tipo == "task_started":
            estado = TRABAJANDO
        elif tipo == "task_complete":
            estado = TERMINADO
    return estado


def _rollouts_recientes(base: Path, limite: datetime) -> list:
    if not base.is_dir():
        return []
    encontrados = []
    for ruta in base.rglob("rollout-*.jsonl"):
        try:
            mtime = datetime.fromtimestamp(ruta.stat().st_mtime, timezone.utc)
        except OSError:
            continue
        if mtime >= limite:
            encontrados.append((ruta, mtime))
    return encontrados


def _detalle(origen: str, estado: str) -> str:
    donde = "VS Code" if "vscode" in (origen or "").lower() else "terminal"
    return f"Codex en {donde}: " + ("trabajando" if estado == TRABAJANDO else "turno terminado")


def _inicio(ruta: Path, mtime: datetime) -> str:
    try:
        return datetime.fromtimestamp(ruta.stat().st_ctime, timezone.utc).isoformat()
    except OSError:
        return mtime.isoformat()


def leer_sesiones(base=None, now=None) -> dict:
    """Sesiones de Codex vivas, con la misma forma que las de Claude.

    El resultado se mezcla con `status.json` en memoria; nada de esto se
    escribe en disco, porque la fuente de verdad son los rollouts.
    """
    now = now or datetime.now(timezone.utc)
    base = Path(base) if base else directorio_sesiones()
    limite = now - timedelta(seconds=STALE_SECONDS)

    sesiones = {}
    for ruta, mtime in _rollouts_recientes(base, limite):
        clave = str(ruta)
        cacheado = _cache.get(clave)
        if cacheado and cacheado["mtime"] == mtime:
            cabecera, estado = cacheado["cabecera"], cacheado["estado"]
        else:
            # La cabecera no cambia nunca; solo se relee si no estaba.
            cabecera = cacheado["cabecera"] if cacheado else leer_cabecera(ruta)
            estado = leer_estado(ruta)
            _cache[clave] = {"mtime": mtime, "cabecera": cabecera, "estado": estado}

        # El identificador va al final del nombre del archivo.
        ident = ruta.stem[-36:]
        sesiones[PREFIJO + ident] = {
            "state": estado,
            "detail": _detalle(cabecera["origen"], estado),
            "title": cabecera["title"] or ruta.stem,
            "cwd": cabecera["cwd"],
            "source": "codex",
            # Mismo criterio que con Claude Code: sin un mensaje escrito
            # a mano no es una conversacion de una persona, sino un
            # resumen o una sesion interna, y no se pinta.
            "interactive": bool(cabecera["title"]),
            "updated_at": mtime.isoformat(),
            "started_at": _inicio(ruta, mtime),
        }

    if len(_cache) > 64:
        for clave in [k for k in _cache if not os.path.exists(k)]:
            _cache.pop(clave, None)
    return sesiones
