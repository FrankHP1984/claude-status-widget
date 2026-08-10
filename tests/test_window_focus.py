"""Tests de la eleccion de ventana por nombre de proyecto.

Enfocar de verdad es Win32 y no se puede probar aqui, pero la decision
de A QUE ventana ir si es logica pura, y es donde estan los errores:
el nombre de una carpeta aparece en mas sitios de los que parece.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_status_widget import window_focus  # noqa: E402

VENTANAS = [
    (100, "explorer.exe", "rag-epistelaris"),
    (200, "Code.exe", "arquitectura.md - rag-epistelaris - Visual Studio Code"),
    (300, "WindowsTerminal.exe", "franc@equipo: C:\\proyectos\\otro"),
]


class TestElegirVentana:
    def test_prefiere_el_editor_sobre_el_explorador(self):
        # Los dos titulos contienen el nombre del proyecto; el editor es
        # el que tiene la sesion dentro.
        assert window_focus.elegir_ventana("rag-epistelaris", VENTANAS) == 200

    def test_sin_coincidencia_no_devuelve_nada(self):
        assert window_focus.elegir_ventana("proyecto-fantasma", VENTANAS) is None

    def test_sin_fragmento_no_adivina(self):
        assert window_focus.elegir_ventana("", VENTANAS) is None

    def test_no_distingue_mayusculas(self):
        assert window_focus.elegir_ventana("RAG-Epistelaris", VENTANAS) == 200

    def test_sin_editor_vale_cualquier_ventana(self):
        solo_explorador = [(100, "explorer.exe", "rag-epistelaris")]
        assert window_focus.elegir_ventana("rag-epistelaris", solo_explorador) == 100

    def test_tolera_titulos_vacios(self):
        assert window_focus.elegir_ventana("x", [(1, "a.exe", None)]) is None


class TestFocusProjectWindow:
    """Recorre la funcion entera, no solo la decision.

    Existe por un fallo real: faltaba `import os` y la funcion reventaba
    con NameError en el primer clic. Los tests de `elegir_ventana` no lo
    vieron porque nunca la ejecutaban. Aqui se recorre el camino
    completo contra un proyecto que no existe: sin abrir ninguna
    ventana, cualquier error de nombre o de import salta.
    """

    def test_proyecto_inexistente_devuelve_false(self):
        assert window_focus.focus_project_window(
            "C:/proyectos/carpeta-que-no-existe-jamas-9f3a") is False

    def test_cwd_vacio_devuelve_false(self):
        assert window_focus.focus_project_window("") is False

    def test_barra_final_no_deja_el_nombre_vacio(self):
        # basename("c:/x/proyecto/") es "" si no se limpia la barra, y
        # entonces buscaria con un fragmento vacio.
        assert window_focus.focus_project_window(
            "C:/proyectos/carpeta-que-no-existe-jamas-9f3a/") is False
