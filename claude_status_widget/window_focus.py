"""Localiza y enfoca la ventana de terminal asociada a un proceso.

Usado por el hook (para averiguar que PID de ventana corresponde a la
sesion) y por el widget (para traer esa ventana al frente al hacer clic).
"""
import os
import time

import psutil
import win32api
import win32con
import win32gui
import win32process

SHELL_NAMES = {"powershell.exe", "pwsh.exe", "cmd.exe", "bash.exe", "wsl.exe"}

VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
KEYEVENTF_KEYUP = 0x0002


def _visible_window_pids() -> dict:
    pid_to_hwnd = {}

    def handler(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            pid_to_hwnd.setdefault(pid, hwnd)

    win32gui.EnumWindows(handler, None)
    return pid_to_hwnd


def elegir_ventana(fragmento: str, ventanas: list):
    """PID de la ventana que corresponde a un proyecto, o None.

    Las sesiones de Codex no traen ningun PID: viven dentro del editor,
    no en una terminal propia. Lo unico que las ata a una ventana es el
    nombre de la carpeta, que los editores ponen en el titulo.

    `ventanas` es una lista de (pid, nombre_proceso, titulo). Se prefiere
    un editor conocido, porque el mismo nombre de carpeta puede aparecer
    en un explorador o en esta misma conversacion.
    """
    if not fragmento:
        return None
    objetivo = fragmento.lower()
    candidatos = [v for v in ventanas if objetivo in (v[2] or "").lower()]
    if not candidatos:
        return None
    editores = [v for v in candidatos if any(
        e in (v[1] or "").lower() for e in ("code.exe", "cursor.exe", "windsurf.exe"))]
    return (editores or candidatos)[0][0]


def listar_ventanas() -> list:
    """Ventanas visibles con titulo: (pid, nombre_proceso, titulo)."""
    encontradas = []

    def handler(hwnd, _):
        if not (win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd)):
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            encontradas.append((pid, psutil.Process(pid).name(), win32gui.GetWindowText(hwnd)))
        except (psutil.Error, OSError):
            pass

    try:
        win32gui.EnumWindows(handler, None)
    except OSError:
        pass
    return encontradas


def focus_project_window(cwd: str) -> bool:
    """Trae al frente la ventana del editor abierto en ese directorio."""
    if not cwd:
        return False
    nombre = os.path.basename(str(cwd).replace("\\", "/").rstrip("/"))
    pid = elegir_ventana(nombre, listar_ventanas())
    return focus_pid(pid) if pid else False


def find_focusable_ancestor_pid(start_pid: int):
    """Sube por los procesos padre hasta encontrar uno con ventana visible."""
    visible = _visible_window_pids()
    try:
        proc = psutil.Process(start_pid)
    except psutil.Error:
        return None

    chain = [proc] + proc.parents()
    for ancestor in chain:
        if ancestor.pid in visible:
            return ancestor.pid
    return None


def find_shell_ancestor_pid(start_pid: int, window_pid=None):
    """Ancestro que representa la pestaña que contiene esta sesion.

    No vale con coger el primer shell de la cadena: los hooks se lanzan a
    traves de un bash intermedio, que no es la pestaña. La pestaña real es
    el ancestro que cuelga directamente del proceso de la ventana.
    """
    try:
        proc = psutil.Process(start_pid)
    except psutil.Error:
        return None

    chain = [proc] + proc.parents()

    if window_pid:
        for ancestor in chain:
            try:
                parent = ancestor.parent()
            except psutil.Error:
                continue
            if parent and parent.pid == window_pid:
                return ancestor.pid

    # Sin ventana conocida (terminal en ventana propia): el shell mas alto
    # de la cadena se aproxima mejor que el mas cercano.
    fallback = None
    for ancestor in chain:
        try:
            if ancestor.name().lower() in SHELL_NAMES:
                fallback = ancestor.pid
        except psutil.Error:
            continue
    return fallback


def _tab_index(window_pid: int, shell_pid: int):
    """Indice (1-based) de la pestaña, por orden de creacion de los shells.

    Windows no expone las pestañas de Windows Terminal como ventanas, asi
    que se infiere del orden en que se crearon los procesos hijos.
    """
    try:
        window_proc = psutil.Process(window_pid)
    except psutil.Error:
        return None

    shells = []
    for child in window_proc.children(recursive=False):
        try:
            if child.name().lower() in SHELL_NAMES:
                shells.append((child.create_time(), child.pid))
        except psutil.Error:
            continue

    if len(shells) < 2:
        return None

    shells.sort()
    for index, (_, pid) in enumerate(shells, start=1):
        if pid == shell_pid:
            return index
    return None


def _send_ctrl_alt_digit(digit: int) -> None:
    vk_digit = 0x30 + digit  # VK_0..VK_9
    win32api.keybd_event(VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(VK_MENU, 0, 0, 0)
    win32api.keybd_event(vk_digit, 0, 0, 0)
    time.sleep(0.02)
    win32api.keybd_event(vk_digit, 0, KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def switch_to_tab(window_pid: int, shell_pid=None) -> bool:
    """Enfoca la ventana y, si procede, salta a su pestaña concreta."""
    if not focus_pid(window_pid):
        return False

    if not shell_pid:
        return True

    index = _tab_index(window_pid, shell_pid)
    if index is None or index > 9:
        return True

    time.sleep(0.08)  # dar tiempo a que la ventana tome el foco
    _send_ctrl_alt_digit(index)
    return True


def focus_pid(pid: int) -> bool:
    pid_to_hwnd = _visible_window_pids()
    hwnd = pid_to_hwnd.get(pid)
    if not hwnd:
        return False

    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    try:
        win32gui.SetForegroundWindow(hwnd)
        return True
    except win32gui.error:
        pass

    fg_hwnd = win32gui.GetForegroundWindow()
    fg_thread, _ = win32process.GetWindowThreadProcessId(fg_hwnd)
    target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
    win32process.AttachThreadInput(fg_thread, target_thread, True)
    try:
        win32gui.SetForegroundWindow(hwnd)
        return True
    finally:
        win32process.AttachThreadInput(fg_thread, target_thread, False)
