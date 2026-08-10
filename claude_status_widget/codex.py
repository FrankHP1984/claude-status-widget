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

# Tope de lectura hacia atras buscando el evento de turno. Los rollouts
# de una sesion larga pasan de 3 MB; mas alla de esto se asume que no
# hay senal util y no merece la pena seguir leyendo en cada sondeo.
MAX_LECTURA = 4 * 1024 * 1024

# Si el turno sigue abierto pero el archivo lleva MUCHO tiempo callado,
# lo mas probable es que el chat se cerrara a medias: nunca llegara el
# `task_complete` que apaga el verde. El margen es generoso a proposito,
# porque una herramienta lenta (una compilacion, una bateria de tests)
# tambien deja el archivo quieto varios minutos sin estar muerta.
INACTIVO_SEGUNDOS = 15 * 60

TRABAJANDO = "trabajando"
TERMINADO = "terminado"
ESPERANDO = "esperando"

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
    datos = {"cwd": "", "origen": "", "title": "", "humana": None}
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
                    origen = payload.get("originator", "") or payload.get("source", "")
                    datos["origen"] = origen if isinstance(origen, str) else ""
                    # Codex abre un rollout propio por cada subagente que
                    # lanza, con el mismo cwd y el mismo aspecto que una
                    # conversacion. `thread_source` es lo que los separa:
                    # 'user' es lo que escribiste tu, 'subagent' es suyo.
                    hilo = payload.get("thread_source")
                    if hilo is not None:
                        datos["humana"] = hilo == "user"
                    elif payload.get("parent_thread_id"):
                        datos["humana"] = False
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


def _cola_con_turno(ruta: Path) -> list:
    """Lineas del final del archivo, hasta encontrar un evento de turno.

    Leer un trozo fijo del final no sirve: un turno largo escribe megas
    de razonamiento y llamadas a herramientas, y el `task_started` queda
    muy atras. Si no se encuentra, el estado se daba por terminado
    estando la sesion en marcha. Por eso se retrocede por bloques hasta
    dar con la senal, con un tope para no leer archivos enormes enteros.
    """
    try:
        tam = ruta.stat().st_size
    except OSError:
        return []

    leido = 0
    bloque = COLA_BYTES
    while True:
        leido = min(tam, max(bloque, leido * 4))
        try:
            with open(ruta, "rb") as f:
                f.seek(tam - leido)
                lineas = f.read().decode("utf-8", errors="replace").splitlines()
        except OSError:
            return []
        # La primera linea puede venir cortada por la mitad.
        if leido < tam:
            lineas = lineas[1:]
        if any('"task_started"' in l or '"task_complete"' in l for l in lineas):
            return lineas
        if leido >= tam or leido >= MAX_LECTURA:
            return lineas


def _pide_permiso(lineas: list) -> bool:
    """Hay una llamada esperando que el usuario escale privilegios.

    Codex no emite ningun evento de "pidiendo permiso". Lo que hace es
    dejar la llamada colgada: aparece un `function_call` que pide
    escalada y no llega su salida hasta que la persona responde.
    """
    for linea in reversed(lineas):
        d = _json(linea)
        if not d:
            continue
        payload = d.get("payload") or {}
        tipo = payload.get("type")
        if tipo in ("function_call_output", "custom_tool_call_output"):
            return False  # la ultima llamada ya se resolvio
        if tipo in ("function_call", "custom_tool_call"):
            return "require_escalated" in str(payload.get("arguments", ""))
    return False


def leer_estado(ruta: Path) -> str:
    """Estado de la sesion segun el ultimo turno escrito.

    Terminado si el ultimo evento de turno fue `task_complete`. Si el
    turno sigue abierto, esperando cuando hay una llamada pendiente de
    permiso, y trabajando en cualquier otro caso.
    """
    lineas = _cola_con_turno(ruta)
    if not lineas:
        return TERMINADO

    abierto = False
    for linea in lineas:
        d = _json(linea)
        if not d or d.get("type") != "event_msg":
            continue
        tipo = (d.get("payload") or {}).get("type")
        if tipo == "task_started":
            abierto = True
        elif tipo == "task_complete":
            abierto = False

    if not abierto:
        return TERMINADO
    return ESPERANDO if _pide_permiso(lineas) else TRABAJANDO


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
    que = {
        TRABAJANDO: "trabajando",
        ESPERANDO: "esperando permiso",
        TERMINADO: "turno terminado",
    }[estado]
    return f"Codex en {donde}: {que}"


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

        # La caducidad se aplica fuera de la cache: el estado cacheado
        # depende del archivo, pero esto depende del reloj, asi que un
        # turno abandonado se apaga solo aunque nada vuelva a escribirse.
        # No se toca "esperando": un permiso puede estar pendiente el
        # rato que tarde la persona en volver a la silla.
        if estado == TRABAJANDO and (now - mtime).total_seconds() > INACTIVO_SEGUNDOS:
            estado = TERMINADO

        # El identificador va al final del nombre del archivo.
        ident = ruta.stem[-36:]
        sesiones[PREFIJO + ident] = {
            "state": estado,
            "detail": _detalle(cabecera["origen"], estado),
            "title": cabecera["title"] or ruta.stem,
            "cwd": cabecera["cwd"],
            "source": "codex",
            # Manda `thread_source`; si esa version de Codex no lo
            # escribe, se cae al criterio de siempre: sin mensaje a mano
            # no es una conversacion de una persona.
            "interactive": (cabecera["humana"] if cabecera["humana"] is not None
                            else bool(cabecera["title"])),
            "updated_at": mtime.isoformat(),
            "started_at": _inicio(ruta, mtime),
        }

    if len(_cache) > 64:
        for clave in [k for k in _cache if not os.path.exists(k)]:
            _cache.pop(clave, None)
    return sesiones
