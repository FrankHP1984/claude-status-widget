# Claude Status Widget

Panel flotante para Windows que muestra, de un vistazo, en qué está cada una de tus
conversaciones de [Claude Code](https://claude.com/claude-code) abiertas.

Cuando trabajas con varias terminales a la vez es fácil perder de vista cuál sigue
trabajando, cuál ha terminado y cuál lleva un rato parada esperando que le concedas un
permiso. Este widget pone esa información en una esquina de la pantalla y, al hacer clic
en una fila, salta directamente a la pestaña de terminal correspondiente.

- **Semáforo por sesión** — iniciado, trabajando, esperando permiso, terminado.
- **Título real de la conversación** — el primer mensaje que escribiste, no uno intermedio.
- **Clic para saltar** — enfoca la ventana y cambia a la pestaña de Windows Terminal.
- **Aviso sonoro opcional** — al terminar una tarea o al quedarse esperando permiso.
- **Solo conversaciones reales** — las sesiones internas (subagentes, resúmenes) se descartan.
- **Soporte para OpenCode** — mediante un plugin que escribe en el mismo estado.

## Requisitos

- Windows 10/11
- Python 3.10 o superior
- [Windows Terminal](https://aka.ms/terminal) (necesario solo para el salto entre pestañas)
- Claude Code con los hooks habilitados

## Instalación

```bash
git clone https://github.com/FrankHP1984/claude-status-widget.git
cd claude-status-widget
pip install -r requirements.txt
```

### Registrar los hooks

El widget no sondea nada: se alimenta de los hooks de Claude Code. Añade a
`%USERPROFILE%\.claude\settings.json` una entrada por cada evento, apuntando a
`hooks/notify_status.py` con la ruta donde hayas clonado el repositorio:

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [{ "type": "command", "command": "python C:/ruta/al/claude-status-widget/hooks/notify_status.py" }] }
    ],
    "UserPromptSubmit": [
      { "hooks": [{ "type": "command", "command": "python C:/ruta/al/claude-status-widget/hooks/notify_status.py" }] }
    ],
    "PreToolUse": [
      { "hooks": [{ "type": "command", "command": "python C:/ruta/al/claude-status-widget/hooks/notify_status.py" }] }
    ],
    "PostToolUse": [
      { "hooks": [{ "type": "command", "command": "python C:/ruta/al/claude-status-widget/hooks/notify_status.py" }] }
    ],
    "Notification": [
      { "hooks": [{ "type": "command", "command": "python C:/ruta/al/claude-status-widget/hooks/notify_status.py" }] }
    ],
    "Stop": [
      { "hooks": [{ "type": "command", "command": "python C:/ruta/al/claude-status-widget/hooks/notify_status.py" }] }
    ]
  }
}
```

Si ya tienes hooks registrados para esos eventos, añade este como un elemento más del
array en lugar de sustituir el existente.

### Arrancar el widget

```bash
python widget/app.py
```

Aparece un icono en la bandeja del sistema y el panel en la esquina superior derecha.
El panel se arrastra a donde quieras; desde el icono de la bandeja puedes silenciar los
avisos, restablecer la posición o salir.

Las sesiones ya abiertas cuando instalaste los hooks no aparecerán hasta su siguiente
evento (basta con enviar un mensaje).

## Cómo funciona

Cada evento de Claude Code invoca `hooks/notify_status.py`, que traduce el evento a un
estado y lo escribe en `%LOCALAPPDATA%\claude-status-widget\status.json`. El widget relee
ese archivo cada 150 ms y solo repinta si el contenido ha cambiado.

Cada entrada del estado tiene esta forma:

```json
{
  "a1b2c3d4": {
    "state": "trabajando",
    "detail": "Usando Bash",
    "title": "arreglar el parser de fechas",
    "cwd": "C:/proyectos/ejemplo",
    "updated_at": "2026-01-01T12:00:00+00:00",
    "started_at": "2026-01-01T11:45:00+00:00",
    "source": "claude",
    "interactive": true,
    "focus_pid": 1111,
    "shell_pid": 2222
  }
}
```

Tres decisiones de diseño merecen explicación:

**El título sale del transcript, no del evento.** Claude Code guarda cada conversación en
un JSONL bajo `~/.claude/projects/`. El primer registro `user` con `isSidechain=false` y
`promptSource="typed"` es el mensaje que abrió la conversación. Ese mismo criterio sirve
para lo segundo: si una sesión no tiene ningún mensaje escrito a mano, es una sesión
interna y no se muestra.

**El permiso se predice, no se espera.** El evento `Notification` — la señal oficial de
"estoy pidiendo permiso" — llega con varios segundos de retraso, mientras que `PreToolUse`
llega al instante. `permissions.py` predice si Claude va a preguntar, de forma
deliberadamente conservadora: prefiere no avisar a avisar en falso. El widget confirma la
predicción esperando a que la pausa se sostenga un segundo antes de mostrarla y sonar.

**El salto de pestaña usa el árbol de procesos.** Varias pestañas de Windows Terminal
comparten una única ventana, así que enfocar la ventana no basta. El hook guarda dos PIDs:
el de la ventana (`focus_pid`) y el del shell de la pestaña (`shell_pid`). El índice de
pestaña se deduce ordenando los shells hijos por hora de creación, y se envía
`Ctrl+Alt+<n>`, el atajo nativo de Windows Terminal.

## Limitaciones conocidas

- **Solo Windows.** Depende de `pywin32` y de los atajos de Windows Terminal.
- **El índice de pestaña se infiere del orden de creación de los procesos.** Si reordenas
  pestañas arrastrándolas, o cierras una del medio, el salto puede ir a la pestaña
  equivocada. Si el índice no se puede determinar, o es mayor que 9, el widget se limita a
  enfocar la ventana.
- **La predicción de permisos no replica la lógica interna de Claude Code.** No cubre con
  exactitud todos los modos, los "permitir siempre" ni las herramientas MCP. Por eso es
  conservadora.

## OpenCode

`opencode/claude-status-widget.js` publica el estado de las sesiones de OpenCode en el
mismo `status.json`, respetando el mismo lock de archivo y prefijando los identificadores
con `opencode:`. Para instalarlo, copia el archivo a
`%USERPROFILE%\.config\opencode\plugin\claude-status-widget.js`.

## Desarrollo

```bash
pip install -r requirements.txt
python -m pytest
```

La lógica con sustancia vive en el paquete `claude_status_widget/`, cubierta por tests.
Los archivos de `hooks/` y `widget/` son capas finas encima: el hook solo traduce eventos
a estados, y el widget solo pinta. Consulta [CONTRIBUTING.md](CONTRIBUTING.md) antes de
enviar cambios.

## Licencia

[MIT](LICENSE)
