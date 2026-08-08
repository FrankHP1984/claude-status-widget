"""Lectura de los transcripts de Claude Code.

AVISO DE ACOPLAMIENTO: este es el unico modulo que conoce el formato
interno de los transcripts (nombres de registro, campos, marcas de
interrupcion). No esta documentado por Anthropic y puede cambiar sin
previo aviso, asi que todo lo fragil se concentra aqui: si Claude Code
cambia algo, se arregla en este archivo y en ningun otro.
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

TITLE_MAX_CHARS = 42

INTERRUPT_MARKERS = (
    "[Request interrupted by user",
    "Request interrupted by user",
)


def encode_project_dir(cwd: str) -> str:
    """Codifica el cwd igual que Claude Code: dos puntos y barras pasan a guiones."""
    return cwd.replace(":", "-").replace("\\", "-").replace("/", "-")


def transcript_path(session_id: str, cwd: str, given: str = "") -> Path | None:
    """Ruta del JSONL de una sesion.

    Fuente unica de verdad: la usan tanto el hook (que suele recibir la
    ruta en el payload) como el widget (que debe derivarla porque una
    sesion atascada ya no genera eventos).
    """
    if given and Path(given).exists():
        return Path(given)
    if not session_id or not cwd:
        return None
    candidate = (
        Path.home() / ".claude" / "projects"
        / encode_project_dir(cwd) / f"{session_id}.jsonl"
    )
    return candidate if candidate.exists() else None


def _iter_records(path, tail_bytes: int | None = None):
    """Recorre los registros JSON, opcionalmente solo el final del archivo."""
    try:
        with open(path, "rb") as f:
            if tail_bytes is not None:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - tail_bytes))
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return
    for line in data.splitlines():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue  # linea partida por el corte del tail


def build_title(text: str, max_chars: int = TITLE_MAX_CHARS) -> str:
    """Recorta a un titulo corto, por frase y sin partir palabras."""
    text = (text or "").strip().replace("\n", " ")
    if not text:
        return ""
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    if len(first) <= max_chars:
        return first
    cut = first[:max_chars]
    space = cut.rfind(" ")
    if space > 10:
        cut = cut[:space]
    return cut + "\u2026"


def prettify_agent_name(name: str) -> str:
    """fix-status-widget-bugs -> Fix status widget bugs"""
    text = (name or "").replace("-", " ").replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else ""


def read_names(path) -> dict:
    """Nombres que Claude Code genera para la conversacion.

    `agent-name` es el que se ve en la pestaña de la terminal; `ai-title`
    es un resumen mas largo. Ambos se reescriben durante la sesion, asi
    que vale la ultima aparicion de cada uno.
    """
    names = {"agent_name": "", "ai_title": ""}
    if not path:
        return names
    for record in _iter_records(path):
        if record.get("type") == "agent-name" and record.get("agentName"):
            names["agent_name"] = record["agentName"]
        elif record.get("type") == "ai-title" and record.get("aiTitle"):
            names["ai_title"] = record["aiTitle"]
    return names


def read_first_typed_prompt(path) -> str:
    """Primer mensaje escrito a mano por el usuario.

    Ignora sidechains (subagentes) y prompts inyectados por hooks o
    comandos. Si no hay ninguno, la sesion no es una terminal real del
    usuario sino interna, y no debe mostrarse.
    """
    if not path:
        return ""
    for record in _iter_records(path):
        if (
            record.get("type") != "user"
            or record.get("isSidechain")
            or record.get("promptSource") != "typed"
        ):
            continue
        content = record.get("message", {}).get("content")
        if isinstance(content, list):
            content = " ".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def session_title(path) -> str:
    """Mejor nombre disponible, por orden de calidad descendente."""
    names = read_names(path)
    return (
        prettify_agent_name(names["agent_name"])
        or names["ai_title"]
        or read_first_typed_prompt(path)
    )


def parse_timestamp(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def was_interrupted_after(path, moment, tail_bytes: int = 65536) -> bool:
    """Busca una interrupcion FECHADA posterior a `moment`.

    Comprobar solo si aparece la marca no vale: una sesion larga acumula
    decenas de interrupciones antiguas y cualquiera daria un falso
    "cancelado".
    """
    if not path or moment is None:
        return False
    for record in _iter_records(path, tail_bytes=tail_bytes):
        blob = json.dumps(record, ensure_ascii=False)
        if not any(marker in blob for marker in INTERRUPT_MARKERS):
            continue
        ts = parse_timestamp(record.get("timestamp"))
        if ts is not None and ts > moment:
            return True
    return False
