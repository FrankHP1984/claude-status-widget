# Cómo contribuir

Gracias por el interés. Este es un proyecto pequeño y con un objetivo acotado, así que
antes de escribir código conviene abrir un issue describiendo el problema o la propuesta.

## Entorno

```bash
git clone https://github.com/FrankHP1984/claude-status-widget.git
cd claude-status-widget
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m pytest
```

Necesitas Windows para ejecutar el widget y los tests que tocan procesos o ventanas. La
lógica pura del paquete `claude_status_widget/` es independiente del sistema salvo
`window_focus.py`.

## Estructura

La regla que organiza el proyecto es sencilla: **la lógica con sustancia vive en
`claude_status_widget/` y está cubierta por tests**. Todo lo demás son capas finas.

| Ruta | Responsabilidad |
|---|---|
| `claude_status_widget/transcript.py` | Leer el JSONL de sesión: título real y detección de sesiones interactivas |
| `claude_status_widget/sessions.py` | Decidir qué sesiones se muestran: `interactive`, caducidad, PID vivo |
| `claude_status_widget/state_store.py` | Leer y escribir `status.json` con escritura atómica, lock y purga |
| `claude_status_widget/permissions.py` | Predecir si Claude va a pedir confirmación |
| `claude_status_widget/window_focus.py` | Enfocar ventana y saltar de pestaña |
| `hooks/notify_status.py` | Traducir eventos de Claude Code a estados. Nada más |
| `widget/app.py` | Pintar el panel y el icono de bandeja |
| `opencode/claude-status-widget.js` | Publicar el estado de OpenCode en el mismo archivo |

Si te encuentras añadiendo condicionales a `hooks/` o a `widget/app.py`, probablemente esa
lógica pertenece al paquete, donde puede probarse.

## Antes de enviar un cambio

1. **Tests en verde**: `python -m pytest`. La CI ejecuta lo mismo en cada push.
2. **Cobertura del cambio**: cualquier lógica nueva en `claude_status_widget/` llega con
   sus tests.
3. **Sin datos personales**: nada de rutas con tu nombre de usuario real, ni en el código
   ni en los tests. Usa `C:/Users/usuario` o `tmp_path`.
4. **Rendimiento del hook**: `notify_status.py` se ejecuta en *cada* acción de Claude Code.
   Los imports caros (`psutil`, `pywin32`) van dentro de la función que los necesita, no
   arriba del archivo.

## Estilo

- Código en inglés o español, pero **coherente con el archivo que tocas**.
- Los comentarios explican *por qué*, no *qué*. El código ya dice qué hace.
- Documenta las limitaciones asumidas en el propio módulo, como hace `permissions.py`.

## Reportar un fallo

Incluye la versión de Windows, la de Python, si usas Windows Terminal y con cuántas
pestañas, y el contenido de `%LOCALAPPDATA%\claude-status-widget\status.json` en el
momento del fallo — **quitando antes las rutas y títulos que no quieras compartir**, ya
que ese archivo contiene el primer mensaje de tus conversaciones.
