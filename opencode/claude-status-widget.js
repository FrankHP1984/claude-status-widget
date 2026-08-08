/**
 * Plugin de OpenCode que publica el estado de sus sesiones en el mismo
 * status.json que usa el widget de Claude Code.
 *
 * Escribe en %LOCALAPPDATA%\claude-status-widget\status.json respetando el
 * mismo lock de archivo que state_store.py (creacion exclusiva de
 * status.lock), con los ids prefijados por "opencode:" para no chocar con
 * las sesiones de Claude Code.
 *
 * Instalacion: copiar este archivo a
 *   %USERPROFILE%\.config\opencode\plugin\claude-status-widget.js
 * (o referenciarlo desde el array "plugin" de opencode.json).
 *
 * Todo esta envuelto en try/catch: un fallo escribiendo el estado nunca
 * debe romper OpenCode.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFile } from "node:child_process";

// ---------------------------------------------------------------- rutas

const STATE_DIR = path.join(
  process.env.LOCALAPPDATA || os.tmpdir(),
  "claude-status-widget",
);
const STATE_FILE = path.join(STATE_DIR, "status.json");
const LOCK_FILE = path.join(STATE_DIR, "status.lock");

const REPO_DIR =
  process.env.CLAUDE_STATUS_WIDGET_DIR || "C:\\proyectos\\claude-status-widget";
const PYTHON = process.env.CLAUDE_STATUS_WIDGET_PYTHON || "python";

const ID_PREFIX = "opencode:";
const SOURCE = "opencode";

// Mismos limites que state_store.py
const MAX_ENTRIES = 50;
const LOCK_TIMEOUT_MS = 5000;
const LOCK_POLL_MS = 50;

const TITLE_MAX_CHARS = 42;
const DETAIL_MAX_CHARS = 80;

// ------------------------------------------------------------ utilidades

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function nowIso() {
  return new Date().toISOString().replace("Z", "+00:00");
}

function truncate(text, max) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  if (value.length <= max) return value;
  return value.slice(0, max) + "…";
}

function ensureDir() {
  fs.mkdirSync(STATE_DIR, { recursive: true });
}

function loadState() {
  try {
    return JSON.parse(fs.readFileSync(STATE_FILE, "utf8")) || {};
  } catch {
    return {};
  }
}

function saveState(data) {
  const tmp = path.join(STATE_DIR, `.oc-${process.pid}-${Date.now()}.tmp`);
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2), "utf8");
  try {
    fs.renameSync(tmp, STATE_FILE); // atomico y sobreescribe en Windows
  } catch (err) {
    try {
      fs.unlinkSync(tmp);
    } catch {}
    throw err;
  }
}

/** Mismo protocolo que state_store._FileLock: creacion exclusiva + rotura
 *  del lock huerfano cuando se agota el tiempo. */
async function withLock(fn) {
  ensureDir();
  const deadline = Date.now() + LOCK_TIMEOUT_MS;
  let fd = null;
  while (fd === null) {
    try {
      fd = fs.openSync(LOCK_FILE, "wx");
    } catch (err) {
      if (err.code !== "EEXIST") throw err;
      if (Date.now() > deadline) {
        try {
          fs.unlinkSync(LOCK_FILE);
        } catch {}
        continue;
      }
      await sleep(LOCK_POLL_MS);
    }
  }
  try {
    return fn();
  } finally {
    try {
      fs.closeSync(fd);
    } catch {}
    try {
      fs.unlinkSync(LOCK_FILE);
    } catch {}
  }
}

function prune(data, keep) {
  const keys = Object.keys(data);
  if (keys.length <= MAX_ENTRIES) return data;
  keys.sort((a, b) =>
    String(data[b].updated_at || "").localeCompare(String(data[a].updated_at || "")),
  );
  const kept = {};
  for (const key of keys.slice(0, MAX_ENTRIES)) kept[key] = data[key];
  if (keep && data[keep]) kept[keep] = data[keep];
  return kept;
}

// Las escrituras se serializan en el proceso para que dos eventos casi
// simultaneos no compitan por el lock innecesariamente.
let queue = Promise.resolve();

function enqueue(task) {
  queue = queue.then(task).catch(() => {});
  return queue;
}

function updateSession(sessionId, fields, onlyIfAbsent) {
  return enqueue(() =>
    withLock(() => {
      const data = loadState();
      const entry = data[sessionId] || {};
      for (const [key, value] of Object.entries(onlyIfAbsent || {})) {
        if (!entry[key]) entry[key] = value;
      }
      Object.assign(entry, fields || {});
      data[sessionId] = entry;
      saveState(prune(data, sessionId));
    }),
  );
}

function removeSession(sessionId) {
  return enqueue(() =>
    withLock(() => {
      const data = loadState();
      if (!(sessionId in data)) return;
      delete data[sessionId];
      saveState(data);
    }),
  );
}

// ------------------------------------------------------- pids de ventana

/** Pregunta a Python (psutil + win32) que ventana/pestaña corresponde a
 *  este proceso de OpenCode, para que el clic en el widget la enfoque. */
