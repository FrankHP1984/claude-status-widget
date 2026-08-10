"""Widget de estado: icono de bandeja + panel flotante.

Lee state_store (actualizado por hooks/notify_status.py) y muestra
un semaforo por sesion de Claude Code activa, en un panel oscuro
sin bordes de ventana, con tarjetas redondeadas por sesion.
"""
import json
import os
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
import winsound
from datetime import datetime, timezone
from pathlib import Path

import psutil
import pystray
import win32api
import win32event
import winerror
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from claude_status_widget import codex, sessions, state_store, window_focus  # noqa: E402

# Refresco muy corto: solo se relee un JSON pequeño y unicamente se repinta
# si la huella del contenido cambia, asi que el coste es despreciable.
REFRESH_MS = 150

# Codex no avisa de nada: hay que mirarle los archivos de sesion. Se
# hace cada pocos segundos, no en cada refresco, porque implica recorrer
# un directorio en vez de leer un JSON pequeño.
CODEX_POLL_MS = 2000

# Un permiso solo se muestra (y suena) si sigue pendiente pasado este
# tiempo. Los falsos positivos se resuelven solos en ~0,2 s porque la
# herramienta termina; los permisos de verdad persisten hasta que decidas.
PENDING_CONFIRM_SECONDS = 1.0
PANEL_W = 380
ROW_H = 74
ROW_GAP = 8
PAD = 12
TEXT_LEFT = 34
TEXT_RIGHT_MARGIN = 14
HEADER_H = 46
PANEL_MAX_H = 520

# Posicion por defecto: esquina superior derecha, bajo los botones de
# minimizar/maximizar/cerrar. El usuario puede arrastrarla a donde quiera.
SCREEN_MARGIN_X = 14
SCREEN_MARGIN_Y = 48

# Ligera transparencia para que no tape del todo lo que hay debajo.
PANEL_ALPHA = 0.92

BG_PANEL = "#0f1116"
BG_HEADER = "#151821"
BG_CARD = "#1a1e27"
BG_CARD_HOVER = "#222735"
BORDER = "#262b36"
TEXT_PRIMARY = "#f4f5f7"
TEXT_SECONDARY = "#98a0b0"
TEXT_MUTED = "#646c7d"

ACCENT = {
    "iniciado": "#98a0b0",   # gris: aun sin actividad
    "trabajando": "#2fd07a",  # verde: en marcha
    "esperando": "#ffb020",   # ambar: necesita tu permiso
    "terminado": "#4aa3ff",   # azul: tarea completada
    "error": "#ff5a5f",       # rojo: ha fallado
}
DEFAULT_ACCENT = "#98a0b0"

# Fondo de la etiqueta de estado: tinte oscuro del color de acento.
PILL_BG = {
    "iniciado": "#242a35",
    "trabajando": "#0f3524",
    "esperando": "#402d0c",
    "terminado": "#0f2a45",
    "error": "#3d1a1c",
}
DEFAULT_PILL_BG = "#242a35"

# Sonidos propios, incluidos en el repo. Se guardan en WAV porque winsound
# no reproduce MP3; los MP3 originales se conservan en assets/ como fuente.
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
SOUND_BY_STATE = {
    "terminado": str(ASSETS_DIR / "terminado.wav"),  # cello suave
    "esperando": str(ASSETS_DIR / "esperando.wav"),  # clic de madera
}
SETTINGS_FILE = state_store.STATE_DIR / "widget-settings.json"

LABELS = {
    "iniciado": "Iniciado",
    "trabajando": "Trabajando",
    "esperando": "Esperando confirmacion",
    "terminado": "Terminado",
    "error": "Error",
}

# Etiqueta corta por origen de la sesion. Las de Claude Code no llevan
# distintivo (son el caso por defecto).
SOURCE_TAGS = {
    "opencode": "OC",
}

FONT_TITLE = ("Segoe UI", 10, "bold")
FONT_BODY = ("Segoe UI", 9)
FONT_SMALL = ("Segoe UI", 8)
FONT_SMALL_BOLD = ("Segoe UI", 8, "bold")


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sound_enabled": True}


