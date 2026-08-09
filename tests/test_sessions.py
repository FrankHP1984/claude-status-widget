"""Tests de la maquina de estados visible.

Cubre las tres regresiones que costaron mas depuracion:
 - sesiones internas y muertas colandose en el panel
 - "esperando" colgado para siempre al cancelar con Escape
 - ambar y sonido en falsos positivos de prediccion
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from claude_status_widget import sessions

AHORA = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
VIVO = lambda pid: True
MUERTO = lambda pid: False


def entrada(**kw):
    base = {
        "state": "trabajando", "detail": "Usando Bash", "title": "Sesion",
        "cwd": "C:/proyectos/app", "interactive": True,
        "started_at": (AHORA - timedelta(minutes=5)).isoformat(),
        "updated_at": AHORA.isoformat(),
    }
    base.update(kw)
    return base


class TestElapsedLabel:
    @pytest.mark.parametrize("segundos,esperado", [
        (5, "5s"), (59, "59s"), (60, "1m"), (3540, "59m"), (3600, "1h0m"), (9000, "2h30m"),
    ])
    def test_formatos(self, segundos, esperado):
        inicio = (AHORA - timedelta(seconds=segundos)).isoformat()
        assert sessions.elapsed_label(inicio, AHORA) == esperado

    def test_fecha_invalida(self):
        assert sessions.elapsed_label("no es fecha", AHORA) == ""
        assert sessions.elapsed_label("", AHORA) == ""


class TestSessionLabel:
    def test_el_nombre_manual_manda(self):
        e = entrada(custom_title="Mi nombre", title="Automatico")
        assert sessions.session_label("abc", e) == "Mi nombre"

    def test_luego_el_titulo_automatico(self):
        assert sessions.session_label("abc", entrada(title="Automatico")) == "Automatico"

    def test_luego_la_carpeta(self):
        e = entrada(title="", cwd="C:/proyectos/mi-app")
        assert sessions.session_label("abc", e) == "mi-app"

    def test_por_ultimo_el_id(self):
        e = entrada(title="", cwd="")
        assert sessions.session_label("abcdefgh12345", e) == "abcdefgh"

    def test_nombre_manual_vacio_no_gana(self):
        e = entrada(custom_title="   ", title="Automatico")
        assert sessions.session_label("abc", e) == "Automatico"


class TestFiltrado:
    def test_descarta_sesiones_internas(self):
        data = {"interna": entrada(interactive=False), "real": entrada()}
        visibles = sessions.visible_sessions(data, VIVO, AHORA)
        assert [s for s, _ in visibles] == ["real"]

    def test_descarta_terminal_cerrada(self):
        data = {"muerta": entrada(shell_pid=999)}
        assert sessions.visible_sessions(data, MUERTO, AHORA) == []

    def test_sin_pid_no_se_descarta(self):
        """OpenCode puede no aportar pid; no debe desaparecer por ello."""
        data = {"oc": entrada(source="opencode")}
        assert len(sessions.visible_sessions(data, MUERTO, AHORA)) == 1

    def test_descarta_obsoletas(self):
        vieja = entrada(updated_at=(AHORA - timedelta(hours=7)).isoformat())
        assert sessions.visible_sessions({"v": vieja}, VIVO, AHORA) == []

    def test_ordena_por_actividad_reciente(self):
        data = {
            "antigua": entrada(updated_at=(AHORA - timedelta(minutes=10)).isoformat()),
            "reciente": entrada(updated_at=AHORA.isoformat()),
        }
        assert [s for s, _ in sessions.visible_sessions(data, VIVO, AHORA)] == ["reciente", "antigua"]


class TestReconcilePending:
    """REGRESION: al cancelar con Escape no llega ningun hook."""

    def _sesion(self, tmp_path, registros, transcript_mas_nuevo):
        p = tmp_path / "s.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in registros), encoding="utf-8")
        updated = AHORA
        stamp = (updated + timedelta(seconds=30)) if transcript_mas_nuevo else (updated - timedelta(seconds=30))
        os.utime(p, (stamp.timestamp(), stamp.timestamp()))
        return entrada(state="esperando", detail="Permiso para Bash",
                       updated_at=updated.isoformat(), transcript_path=str(p))

    def test_espera_real_se_mantiene(self, tmp_path):
        e = self._sesion(tmp_path, [{"type": "user"}], transcript_mas_nuevo=False)
        assert sessions.reconcile_pending("s", e)["state"] == "esperando"

    def test_cancelado_con_escape(self, tmp_path):
        marca = {"timestamp": (AHORA + timedelta(seconds=10)).isoformat().replace("+00:00", "Z"),
                 "message": {"content": "[Request interrupted by user]"}}
        e = self._sesion(tmp_path, [marca], transcript_mas_nuevo=True)
        r = sessions.reconcile_pending("s", e)
        assert r["state"] == "terminado"
        assert "Cancelado" in r["detail"]

    def test_permiso_concedido(self, tmp_path):
        e = self._sesion(tmp_path, [{"type": "assistant"}], transcript_mas_nuevo=True)
        assert sessions.reconcile_pending("s", e)["state"] == "trabajando"

    def test_interrupcion_antigua_no_marca_cancelado(self, tmp_path):
        """REGRESION: 71 interrupciones acumuladas daban falsos cancelados."""
        marca = {"timestamp": (AHORA - timedelta(hours=3)).isoformat().replace("+00:00", "Z"),
                 "message": {"content": "[Request interrupted by user]"}}
        e = self._sesion(tmp_path, [marca], transcript_mas_nuevo=True)
        assert sessions.reconcile_pending("s", e)["state"] == "trabajando"

    def test_no_toca_estados_que_no_esperan(self):
        e = entrada(state="trabajando")
        assert sessions.reconcile_pending("s", e) is e

    def test_sin_transcript_no_inventa(self):
        e = entrada(state="esperando", cwd="", transcript_path="")
        assert sessions.reconcile_pending("s", e)["state"] == "esperando"


class TestConfirmPending:
    """REGRESION: se mostraba ambar y sonaba en falsos positivos."""

    ESPERA = [("s1", {"state": "esperando", "detail": "Permiso para Bash"})]

    def test_espera_fugaz_no_se_muestra(self):
        pendientes = {}
        r = sessions.confirm_pending(self.ESPERA, pendientes, 100.0)
        assert r[0][1]["state"] == "trabajando"

    def test_sigue_oculta_antes_del_umbral(self):
        pendientes = {}
        sessions.confirm_pending(self.ESPERA, pendientes, 100.0)
        r = sessions.confirm_pending(self.ESPERA, pendientes, 100.0 + sessions.PENDING_CONFIRM_SECONDS - 0.1)
        assert r[0][1]["state"] == "trabajando"

    def test_se_muestra_al_superar_el_umbral(self):
        pendientes = {}
        sessions.confirm_pending(self.ESPERA, pendientes, 100.0)
        r = sessions.confirm_pending(self.ESPERA, pendientes, 100.0 + sessions.PENDING_CONFIRM_SECONDS)
        assert r[0][1]["state"] == "esperando"

    def test_no_muta_la_entrada_original(self):
        original = {"state": "esperando", "detail": "x"}
        sessions.confirm_pending([("s1", original)], {}, 100.0)
        assert original["state"] == "esperando"

    def test_se_olvida_al_dejar_de_esperar(self):
        pendientes = {}
        sessions.confirm_pending(self.ESPERA, pendientes, 100.0)
        assert "s1" in pendientes
        sessions.confirm_pending([("s1", {"state": "trabajando"})], pendientes, 101.0)
        assert pendientes == {}

    def test_limpia_sesiones_desaparecidas(self):
        pendientes = {}
        sessions.confirm_pending(self.ESPERA, pendientes, 100.0)
        sessions.confirm_pending([], pendientes, 101.0)
        assert pendientes == {}

    def test_reinicia_el_contador_tras_una_pausa(self):
        pendientes = {}
        sessions.confirm_pending(self.ESPERA, pendientes, 100.0)
        sessions.confirm_pending([("s1", {"state": "trabajando"})], pendientes, 101.0)
        r = sessions.confirm_pending(self.ESPERA, pendientes, 102.0)
        assert r[0][1]["state"] == "trabajando"


class TestCurrentContextPct:
    """El medidor de contexto (statusline) de la sesion mas reciente."""

    def test_devuelve_el_de_la_sesion_mas_reciente(self):
        data = {
            "antigua": entrada(
                updated_at=(AHORA - timedelta(minutes=10)).isoformat(),
                context_used_pct=10,
            ),
            "reciente": entrada(updated_at=AHORA.isoformat(), context_used_pct=85),
        }
        assert sessions.current_context_pct(data, VIVO, AHORA) == 85

    def test_sin_medidor_devuelve_none(self):
        assert sessions.current_context_pct({"s": entrada()}, VIVO, AHORA) is None

    def test_ignora_sesiones_no_visibles(self):
        data = {
            "interna": entrada(interactive=False, context_used_pct=50),
            "muerta": entrada(shell_pid=999, context_used_pct=50),
        }
        assert sessions.current_context_pct(data, MUERTO, AHORA) is None

    def test_prioriza_la_con_medidor_aunque_haya_otra_mas_nueva(self):
        data = {
            "nueva_sin": entrada(updated_at=AHORA.isoformat()),
            "con": entrada(
                updated_at=(AHORA - timedelta(seconds=30)).isoformat(),
                context_used_pct=40,
            ),
        }
        assert sessions.current_context_pct(data, VIVO, AHORA) == 40

    def test_ignora_porcentajes_invalidos(self):
        data = {
            "rara": entrada(context_used_pct="no es numero"),
            "buena": entrada(context_used_pct=72),
        }
        assert sessions.current_context_pct(data, VIVO, AHORA) == 72

    def test_acota_fuera_de_rango(self):
        data = {"rara": entrada(context_used_pct=150)}
        assert sessions.current_context_pct(data, VIVO, AHORA) == 100
        data = {"rara": entrada(context_used_pct=-5)}
        assert sessions.current_context_pct(data, VIVO, AHORA) == 0


class TestSessionMeta:
    """Modelo y contexto de la fila, cada uno opcional por separado."""

    def test_modelo_y_contexto(self):
        e = entrada(model="Opus 5", context_used_pct=7)
        assert sessions.session_meta(e) == "Opus 5 · 7%"

    def test_solo_modelo(self):
        assert sessions.session_meta(entrada(model="Haiku 4.5")) == "Haiku 4.5"

    def test_solo_contexto(self):
        assert sessions.session_meta(entrada(context_used_pct=42)) == "42%"

    def test_sin_ninguno_de_los_dos(self):
        # Fila de OpenCode: nunca reporta ni modelo ni medidor.
        assert sessions.session_meta(entrada(source="opencode")) == ""

    def test_cero_por_ciento_se_muestra(self):
        # 0 es falsy: si se filtrara por verdad, desapareceria.
        assert sessions.session_meta(entrada(context_used_pct=0)) == "0%"

    def test_modelo_vacio_o_solo_espacios_se_ignora(self):
        assert sessions.session_meta(entrada(model="   ", context_used_pct=5)) == "5%"

    def test_contexto_invalido_se_ignora(self):
        assert sessions.session_meta(entrada(model="Opus 5", context_used_pct="ns")) == "Opus 5"

    def test_acota_fuera_de_rango(self):
        assert sessions.session_meta(entrada(context_used_pct=150)) == "100%"


class TestUsagePct:
    """El limite de uso: la bolsa de 5 horas, NO el contexto del chat.

    Es un dato de cuenta y vive en `_account`, no en cada sesion: eso es
    justo lo que evita el baile de numeros que tenia la cabecera.
    """

    def cuenta(self, **kw):
        base = {"five_hour_pct": 22, "seven_day_pct": 38}
        base.update(kw)
        return {"_account": base, "s1": entrada(context_used_pct=7)}

    def test_lee_la_ventana_de_cinco_horas(self):
        assert sessions.usage_pct(self.cuenta()) == 22

    def test_no_lo_confunde_con_el_contexto_de_la_sesion(self):
        # La sesion visible va por 7%: la cabecera debe ignorarlo.
        assert sessions.usage_pct(self.cuenta()) != 7

    def test_semanal(self):
        assert sessions.weekly_pct(self.cuenta()) == 38

    def test_sin_datos_de_cuenta(self):
        assert sessions.usage_pct({"s1": entrada()}) is None

    def test_cero_por_ciento(self):
        assert sessions.usage_pct(self.cuenta(five_hour_pct=0)) == 0

    def test_acota_y_descarta_basura(self):
        assert sessions.usage_pct(self.cuenta(five_hour_pct=150)) == 100
        assert sessions.usage_pct(self.cuenta(five_hour_pct="ns")) is None

    def test_la_cuenta_no_se_pinta_como_fila(self):
        # `_account` no es interactive, asi que nunca aparece en el panel.
        visibles = sessions.visible_sessions(self.cuenta(), VIVO, AHORA)
        assert [sid for sid, _ in visibles] == ["s1"]


class TestResetsLabel:
    def etiqueta(self, segundos):
        epoch = int((AHORA + timedelta(seconds=segundos)).timestamp())
        data = {"_account": {"five_hour_resets_at": epoch}}
        return sessions.resets_label(data, AHORA)

    @pytest.mark.parametrize("segundos,esperado", [
        (600, "10m"), (3600, "1h00m"), (11520, "3h12m"),
    ])
    def test_formatos(self, segundos, esperado):
        assert self.etiqueta(segundos) == esperado

    def test_ya_pasado_no_se_muestra(self):
        assert self.etiqueta(-60) == ""

    def test_sin_dato(self):
        assert sessions.resets_label({}, AHORA) == ""

    def test_basura(self):
        assert sessions.resets_label({"_account": {"five_hour_resets_at": "ns"}}, AHORA) == ""


class TestContextPct:
    def test_devuelve_none_sin_medidor(self):
        assert sessions.context_pct(entrada()) is None

    def test_lee_solo_la_suya_sin_resolver_sesion_actual(self):
        assert sessions.context_pct(entrada(context_used_pct=33)) == 33
