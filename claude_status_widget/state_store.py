"""Almacén de estado compartido para las sesiones de Claude Code.

Los hooks escriben aqui; el widget (bandeja + panel) lee de aqui.
El archivo vive fuera del repo, en %LOCALAPPDATA%, para no versionar
estado runtime.
"""
import json
import os
import tempfile
import time
from pathlib import Path

STATE_DIR = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "claude-status-widget"
STATE_FILE = STATE_DIR / "status.json"
LOCK_FILE = STATE_DIR / "status.lock"

# Sesiones sin actividad durante mas de esto se consideran obsoletas
# y se ocultan en el panel (no se borran del archivo, solo se filtran).
STALE_SECONDS = 60 * 60 * 6  # 6 horas

# Tope de sesiones guardadas en disco.
MAX_ENTRIES = 50


def _ensure_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def load() -> dict:
    _ensure_dir()
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save(data: dict) -> None:
    _ensure_dir()
    tmp_fd, tmp_path = tempfile.mkstemp(dir=STATE_DIR, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, STATE_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


class _FileLock:
    """Lock entre procesos basado en la creacion exclusiva de un archivo.

    Evita que dos hooks que se disparan casi a la vez (p.ej. PreToolUse y
    UserPromptSubmit) se pisen el uno al otro al leer-modificar-escribir
    el mismo status.json.
    """

    def __init__(self, path: Path, timeout: float = 5.0, poll: float = 0.05):
        self.path = path
        self.timeout = timeout
        self.poll = poll
        self._fd = None

    def __enter__(self):
        _ensure_dir()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                return self
            except FileExistsError:
                if time.monotonic() > deadline:
                    # Lock huerfano (proceso que murio sin liberarlo): lo
                    # rompemos para no bloquear el widget para siempre.
                    try:
                        os.remove(self.path)
                    except OSError:
                        pass
                    continue
                time.sleep(self.poll)

    def __exit__(self, *exc_info):
        if self._fd is not None:
            os.close(self._fd)
        try:
            os.remove(self.path)
        except OSError:
            pass


def update_session(session_id: str, fields: dict | None = None, only_if_absent: dict | None = None) -> None:
    """Actualiza una sesion de forma atomica.

    `fields` se aplica siempre. `only_if_absent` solo se escribe si la
    sesion aun no tenia ese campo (o estaba vacio) — util para title y
    started_at, que deben fijarse una sola vez.
    """
    fields = fields or {}
    only_if_absent = only_if_absent or {}
    with _FileLock(LOCK_FILE):
        data = load()
        entry = data.get(session_id, {})
        for key, value in only_if_absent.items():
            if not entry.get(key):
                entry[key] = value
        entry.update(fields)
        data[session_id] = entry
        save(prune(data, keep=session_id))


def prune(data: dict, keep: str | None = None, max_entries: int = MAX_ENTRIES) -> dict:
    """Recorta el historico para que el archivo no crezca sin limite."""
    if len(data) <= max_entries:
        return data
    ordered = sorted(
        data.items(), key=lambda item: item[1].get("updated_at", ""), reverse=True
    )
    kept = dict(ordered[:max_entries])
    if keep and keep in data:
        kept[keep] = data[keep]
    return kept
