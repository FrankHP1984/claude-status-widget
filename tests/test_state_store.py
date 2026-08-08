"""Tests del almacen compartido: es el punto donde convergen todos los
procesos (hooks de Claude Code, plugin de OpenCode, widget), asi que la
atomicidad y el bloqueo importan mas que en ningun otro sitio."""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from claude_status_widget import state_store


@pytest.fixture(autouse=True)
def almacen_aislado(tmp_path, monkeypatch):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state_store, "STATE_FILE", tmp_path / "status.json")
    monkeypatch.setattr(state_store, "LOCK_FILE", tmp_path / "status.lock")
    return tmp_path


class TestCargaYGuardado:
    def test_vacio_al_principio(self):
        assert state_store.load() == {}

    def test_ida_y_vuelta(self):
        state_store.save({"s1": {"state": "trabajando"}})
        assert state_store.load()["s1"]["state"] == "trabajando"

    def test_conserva_acentos(self):
        state_store.save({"s1": {"title": "revisión de sesión"}})
        assert state_store.load()["s1"]["title"] == "revisión de sesión"

    def test_json_corrupto_no_revienta(self, almacen_aislado):
        (almacen_aislado / "status.json").write_text("{ roto", encoding="utf-8")
        assert state_store.load() == {}

    def test_no_deja_temporales(self, almacen_aislado):
        state_store.save({"s1": {}})
        assert list(almacen_aislado.glob("*.tmp")) == []


class TestUpdateSession:
    def test_crea_y_actualiza(self):
        state_store.update_session("s1", {"state": "trabajando"})
        state_store.update_session("s1", {"state": "terminado"})
        assert state_store.load()["s1"]["state"] == "terminado"

    def test_only_if_absent_no_pisa(self):
        state_store.update_session("s1", {}, {"title": "primero"})
        state_store.update_session("s1", {}, {"title": "segundo"})
        assert state_store.load()["s1"]["title"] == "primero"

    def test_only_if_absent_rellena_si_falta(self):
        state_store.update_session("s1", {"state": "x"})
        state_store.update_session("s1", {}, {"title": "puesto luego"})
        assert state_store.load()["s1"]["title"] == "puesto luego"

    def test_only_if_absent_rellena_si_vacio(self):
        state_store.update_session("s1", {"title": ""})
        state_store.update_session("s1", {}, {"title": "ahora si"})
        assert state_store.load()["s1"]["title"] == "ahora si"

    def test_no_afecta_a_otras_sesiones(self):
        state_store.update_session("s1", {"state": "a"})
        state_store.update_session("s2", {"state": "b"})
        assert state_store.load()["s1"]["state"] == "a"

    def test_libera_el_lock(self, almacen_aislado):
        state_store.update_session("s1", {"state": "x"})
        assert not (almacen_aislado / "status.lock").exists()


class TestPoda:
    def test_recorta_al_maximo(self):
        data = {f"s{i}": {"updated_at": f"2026-08-08T10:{i:02d}:00+00:00"} for i in range(60)}
        podado = state_store.prune(data, max_entries=10)
        assert len(podado) == 10

    def test_conserva_las_mas_recientes(self):
        data = {f"s{i}": {"updated_at": f"2026-08-08T10:{i:02d}:00+00:00"} for i in range(60)}
        podado = state_store.prune(data, max_entries=5)
        assert "s59" in podado and "s0" not in podado

    def test_siempre_conserva_la_sesion_indicada(self):
        data = {f"s{i}": {"updated_at": f"2026-08-08T10:{i:02d}:00+00:00"} for i in range(60)}
        podado = state_store.prune(data, keep="s0", max_entries=5)
        assert "s0" in podado

    def test_por_debajo_del_limite_no_toca_nada(self):
        data = {"s1": {"updated_at": "2026-08-08T10:00:00+00:00"}}
        assert state_store.prune(data, max_entries=10) == data


class TestConcurrencia:
    def test_escrituras_simultaneas_no_corrompen(self, almacen_aislado):
        """Varios procesos escribiendo a la vez: es el caso real cuando
        hay hooks de Claude Code y el plugin de OpenCode en paralelo."""
        raiz = Path(__file__).resolve().parent.parent
        guion = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, r"{raiz}")
            from pathlib import Path
            from claude_status_widget import state_store
            state_store.STATE_DIR = Path(r"{almacen_aislado}")
            state_store.STATE_FILE = state_store.STATE_DIR / "status.json"
            state_store.LOCK_FILE = state_store.STATE_DIR / "status.lock"
            etiqueta = sys.argv[1]
            for i in range(15):
                state_store.update_session(f"{{etiqueta}}-{{i}}", {{"state": "trabajando"}})
        """)
        archivo = almacen_aislado / "escritor.py"
        archivo.write_text(guion, encoding="utf-8")

        procesos = [subprocess.Popen([sys.executable, str(archivo), f"p{n}"]) for n in range(3)]
        for p in procesos:
            assert p.wait(timeout=60) == 0

        data = json.loads((almacen_aislado / "status.json").read_text(encoding="utf-8"))
        assert len(data) == 45, "se perdieron escrituras por condicion de carrera"
        assert not (almacen_aislado / "status.lock").exists()
        assert list(almacen_aislado.glob("*.tmp")) == []
