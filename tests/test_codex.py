"""Tests de la lectura de sesiones de Codex.

Codex no tiene hooks, asi que todo sale de sus archivos de rollout. Lo
que hay que asegurar es que se distingue una conversacion de verdad de
un resumen interno, y que el estado de turno se lee del final del
archivo aunque este sea enorme.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_status_widget import codex  # noqa: E402


def escribir(directorio: Path, nombre: str, lineas: list) -> Path:
    ruta = directorio / f"rollout-{nombre}.jsonl"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text("\n".join(json.dumps(l) for l in lineas), encoding="utf-8")
    return ruta


def meta(cwd="c:/p/demo", origen="codex_vscode"):
    return {"type": "session_meta", "payload": {"cwd": cwd, "originator": origen}}


def evento(tipo, **kw):
    return {"type": "event_msg", "payload": dict(type=tipo, **kw)}


class TestEstado:
    def test_turno_abierto_es_trabajando(self, tmp_path):
        r = escribir(tmp_path, "a", [meta(), evento("task_started")])
        assert codex.leer_estado(r) == "trabajando"

    def test_turno_cerrado_es_terminado(self, tmp_path):
        r = escribir(tmp_path, "a", [meta(), evento("task_started"), evento("task_complete")])
        assert codex.leer_estado(r) == "terminado"

    def test_manda_el_ultimo_evento_de_turno(self, tmp_path):
        r = escribir(tmp_path, "a", [
            meta(), evento("task_started"), evento("task_complete"), evento("task_started"),
        ])
        assert codex.leer_estado(r) == "trabajando"

    def test_el_ruido_intermedio_no_confunde(self, tmp_path):
        r = escribir(tmp_path, "a", [
            meta(), evento("task_started"),
            *[evento("token_count") for _ in range(50)],
        ])
        assert codex.leer_estado(r) == "trabajando"


class TestCabecera:
    def test_saca_cwd_origen_y_titulo(self, tmp_path):
        r = escribir(tmp_path, "a", [
            meta(cwd="c:/p/demo"),
            evento("user_message", message="arregla el parser de fechas"),
        ])
        c = codex.leer_cabecera(r)
        assert c["cwd"] == "c:/p/demo"
        assert c["origen"] == "codex_vscode"
        assert c["title"] == "arregla el parser de fechas"

    def test_descarta_las_plantillas_del_sistema(self, tmp_path):
        # Las instrucciones inyectadas empiezan por '<'; el titulo debe
        # ser el primer mensaje humano de verdad.
        r = escribir(tmp_path, "a", [
            meta(),
            evento("user_message", message="<skills_instructions>haz esto</skills_instructions>"),
            evento("user_message", message="hola que tal"),
        ])
        assert codex.leer_cabecera(r)["title"] == "hola que tal"

    def test_sin_mensaje_humano_no_hay_titulo(self, tmp_path):
        r = escribir(tmp_path, "a", [meta(), evento("task_started")])
        assert codex.leer_cabecera(r)["title"] == ""


class TestLeerSesiones:
    def test_una_conversacion_real_se_muestra(self, tmp_path):
        escribir(tmp_path, "2026-08-10T10-00-00-abc", [
            meta(), evento("user_message", message="arregla el parser"), evento("task_started"),
        ])
        s = codex.leer_sesiones(base=tmp_path)
        assert len(s) == 1
        entrada = list(s.values())[0]
        assert entrada["state"] == "trabajando"
        assert entrada["interactive"] is True
        assert entrada["source"] == "codex"
        assert "VS Code" in entrada["detail"]

    def test_la_sesion_interna_no_se_pinta(self, tmp_path):
        # Sin mensaje escrito a mano: resumen o sesion de sistema.
        escribir(tmp_path, "2026-08-10T10-00-00-abc", [meta(), evento("task_started")])
        entrada = list(codex.leer_sesiones(base=tmp_path).values())[0]
        assert entrada["interactive"] is False

    def test_distingue_terminal_de_vs_code(self, tmp_path):
        escribir(tmp_path, "2026-08-10T10-00-00-abc", [
            meta(origen="codex_cli"), evento("user_message", message="hola"),
        ])
        entrada = list(codex.leer_sesiones(base=tmp_path).values())[0]
        assert "terminal" in entrada["detail"]

    def test_las_claves_van_prefijadas(self, tmp_path):
        escribir(tmp_path, "2026-08-10T10-00-00-abc", [
            meta(), evento("user_message", message="hola"),
        ])
        assert all(k.startswith("codex:") for k in codex.leer_sesiones(base=tmp_path))

    def test_las_viejas_se_descartan(self, tmp_path):
        r = escribir(tmp_path, "2026-01-01T10-00-00-abc", [
            meta(), evento("user_message", message="hola"),
        ])
        viejo = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
        os.utime(r, (viejo, viejo))
        assert codex.leer_sesiones(base=tmp_path) == {}

    def test_sin_directorio_no_revienta(self, tmp_path):
        assert codex.leer_sesiones(base=tmp_path / "no-existe") == {}
