"""Tests de lectura de transcripts: la parte mas fragil del proyecto."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from claude_status_widget import transcript

BSLASH = chr(92)


def escribir(path, registros):
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in registros),
        encoding="utf-8",
    )
    return path


class TestEncodeProjectDir:
    def test_codifica_ruta_windows(self):
        cwd = f"C:{BSLASH}Users{BSLASH}usuario"
        assert transcript.encode_project_dir(cwd) == "C--Users-usuario"

    def test_codifica_barras_normales(self):
        assert transcript.encode_project_dir("C:/proyectos/app") == "C--proyectos-app"


class TestTranscriptPath:
    def test_prefiere_la_ruta_dada_si_existe(self, tmp_path):
        real = escribir(tmp_path / "s.jsonl", [])
        assert transcript.transcript_path("s", "C:/x", str(real)) == real

    def test_ignora_ruta_dada_inexistente(self, tmp_path):
        # Debe caer a la derivacion en vez de devolver una ruta rota
        assert transcript.transcript_path("s", "C:/x", str(tmp_path / "no.jsonl")) is None

    def test_sin_datos_devuelve_none(self):
        assert transcript.transcript_path("", "", "") is None


class TestBuildTitle:
    def test_texto_corto_intacto(self):
        assert transcript.build_title("Arreglar el widget") == "Arreglar el widget"

    def test_corta_por_primera_frase(self):
        assert transcript.build_title("Hola. Segunda frase larga") == "Hola."

    def test_no_parte_palabras(self):
        titulo = transcript.build_title("palabra " * 20)
        assert titulo.endswith("\u2026")
        assert not titulo.replace("\u2026", "").endswith("palab")

    def test_respeta_acentos(self):
        assert transcript.build_title("revisión de sesión") == "revisión de sesión"

    def test_vacio(self):
        assert transcript.build_title("") == ""
        assert transcript.build_title(None) == ""


class TestPrettifyAgentName:
    def test_convierte_kebab_case(self):
        assert transcript.prettify_agent_name("fix-status-widget-bugs") == "Fix status widget bugs"

    def test_vacio(self):
        assert transcript.prettify_agent_name("") == ""


class TestReadNames:
    def test_se_queda_con_la_ultima_aparicion(self, tmp_path):
        p = escribir(tmp_path / "s.jsonl", [
            {"type": "agent-name", "agentName": "nombre-viejo"},
            {"type": "ai-title", "aiTitle": "titulo viejo"},
            {"type": "agent-name", "agentName": "nombre-nuevo"},
        ])
        names = transcript.read_names(p)
        assert names["agent_name"] == "nombre-nuevo"
        assert names["ai_title"] == "titulo viejo"

    def test_archivo_inexistente_no_revienta(self, tmp_path):
        assert transcript.read_names(tmp_path / "no.jsonl") == {"agent_name": "", "ai_title": ""}


class TestReadFirstTypedPrompt:
    def test_devuelve_el_primero_escrito(self, tmp_path):
        p = escribir(tmp_path / "s.jsonl", [
            {"type": "user", "promptSource": "typed", "message": {"content": "primero"}},
            {"type": "user", "promptSource": "typed", "message": {"content": "segundo"}},
        ])
        assert transcript.read_first_typed_prompt(p) == "primero"

    def test_ignora_subagentes(self, tmp_path):
        p = escribir(tmp_path / "s.jsonl", [
            {"type": "user", "promptSource": "typed", "isSidechain": True,
             "message": {"content": "de subagente"}},
            {"type": "user", "promptSource": "typed", "message": {"content": "del usuario"}},
        ])
        assert transcript.read_first_typed_prompt(p) == "del usuario"

    def test_ignora_prompts_no_escritos(self, tmp_path):
        p = escribir(tmp_path / "s.jsonl", [
            {"type": "user", "promptSource": "hook", "message": {"content": "inyectado"}},
            {"type": "user", "promptSource": "typed", "message": {"content": "escrito"}},
        ])
        assert transcript.read_first_typed_prompt(p) == "escrito"

    def test_contenido_en_bloques(self, tmp_path):
        p = escribir(tmp_path / "s.jsonl", [
            {"type": "user", "promptSource": "typed",
             "message": {"content": [{"text": "hola"}, {"text": "mundo"}]}},
        ])
        assert transcript.read_first_typed_prompt(p) == "hola mundo"

    def test_sesion_interna_sin_prompts(self, tmp_path):
        p = escribir(tmp_path / "s.jsonl", [{"type": "assistant", "message": {}}])
        assert transcript.read_first_typed_prompt(p) == ""


class TestSessionTitle:
    def test_prioridad_agent_name(self, tmp_path):
        p = escribir(tmp_path / "s.jsonl", [
            {"type": "agent-name", "agentName": "fix-bugs"},
            {"type": "ai-title", "aiTitle": "un titulo largo"},
            {"type": "user", "promptSource": "typed", "message": {"content": "el prompt"}},
        ])
        assert transcript.session_title(p) == "Fix bugs"

    def test_cae_a_ai_title(self, tmp_path):
        p = escribir(tmp_path / "s.jsonl", [
            {"type": "ai-title", "aiTitle": "un titulo"},
            {"type": "user", "promptSource": "typed", "message": {"content": "el prompt"}},
        ])
        assert transcript.session_title(p) == "un titulo"

    def test_cae_al_primer_prompt(self, tmp_path):
        p = escribir(tmp_path / "s.jsonl", [
            {"type": "user", "promptSource": "typed", "message": {"content": "el prompt"}},
        ])
        assert transcript.session_title(p) == "el prompt"


class TestWasInterruptedAfter:
    """REGRESION: se marcaban sesiones como canceladas por interrupciones
    antiguas, porque solo se buscaba la marca sin comprobar la fecha."""

    def _con_interrupcion(self, tmp_path, cuando):
        return escribir(tmp_path / "s.jsonl", [
            {"timestamp": cuando.isoformat().replace("+00:00", "Z"),
             "message": {"content": "[Request interrupted by user]"}},
        ])

    def test_interrupcion_antigua_no_cuenta(self, tmp_path):
        corte = datetime.now(timezone.utc)
        p = self._con_interrupcion(tmp_path, corte - timedelta(hours=3))
        assert transcript.was_interrupted_after(p, corte) is False

    def test_interrupcion_posterior_si_cuenta(self, tmp_path):
        corte = datetime.now(timezone.utc)
        p = self._con_interrupcion(tmp_path, corte + timedelta(seconds=5))
        assert transcript.was_interrupted_after(p, corte) is True

    def test_muchas_antiguas_y_una_nueva(self, tmp_path):
        corte = datetime.now(timezone.utc)
        viejas = [{"timestamp": (corte - timedelta(hours=i)).isoformat().replace("+00:00", "Z"),
                   "message": {"content": "[Request interrupted by user]"}} for i in range(1, 40)]
        p = escribir(tmp_path / "s.jsonl", viejas)
        assert transcript.was_interrupted_after(p, corte) is False

    def test_sin_fecha_no_cuenta(self, tmp_path):
        p = escribir(tmp_path / "s.jsonl", [{"message": {"content": "[Request interrupted by user]"}}])
        assert transcript.was_interrupted_after(p, datetime.now(timezone.utc)) is False

    def test_sin_momento_de_referencia(self, tmp_path):
        p = self._con_interrupcion(tmp_path, datetime.now(timezone.utc))
        assert transcript.was_interrupted_after(p, None) is False