def save_settings(settings: dict) -> None:
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(
            json.dumps(settings, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def play_state_sound(state: str) -> None:
    """Reproduce el aviso sonoro sin bloquear la interfaz."""
    path = SOUND_BY_STATE.get(state)
    if not path or not Path(path).exists():
        return
    try:
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except RuntimeError:
        pass


def _work_area_bottom(fallback_height: int) -> int:
    """Borde inferior del escritorio util, sin contar la barra de tareas."""
    try:
        import ctypes
        from ctypes import wintypes

        SPI_GETWORKAREA = 0x0030
        rect = wintypes.RECT()
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
        )
        if ok and rect.bottom > 0:
            return rect.bottom
    except Exception:
        pass
    return fallback_height


def _fit_text(font: tkfont.Font, text: str, max_width: int) -> str:
    text = (text or "").strip()
    if not text or font.measure(text) <= max_width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid] + "…") <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…"


# La logica pura vive en claude_status_widget.sessions, donde esta
# cubierta por tests. Aqui solo se le da un alias local.
_parse_dt = sessions.parse_dt
_elapsed_label = sessions.elapsed_label
_session_label = sessions.session_label


def _active_sessions() -> list:
    return sessions.visible_sessions(state_store.load(), psutil.pid_exists)


def _aggregate_color(visibles: list) -> str:
    states = [entry.get("state") for _, entry in visibles]
    if "error" in states:
        return ACCENT["error"]
    if "esperando" in states:
        return ACCENT["esperando"]
    if "trabajando" in states:
        return ACCENT["trabajando"]
    if visibles:
        return ACCENT["terminado"]
    return DEFAULT_ACCENT


def _context_color(pct: int | None) -> str:
    """Color del medidor de contexto gastado: verde con margen, ambar cerca, rojo al filo."""
    if pct is None:
        return TEXT_SECONDARY
    if pct >= 80:
        return ACCENT["error"]
    if pct >= 50:
        return ACCENT["esperando"]
    return ACCENT["trabajando"]


def _make_dot_image(color: str, size: int = 64) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = size // 8
    draw.ellipse((margin, margin, size - margin, size - margin), fill=color)
    return img


