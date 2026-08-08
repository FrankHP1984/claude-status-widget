"""Tests del predictor de permisos.

REGRESION principal: el predictor ignoraba `defaultMode`, asi que
marcaba "esperando" en modo automatico, donde Claude Code no pregunta.
"""
import json

import pytest

from claude_status_widget import permissions

CWD = "C:/proyectos/app"


@pytest.fixture
def settings(tmp_path):
    def _crear(modo="default", ask=None, deny=None):
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"permissions": {
            "defaultMode": modo,
            "allow": ["Bash", "Read", "Write"],
            "ask": ask if ask is not None else ["Bash(docker*)", "Bash(git push*)"],
            "deny": deny if deny is not None else ["Bash(rm -rf *)", "Read(./.env)"],
        }}), encoding="utf-8")
        return p
    return _crear


class TestRuleMatches:
    def test_regla_sin_parentesis_compara_herramienta(self):
        assert permissions.rule_matches("Bash", "Bash", "lo que sea") is True
        assert permissions.rule_matches("Bash", "Read", "lo que sea") is False

    def test_comodin_final(self):
        assert permissions.rule_matches("Bash(git push*)", "Bash", "git push origin") is True
        assert permissions.rule_matches("Bash(git push*)", "Bash", "git pull") is False

    def test_coincidencia_exacta(self):
        assert permissions.rule_matches("Bash(git status)", "Bash", "git status") is True
        assert permissions.rule_matches("Bash(git status)", "Bash", "git status -s") is False

    def test_no_cruza_herramientas(self):
        assert permissions.rule_matches("Read(./.env)", "Bash", "./.env") is False


class TestModoAutomatico:
    def test_ask_no_pregunta_en_dontask(self, settings):
        s = settings(modo="dontAsk")
        assert permissions.needs_permission("Bash", {"command": "docker ps"}, CWD, s) is False

    def test_ask_si_pregunta_en_modo_normal(self, settings):
        s = settings(modo="default")
        assert permissions.needs_permission("Bash", {"command": "docker ps"}, CWD, s) is True

    @pytest.mark.parametrize("modo", ["dontAsk", "DONTASK", "bypassPermissions", "acceptEdits"])
    def test_todos_los_modos_no_interactivos(self, settings, modo):
        s = settings(modo=modo)
        assert permissions.needs_permission("Bash", {"command": "docker ps"}, CWD, s) is False

    def test_deny_bloquea_incluso_en_dontask(self, settings):
        s = settings(modo="dontAsk")
        assert permissions.needs_permission("Bash", {"command": "rm -rf /tmp/x"}, CWD, s) is True


class TestBashWritesOutside:
    def test_escritura_fuera_del_proyecto(self):
        cmd = "cp datos.txt C:/Users/franc/.claude/settings.json"
        assert permissions.bash_writes_outside(cmd, CWD) is True

    def test_escritura_dentro_del_proyecto(self):
        cmd = "cp datos.txt C:/proyectos/app/copia.txt"
        assert permissions.bash_writes_outside(cmd, CWD) is False

    def test_sin_orden_de_escritura(self):
        cmd = "cat C:/Users/franc/.claude/settings.json"
        assert permissions.bash_writes_outside(cmd, CWD) is False

    def test_redireccion_cuenta_como_escritura(self):
        cmd = "echo hola > C:/Users/franc/nota.txt"
        assert permissions.bash_writes_outside(cmd, CWD) is True

    def test_directorio_seguro_excluido(self):
        cmd = "cp a.txt C:/Temp/scratch/a.bak"
        assert permissions.bash_writes_outside(cmd, CWD, safe_dirs=("C:/Temp",)) is False

    def test_comando_vacio(self):
        assert permissions.bash_writes_outside("", CWD) is False
        assert permissions.bash_writes_outside("cp a b", "") is False


class TestNeedsPermission:
    def test_comando_inocuo(self, settings):
        s = settings(modo="dontAsk")
        assert permissions.needs_permission("Bash", {"command": "echo hola"}, CWD, s) is False

    def test_escritura_fuera_pregunta_aunque_sea_automatico(self, settings):
        s = settings(modo="dontAsk")
        cmd = "cp a.txt C:/Users/franc/.claude/settings.json"
        assert permissions.needs_permission("Bash", {"command": cmd}, CWD, s) is True

    def test_edit_fuera_del_proyecto_no_es_falso_positivo(self, settings):
        """REGRESION: marcaba ambar en CADA edicion cuando la sesion se
        abria en un directorio distinto al del proyecto."""
        s = settings(modo="dontAsk")
        entrada = {"file_path": "C:/otro/sitio/archivo.py"}
        assert permissions.needs_permission("Edit", entrada, CWD, s) is False

    def test_sin_settings_no_predice(self, tmp_path):
        assert permissions.needs_permission("Bash", {"command": "rm -rf x"}, CWD,
                                            tmp_path / "no-existe.json") is False

    def test_settings_corrupto_no_revienta(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text("{ esto no es json", encoding="utf-8")
        assert permissions.needs_permission("Bash", {"command": "rm -rf x"}, CWD, p) is False
