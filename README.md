# Claude Status Widget

[![tests](https://github.com/FrankHP1984/claude-status-widget/actions/workflows/tests.yml/badge.svg)](https://github.com/FrankHP1984/claude-status-widget/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

Panel flotante para Windows que muestra, de un vistazo, en qué está cada una de tus
conversaciones de [Claude Code](https://claude.com/claude-code) abiertas.

Cuando trabajas con varias terminales a la vez es fácil perder de vista cuál sigue
trabajando, cuál ha terminado y cuál lleva un rato parada esperando que le concedas un
permiso. Este widget pone esa información en una esquina de la pantalla y, al hacer clic
en una fila, salta directamente a la pestaña de terminal correspondiente.

- **Semáforo por sesión** — iniciado, trabajando, esperando permiso, terminado.
- **Título real de la conversación** — el primer mensaje que escribiste, no uno intermedio.
- **Medidor de consumo** — cuánto llevas gastado del límite de uso que se repone cada 5 horas, y cuánto falta para que se reponga, junto a "Sesiones activas". Es el mismo número que da `/usage`.
- **Modelo y contexto por fila** — cada conversación muestra con qué modelo corre y cuánto ocupa su ventana de contexto.
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

### Los medidores (statusline)

Tanto el consumo del límite de uso como el contexto llegan por el **statusline** de
Claude Code, no por los hooks de eventos. Añade esta clave a `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python C:/ruta/al/claude-status-widget/hooks/notify_status.py"
  }
}
```

En cada turno, Claude Code entrega dos cosas que conviene no confundir:

- `rate_limits.five_hour` — **cuánto llevas gastado de tu cuota**, sumando todas tus
  conversaciones, y el momento en que se repone. Es un dato de cuenta, idéntico en todas
  las sesiones, así que se guarda una sola vez bajo la clave `_account`. **Esto es lo que
  sale en la cabecera.**
- `context_window.used_percentage` — **cuánto ocupa una conversación concreta** de su
  ventana de contexto. Se guarda en la sesión correspondiente y se muestra en su fila.

Confundir los dos es el error natural aquí: el primero es la bolsa que se agota y te
obliga a esperar; el segundo se reinicia al abrir un chat nuevo y no tiene nada que ver.

El mismo script imprime una línea de estado corta (`[Modelo] 42% usado`) en la terminal;
si quieres otra línea de estado, sustituye el script por uno que además escriba el
mismo JSON.

### Arrancar el widget

```bash
python widget/app.py
```

#### Acceso directo en el escritorio

Para tenerlo a mano sin abrir una terminal, genera el icono y crea el acceso directo:

```bash
python assets/make_icon.py          # genera assets/widget.ico
powershell -File tools/crear-acceso-directo.ps1
```

Apunta a `pythonw.exe` en vez de a `python.exe`, así que **no deja una ventana de consola
abierta** detrás del panel. Si lo lanzas dos veces no se duplica: el segundo proceso
detecta que ya hay un panel y se cierra solo.

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
    "shell_pid": 2222,
    "model": "Opus 5",
    "context_used_pct": 15,
    "context_remaining_pct": 85,
    "context_checked_at": "2026-01-01T11:59:00+00:00"
  },
  "_account": {
    "five_hour_pct": 22,
    "five_hour_resets_at": 1786291200,
    "seven_day_pct": 38,
    "seven_day_resets_at": 1786680000,
    "updated_at": "2026-01-01T12:00:00+00:00"
  }
}
```

`_account` no es una sesión: guarda el consumo de la **cuenta**, que es idéntico en todas
las conversaciones. Vive aparte precisamente por eso — cuando el dato se guardaba por
sesión, la cabecera acababa enseñando el número de una terminal cualquiera. No lleva
`interactive`, así que nunca se pinta como una fila. Las marcas `resets_at` vienen en
epoch (segundos UTC).

`model`, `context_used_pct` y `context_remaining_pct` los escribe el statusline y no los
tocan el resto de eventos. Se muestran en la fila de cada sesión.

La cabecera muestra `five_hour_pct`, coloreado: verde por debajo del 50 %, ámbar entre
50 y 80 % y rojo por encima del 80 %, con el tiempo que falta para reponerse al lado.

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
python -m pytest                              # paquete Python
node --test tests/opencode/plugin.test.mjs    # plugin de OpenCode
```

Los tests del plugin usan el runner integrado de Node (22 o superior), así que no
hacen falta dependencias de JavaScript.

La lógica con sustancia vive en el paquete `claude_status_widget/`, cubierta por tests.
Los archivos de `hooks/` y `widget/` son capas finas encima: el hook solo traduce eventos
a estados, y el widget solo pinta. Consulta [CONTRIBUTING.md](CONTRIBUTING.md) antes de
enviar cambios.

## Licencia

[MIT](LICENSE)