def _round_rect(canvas: tk.Canvas, x1, y1, x2, y2, radius, **kwargs):
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class StatusWidget:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.panel = None
        self.canvas = None
        self.rows_frame = None
        self.tray_icon = None
        self._drag = {"x": 0, "y": 0}
        self._editing = None
        self._last_states = None
        self._last_signature = None
        self._last_pct = None
        self._codex_cache = {}
        self._codex_visto = 0.0
        self._pending_since = {}
        self.settings = load_settings()
        saved_pos = self.settings.get("panel_pos")
        self._manual_pos = tuple(saved_pos) if saved_pos else None

        self.font_header = tkfont.Font(family="Segoe UI Semibold", size=10)
        self.font_title = tkfont.Font(family="Segoe UI Semibold", size=10)
        self.font_body = tkfont.Font(family="Segoe UI", size=9)
        self.font_small_bold = tkfont.Font(family="Segoe UI Semibold", size=8)
        self.font_small = tkfont.Font(family="Segoe UI", size=8)
        self.count_badge = None

        self._build_panel()
        self._refresh_loop()
        # Por defecto arranca visible; si se cerro la ultima vez, se respeta.
        if self.settings.get("panel_visible", True):
            self.show_panel()

    # ---- construccion del panel ----

    def _build_panel(self):
        panel = tk.Toplevel(self.root)
        panel.overrideredirect(True)
        panel.attributes("-topmost", True)
        panel.attributes("-alpha", PANEL_ALPHA)
        panel.configure(bg=BORDER)  # el fondo hace de borde fino de 1px

        inner = tk.Frame(panel, bg=BG_PANEL)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        header = tk.Frame(inner, bg=BG_HEADER, height=HEADER_H)
        header.pack(fill="x")
        header.pack_propagate(False)

        title = tk.Label(
            header, text="Sesiones activas", bg=BG_HEADER, fg=TEXT_PRIMARY,
            font=self.font_header, anchor="w",
        )
        title.pack(side="left", padx=(16, 8))

        self.count_badge = tk.Label(
            header, text="0", bg=BG_CARD, fg=TEXT_SECONDARY,
            font=self.font_small_bold, padx=8, pady=1,
        )
        self.count_badge.pack(side="left")

        # Medidor de contexto de la sesion actual de Claude: porcentaje de
        # contexto gastado, tal y como lo calcula el propio Claude Code
        # (statusline). Vacio si ninguna sesion lo reporta.
        self.context_label = tk.Label(
            header, text="", bg=BG_HEADER, fg=TEXT_SECONDARY,
            font=self.font_small_bold,
        )
        self.context_label.pack(side="left", padx=(10, 0))

        close_btn = tk.Label(
            header, text="✕", bg=BG_HEADER, fg=TEXT_MUTED,
            font=("Segoe UI", 11), cursor="hand2", padx=6,
        )
        close_btn.pack(side="right", padx=(0, 10))
        close_btn.bind("<Button-1>", self.hide_panel)
        close_btn.bind("<Enter>", lambda e: close_btn.config(fg=TEXT_PRIMARY))
        close_btn.bind("<Leave>", lambda e: close_btn.config(fg=TEXT_MUTED))

        for widget in (header, title, self.count_badge, self.context_label):
            widget.bind("<ButtonPress-1>", self._start_move)
            widget.bind("<B1-Motion>", self._do_move)
            widget.bind("<ButtonRelease-1>", self._end_move)
            widget.config(cursor="fleur")  # indica que se puede arrastrar

        separator = tk.Frame(inner, bg=BORDER, height=1)
        separator.pack(fill="x")

        body = tk.Frame(inner, bg=BG_PANEL)
        body.pack(fill="both", expand=True, pady=(PAD, PAD))

        canvas = tk.Canvas(body, bg=BG_PANEL, highlightthickness=0)
        scrollbar = tk.Scrollbar(body, orient="vertical", command=canvas.yview)
        rows_frame = tk.Frame(canvas, bg=BG_PANEL)

        rows_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=rows_frame, anchor="nw", width=PANEL_W)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.panel = panel
        self.canvas = canvas
        self.rows_frame = rows_frame
        panel.withdraw()

    def _start_move(self, event):
        self._drag["x"] = event.x
        self._drag["y"] = event.y

    def _do_move(self, event):
        x = self.panel.winfo_pointerx() - self._drag["x"]
        y = self.panel.winfo_pointery() - self._drag["y"]
        self.panel.geometry(f"+{x}+{y}")
        # Recordar la posicion: si no, el refresco la devolveria a su sitio.
        self._manual_pos = (x, y)

    def _end_move(self, event):
        if self._manual_pos:
            self.settings["panel_pos"] = list(self._manual_pos)
            save_settings(self.settings)

    # ---- visibilidad ----

    def _position_panel(self, session_count: int):
        """Ajusta el alto al contenido, respetando donde lo haya puesto el usuario."""
        rows_h = max(session_count, 1) * (ROW_H + ROW_GAP)
        height = min(HEADER_H + 1 + 2 * PAD + rows_h, PANEL_MAX_H)

        if self._manual_pos:
            x, y = self._manual_pos
        else:
            x = self.panel.winfo_screenwidth() - PANEL_W - SCREEN_MARGIN_X
            y = SCREEN_MARGIN_Y

        self.panel.geometry(f"{PANEL_W}x{height}+{x}+{y}")

    def _reset_position(self):
        self._manual_pos = None
        self.settings.pop("panel_pos", None)
        save_settings(self.settings)
        self._position_panel(len(_active_sessions()))

    def show_panel(self, *_):
        self._position_panel(len(_active_sessions()))
        self.panel.deiconify()
        self.panel.lift()
        self._remember_visibility(True)

    def hide_panel(self, *_):
        self.panel.withdraw()
        self._remember_visibility(False)

    def _remember_visibility(self, visible: bool):
        if self.settings.get("panel_visible") != visible:
            self.settings["panel_visible"] = visible
            save_settings(self.settings)

    def toggle_panel(self, *_):
        if self.panel.state() == "withdrawn":
            self.show_panel()
        else:
            self.hide_panel()

    def _focus_session(self, pid: int, shell_pid=None):
        try:
            window_focus.switch_to_tab(pid, shell_pid)
        except Exception:
            pass

    # ---- renombrado ----

    def _begin_rename(self, session_id: str, row: tk.Canvas, title_id: int):
        if self._editing:
            return "break"
        self._editing = session_id

        entry_data = state_store.load().get(session_id, {})
        current = _session_label(session_id, entry_data)

        box = tk.Entry(
            row, bg=BG_CARD_HOVER, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
            relief="flat", font=self.font_title, highlightthickness=1,
            highlightbackground=ACCENT["trabajando"], highlightcolor=ACCENT["trabajando"],
        )
        box.insert(0, current)
        box.select_range(0, "end")
        width = PANEL_W - 2 * PAD
        box.place(x=TEXT_LEFT - 4, y=6, width=width - TEXT_LEFT - 10, height=24)
        box.focus_force()

        box.bind("<Return>", lambda e: self._commit_rename(session_id, box.get()))
        box.bind("<Escape>", lambda e: self._cancel_rename())
        box.bind("<FocusOut>", lambda e: self._commit_rename(session_id, box.get()))
        return "break"

    def _commit_rename(self, session_id: str, text: str):
        text = (text or "").strip()
        # Vaciar el campo devuelve el nombre automatico de Claude Code.
        state_store.update_session(session_id, {"custom_title": text})
        self._end_rename()

    def _cancel_rename(self):
        self._end_rename()

    def _end_rename(self):
        self._editing = None
        self._render_rows(self._confirm_pending(_active_sessions()))

    # ---- renderizado ----

    def _render_rows(self, visibles: list):
        for child in self.rows_frame.winfo_children():
            child.destroy()

        if not visibles:
            empty = tk.Label(
                self.rows_frame, text="Sin sesiones activas", bg=BG_PANEL,
                fg=TEXT_MUTED, font=FONT_BODY,
            )
            empty.pack(pady=30)
            return

        for session_id, entry in visibles:
            self._render_row(session_id, entry)

    def _render_row(self, session_id: str, entry: dict):
        state = entry.get("state", "")
        color = ACCENT.get(state, DEFAULT_ACCENT)
        raw_label = _session_label(session_id, entry)
        raw_detail = entry.get("detail", "")
        elapsed = _elapsed_label(entry.get("started_at", ""))
        status_text = LABELS.get(state, state)
        source_tag = SOURCE_TAGS.get(entry.get("source", ""))
        if source_tag:
            status_text = f"{source_tag} · {status_text}"

        # A la derecha de la pastilla: modelo, contexto propio de esta
        # sesion y tiempo transcurrido. Si no cabe entero se van cayendo
        # campos por el final, que es lo menos valioso.
        meta = sessions.session_meta(entry)
        right_text = " · ".join(p for p in (meta, elapsed) if p)

        width = PANEL_W - 2 * PAD
        full_width = width - TEXT_LEFT - TEXT_RIGHT_MARGIN
        status_width = self.font_small_bold.measure(status_text)
        line3_left_width = full_width - status_width - 16
        while right_text and self.font_small.measure(right_text) >= line3_left_width:
            right_text = right_text.rpartition(" · ")[0]

        label = _fit_text(self.font_title, raw_label, full_width)
        detail = _fit_text(self.font_body, raw_detail, full_width)

        focus_pid = entry.get("focus_pid")
        shell_pid = entry.get("shell_pid")
        clickable = bool(focus_pid)

        row = tk.Canvas(
            self.rows_frame, width=width, height=ROW_H, bg=BG_PANEL, highlightthickness=0,
            cursor="hand2" if clickable else "arrow",
        )
        row.pack(pady=(0, ROW_GAP), padx=PAD)

        card_id = _round_rect(row, 0, 0, width, ROW_H, 12, fill=BG_CARD, outline="")
        if clickable:
            handler = lambda e, p=focus_pid, s=shell_pid: self._focus_session(p, s)
            row.tag_bind(card_id, "<Button-1>", handler)
            row.bind("<Button-1>", handler)
            row.bind("<Enter>", lambda e, c=row: c.itemconfig(card_id, fill=BG_CARD_HOVER))
            row.bind("<Leave>", lambda e, c=row: c.itemconfig(card_id, fill=BG_CARD))

        dot_y = 20
        row.create_oval(14, dot_y - 4, 22, dot_y + 4, fill=color, outline="")

        title_id = row.create_text(
            TEXT_LEFT, 18, anchor="w", text=label, fill=TEXT_PRIMARY, font=self.font_title,
        )
        # Clic sobre el nombre = renombrar; clic en el resto = ir a la terminal.
        row.tag_bind(
            title_id, "<Button-1>",
            lambda e, sid=session_id, c=row, t=title_id: self._begin_rename(sid, c, t),
        )
        row.tag_bind(title_id, "<Enter>", lambda e, c=row, t=title_id: c.itemconfig(t, fill=ACCENT["trabajando"]))
        row.tag_bind(title_id, "<Leave>", lambda e, c=row, t=title_id: c.itemconfig(t, fill=TEXT_PRIMARY))
        row.create_text(
            TEXT_LEFT, 39, anchor="w", text=detail, fill=TEXT_SECONDARY, font=self.font_body,
        )

        # Etiqueta de estado tipo pastilla, con el tiempo transcurrido al lado.
        pill_pad = 9
        pill_h = 17
        pill_y = 52
        pill_w = status_width + 2 * pill_pad
        _round_rect(
            row, TEXT_LEFT, pill_y, TEXT_LEFT + pill_w, pill_y + pill_h, 8,
            fill=PILL_BG.get(state, DEFAULT_PILL_BG), outline="",
        )
        row.create_text(
            TEXT_LEFT + pill_pad, pill_y + pill_h // 2, anchor="w",
            text=status_text, fill=color, font=self.font_small_bold,
        )
        if right_text:
            row.create_text(
                width - TEXT_RIGHT_MARGIN, pill_y + pill_h // 2, anchor="e",
                text=right_text, fill=TEXT_MUTED, font=self.font_small,
            )

    def _confirm_pending(self, visibles: list) -> list:
        """Retrasa mostrar una espera hasta confirmar que se sostiene."""
        return sessions.confirm_pending(visibles, self._pending_since, time.monotonic())

    def _announce_changes(self, visibles: list):
        """Suena solo cuando una sesion CAMBIA a terminado o esperando."""
        current = {sid: entry.get("state") for sid, entry in visibles}
        if self._last_states is not None and self.settings.get("sound_enabled"):
            for sid, state in current.items():
                previous = self._last_states.get(sid)
                if previous is not None and previous != state:
                    play_state_sound(state)
        self._last_states = current

    def _signature(self, visibles: list):
        """Huella del contenido visible: evita repintar si nada ha cambiado."""
        return tuple(
            (
                sid,
                entry.get("state"),
                entry.get("detail"),
                _session_label(sid, entry),
                _elapsed_label(entry.get("started_at", "")),
                # Sin esto la fila no se repinta al cambiar de modelo ni
                # al avanzar su medidor de contexto.
                sessions.session_meta(entry),
            )
            for sid, entry in visibles
        )

    def _codex(self) -> dict:
        """Sesiones de Codex, releidas como mucho cada CODEX_POLL_MS.

        Codex no tiene hooks, asi que esta es la unica via; se sondea
        despacio para que el refresco de 150 ms siga siendo gratis.
        """
        ahora = time.monotonic()
        if ahora - self._codex_visto < CODEX_POLL_MS / 1000:
            return self._codex_cache
        self._codex_visto = ahora
        try:
            self._codex_cache = codex.leer_sesiones()
        except Exception:
            # Nunca tumbar el panel por una fuente secundaria.
            self._codex_cache = {}
        return self._codex_cache

    def _refresh_loop(self):
        # El sonido y el repintado usan ya el estado confirmado, asi que
        # una espera fugaz nunca llega a verse ni a oirse.
        data = state_store.load()
        data.update(self._codex())
        visibles = self._confirm_pending(sessions.visible_sessions(data, psutil.pid_exists))
        self._announce_changes(visibles)

        # La cabecera muestra el consumo del limite de uso (la bolsa de
        # 5 horas), no el contexto de ninguna conversacion: es el numero
        # que cuadra con /usage. El contexto vive en cada fila.
        pct = sessions.usage_pct(data)
        resets = sessions.resets_label(data)
        if (pct, resets) != self._last_pct:
            self._last_pct = (pct, resets)
            if pct is None:
                text = ""
            else:
                text = f"{pct}%" + (f" · {resets}" if resets else "")
            self.context_label.config(text=text, fg=_context_color(pct))

        signature = self._signature(visibles)
        # Mientras se renombra no se repinta: destruiria el campo de texto.
        if not self._editing and signature != self._last_signature:
            self._render_rows(visibles)
            if self.count_badge:
                self.count_badge.config(text=str(len(visibles)))
            if self.panel.state() != "withdrawn":
                self._position_panel(len(visibles))
            if self.tray_icon:
                self.tray_icon.icon = _make_dot_image(_aggregate_color(visibles))
            self._last_signature = signature

        self.root.after(REFRESH_MS, self._refresh_loop)

    # ---- bandeja ----

    def _toggle_sound(self):
        self.settings["sound_enabled"] = not self.settings.get("sound_enabled", True)
        save_settings(self.settings)

    def _build_tray(self):
        menu = pystray.Menu(
            # default=True hace que este sea el que responde al doble clic
            # sobre el icono de la bandeja.
            pystray.MenuItem(
                "Mostrar panel",
                lambda: self.root.after(0, self.show_panel),
                default=True,
            ),
            pystray.MenuItem("Mostrar/ocultar panel", lambda: self.root.after(0, self.toggle_panel)),
            pystray.MenuItem(
                "Avisar con sonido",
                lambda: self._toggle_sound(),
                checked=lambda item: self.settings.get("sound_enabled", True),
            ),
            pystray.MenuItem(
                "Restablecer posicion", lambda: self.root.after(0, self._reset_position)
            ),
            pystray.MenuItem("Salir", lambda: self.root.after(0, self.quit)),
        )
        self.tray_icon = pystray.Icon(
            "claude-status-widget", _make_dot_image(DEFAULT_ACCENT), "Claude - estado", menu,
        )
        self.tray_icon.run_detached()

    def quit(self, *_):
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.quit()

    def run(self):
        self._build_tray()
        self.root.mainloop()


# Con un acceso directo en el escritorio es facil hacer doble clic dos
# veces y acabar con dos paneles superpuestos leyendo el mismo archivo.
# El mutex vive mientras viva el proceso y lo libera Windows al morir.
SINGLE_INSTANCE_MUTEX = "claude-status-widget-panel"
_mutex_handle = None


def _already_running() -> bool:
    global _mutex_handle
    try:
        _mutex_handle = win32event.CreateMutex(None, False, SINGLE_INSTANCE_MUTEX)
        return win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS
    except Exception:
        # Sin mutex se arranca igual: es una comodidad, no un requisito.
        return False


def main():
    if _already_running():
        print("El panel ya esta abierto (mira el icono de la bandeja).")
        return
    StatusWidget().run()


if __name__ == "__main__":
    main()