function resolvePids() {
  return new Promise((resolve) => {
    if (process.platform !== "win32") return resolve({});
    const script = path.join(REPO_DIR, "hooks", "resolve_pids.py");
    if (!fs.existsSync(script)) return resolve({});
    execFile(
      PYTHON,
      [script, String(process.pid)],
      { timeout: 10000, windowsHide: true },
      (err, stdout) => {
        if (err) return resolve({});
        try {
          resolve(JSON.parse(stdout) || {});
        } catch {
          resolve({});
        }
      },
    );
  });
}

// --------------------------------------------------------------- plugin

export const ClaudeStatusWidget = async ({ client }) => {
  // sessionID -> {root: bool, title, directory}
  const known = new Map();
  let pids = {};
  const pidsReady = resolvePids()
    .then((value) => {
      pids = value;
    })
    .catch(() => {});

  async function describe(sessionId) {
    if (known.has(sessionId)) return known.get(sessionId);
    let info = null;
    try {
      const res = await client.session.get({ path: { id: sessionId } });
      info = res?.data ?? res ?? null;
    } catch {
      info = null;
    }
    const meta = {
      // Sin informacion asumimos raiz: mejor mostrar de mas que perder
      // una sesion real del usuario.
      root: !info || !info.parentID,
      title: info?.title || "",
      directory: info?.directory || "",
    };
    known.set(sessionId, meta);
    return meta;
  }

  function remember(info) {
    if (!info?.id) return null;
    const meta = {
      root: !info.parentID,
      title: info.title || "",
      directory: info.directory || "",
    };
    known.set(info.id, meta);
    return meta;
  }

  /** Publica un cambio de estado de una sesion raiz de OpenCode. */
  async function publish(sessionId, state, detail, extra) {
    if (!sessionId) return;
    const meta = await describe(sessionId);
    // Los subagentes (sesiones hijas) no son terminales del usuario.
    if (!meta.root) return;

    await pidsReady;

    const now = nowIso();
    const fields = {
      state,
      detail: truncate(detail, DETAIL_MAX_CHARS),
      updated_at: now,
      interactive: true,
      source: SOURCE,
      ...(extra || {}),
    };
    if (meta.directory) fields.cwd = meta.directory;
    if (meta.title) fields.title = truncate(meta.title, TITLE_MAX_CHARS);

    const onlyIfAbsent = { started_at: now };
    if (pids.focus_pid) onlyIfAbsent.focus_pid = pids.focus_pid;
    if (pids.shell_pid) onlyIfAbsent.shell_pid = pids.shell_pid;

    try {
      await updateSession(ID_PREFIX + sessionId, fields, onlyIfAbsent);
    } catch {
      // Nunca propagamos: el widget es accesorio.
    }
  }

  return {
    async event({ event }) {
      try {
        const type = event?.type;
        const props = event?.properties || {};

        switch (type) {
          case "session.created": {
            const meta = remember(props.info);
            if (meta?.root) {
              await publish(props.info.id, "iniciado", "Sesion iniciada");
            }
            break;
          }
          case "session.updated": {
            // Solo refresca titulo/cwd; el estado lo llevan otros eventos.
            const info = props.info;
            if (!info?.id) break;
            const previous = known.get(info.id);
            const meta = remember(info);
            if (!meta?.root) break;
            if (previous && previous.title === meta.title) break;
            if (!meta.title) break;
            try {
              await updateSession(ID_PREFIX + info.id, {
                title: truncate(meta.title, TITLE_MAX_CHARS),
                source: SOURCE,
              });
            } catch {}
            break;
          }
          case "session.idle":
            await publish(props.sessionID, "terminado", "Tarea completada");
            break;
          case "session.error": {
            const message =
              props.error?.data?.message || props.error?.name || "Error";
            await publish(props.sessionID, "error", message);
            break;
          }
          case "session.deleted": {
            const id = props.info?.id;
            if (id) {
              known.delete(id);
              try {
                await removeSession(ID_PREFIX + id);
              } catch {}
            }
            break;
          }
          // "permission.asked" es el nombre en el SDK v2; "permission.updated"
          // el del v1. Se aceptan los dos para no depender de la version.
          case "permission.asked":
          case "permission.updated":
            await publish(
              props.sessionID,
              "esperando",
              props.title || props.type || "Esperando confirmacion",
            );
            break;
          case "permission.replied":
            await publish(props.sessionID, "trabajando", "Permiso resuelto");
            break;
          default:
            break;
        }
      } catch {
        // Silencio deliberado: el plugin nunca debe romper OpenCode.
      }
    },

    async "chat.message"(input, output) {
      try {
        const parts = output?.parts || [];
        const text = parts
          .filter((part) => part?.type === "text" && part.text)
          .map((part) => part.text)
          .join(" ");
        await publish(input?.sessionID, "trabajando", text || "Procesando");
      } catch {}
    },

    async "tool.execute.before"(input) {
      try {
        await publish(
          input?.sessionID,
          "trabajando",
          `Usando ${input?.tool || "?"}`,
        );
      } catch {}
    },

    async dispose() {
      // Al cerrar OpenCode las sesiones dejan de estar activas.
      try {
        for (const [sessionId, meta] of known.entries()) {
          if (!meta.root) continue;
          await updateSession(ID_PREFIX + sessionId, {
            state: "terminado",
            detail: "OpenCode cerrado",
            updated_at: nowIso(),
            source: SOURCE,
          });
        }
      } catch {}
    },
  };
};

export default ClaudeStatusWidget;
