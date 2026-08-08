/**
 * Tests del plugin de OpenCode.
 *
 * El plugin no exporta sus funciones internas, asi que se prueba por su
 * comportamiento observable: se disparan eventos y se comprueba lo que
 * queda escrito en status.json, que es justamente el contrato que el
 * widget consume.
 *
 * El modulo resuelve STATE_DIR al importarse, de ahi que LOCALAPPDATA se
 * fije antes del import dinamico. CLAUDE_STATUS_WIDGET_DIR apunta a un
 * directorio inexistente para que resolvePids devuelva {} sin lanzar
 * Python.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { after, before, beforeEach, describe, it } from "node:test";

const ROOT = fs.mkdtempSync(path.join(os.tmpdir(), "csw-oc-"));
const STATE_FILE = path.join(ROOT, "claude-status-widget", "status.json");

process.env.LOCALAPPDATA = ROOT;
process.env.CLAUDE_STATUS_WIDGET_DIR = path.join(ROOT, "no-existe");

let ClaudeStatusWidget;

before(async () => {
  ({ ClaudeStatusWidget } = await import(
    "../../opencode/claude-status-widget.js"
  ));
});

after(() => {
  fs.rmSync(ROOT, { recursive: true, force: true });
});

beforeEach(() => {
  fs.rmSync(STATE_FILE, { force: true });
});

function readState() {
  try {
    return JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
  } catch {
    return {};
  }
}

function writeState(data) {
  fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true });
  fs.writeFileSync(STATE_FILE, JSON.stringify(data), "utf8");
}

/** Cliente falso: devuelve la sesion pedida, o lanza si no la conoce. */
function fakeClient(sessions = {}) {
  return {
    session: {
      get: async ({ path: { id } }) => {
        if (!(id in sessions)) throw new Error("desconocida");
        return { data: sessions[id] };
      },
    },
  };
}

const rootSession = (id, extra = {}) => ({
  id,
  title: "una conversacion",
  directory: "C:/proyectos/ejemplo",
  ...extra,
});

async function makePlugin(sessions = {}) {
  return ClaudeStatusWidget({ client: fakeClient(sessions) });
}

const created = (info) => ({
  event: { type: "session.created", properties: { info } },
});

describe("session.created", () => {
  it("publica la sesion raiz con prefijo, fuente y marca interactiva", async () => {
    const plugin = await makePlugin();
    await plugin.event(created(rootSession("s1")));

    const entry = readState()["opencode:s1"];
    assert.ok(entry, "deberia existir la entrada prefijada");
    assert.equal(entry.state, "iniciado");
    assert.equal(entry.detail, "Sesion iniciada");
    assert.equal(entry.source, "opencode");
    assert.equal(entry.interactive, true);
    assert.equal(entry.cwd, "C:/proyectos/ejemplo");
    assert.equal(entry.title, "una conversacion");
    assert.ok(entry.started_at, "deberia sellar started_at");
  });

  it("ignora las sesiones hijas, que son subagentes y no terminales", async () => {
    const plugin = await makePlugin();
    await plugin.event(created(rootSession("s2", { parentID: "s1" })));

    assert.deepEqual(readState(), {});
  });

  it("recorta los titulos largos", async () => {
    const plugin = await makePlugin();
    const largo = "t".repeat(80);
    await plugin.event(created(rootSession("s1", { title: largo })));

    const { title } = readState()["opencode:s1"];
    assert.ok(title.length <= 43, `titulo sin recortar: ${title.length}`);
    assert.ok(title.endsWith("…"));
  });
});

describe("estados de sesion", () => {
  it("session.idle marca terminado", async () => {
    const plugin = await makePlugin({ s1: rootSession("s1") });
    await plugin.event({
      event: { type: "session.idle", properties: { sessionID: "s1" } },
    });

    assert.equal(readState()["opencode:s1"].state, "terminado");
  });

  it("session.error guarda el mensaje del error", async () => {
    const plugin = await makePlugin({ s1: rootSession("s1") });
    await plugin.event({
      event: {
        type: "session.error",
        properties: {
          sessionID: "s1",
          error: { data: { message: "algo se rompio" } },
        },
      },
    });

    const entry = readState()["opencode:s1"];
    assert.equal(entry.state, "error");
    assert.equal(entry.detail, "algo se rompio");
  });

  it("session.deleted borra la entrada", async () => {
    const plugin = await makePlugin();
    await plugin.event(created(rootSession("s1")));
    assert.ok(readState()["opencode:s1"]);

    await plugin.event({
      event: { type: "session.deleted", properties: { info: { id: "s1" } } },
    });

    assert.equal(readState()["opencode:s1"], undefined);
  });

  it("session.updated refresca el titulo sin tocar el estado", async () => {
    const plugin = await makePlugin();
    await plugin.event(created(rootSession("s1", { title: "viejo" })));
    await plugin.event({
      event: {
        type: "session.updated",
        properties: { info: rootSession("s1", { title: "nuevo" }) },
      },
    });

    const entry = readState()["opencode:s1"];
    assert.equal(entry.title, "nuevo");
    assert.equal(entry.state, "iniciado", "el estado no deberia cambiar");
  });

  it("ignora los eventos que no reconoce", async () => {
    const plugin = await makePlugin({ s1: rootSession("s1") });
    await plugin.event({
      event: { type: "algo.inventado", properties: { sessionID: "s1" } },
    });

    assert.deepEqual(readState(), {});
  });
});

describe("permisos", () => {
  for (const type of ["permission.asked", "permission.updated"]) {
    it(`${type} deja la sesion esperando`, async () => {
      const plugin = await makePlugin({ s1: rootSession("s1") });
      await plugin.event({
        event: { type, properties: { sessionID: "s1", title: "Ejecutar bash" } },
      });

      const entry = readState()["opencode:s1"];
      assert.equal(entry.state, "esperando");
      assert.equal(entry.detail, "Ejecutar bash");
    });
  }

  it("permission.replied devuelve la sesion a trabajando", async () => {
    const plugin = await makePlugin({ s1: rootSession("s1") });
    await plugin.event({
      event: { type: "permission.replied", properties: { sessionID: "s1" } },
    });

    assert.equal(readState()["opencode:s1"].state, "trabajando");
  });
});

describe("actividad", () => {
  it("tool.execute.before nombra la herramienta", async () => {
    const plugin = await makePlugin({ s1: rootSession("s1") });
    await plugin["tool.execute.before"]({ sessionID: "s1", tool: "bash" });

    const entry = readState()["opencode:s1"];
    assert.equal(entry.state, "trabajando");
    assert.equal(entry.detail, "Usando bash");
  });

  it("chat.message usa el texto de la respuesta, recortado", async () => {
    const plugin = await makePlugin({ s1: rootSession("s1") });
    await plugin["chat.message"](
      { sessionID: "s1" },
      { parts: [{ type: "text", text: "x".repeat(200) }] },
    );

    const { detail } = readState()["opencode:s1"];
    assert.ok(detail.length <= 81, `detalle sin recortar: ${detail.length}`);
  });

  it("chat.message sin texto cae en un detalle generico", async () => {
    const plugin = await makePlugin({ s1: rootSession("s1") });
    await plugin["chat.message"]({ sessionID: "s1" }, { parts: [] });

    assert.equal(readState()["opencode:s1"].detail, "Procesando");
  });
});

describe("robustez", () => {
  it("un evento sin sessionID no escribe nada ni lanza", async () => {
    const plugin = await makePlugin();
    await plugin.event({ event: { type: "session.idle", properties: {} } });

    assert.deepEqual(readState(), {});
  });

  it("si la sesion no se puede consultar se asume raiz y se publica", async () => {
    // Perder una sesion real del usuario es peor que mostrar una de mas.
    const plugin = await makePlugin();
    await plugin.event({
      event: { type: "session.idle", properties: { sessionID: "fantasma" } },
    });

    assert.ok(readState()["opencode:fantasma"]);
  });

  it("respeta el started_at ya existente", async () => {
    writeState({
      "opencode:s1": { started_at: "2020-01-01T00:00:00+00:00", state: "iniciado" },
    });
    const plugin = await makePlugin({ s1: rootSession("s1") });
    await plugin.event({
      event: { type: "session.idle", properties: { sessionID: "s1" } },
    });

    const entry = readState()["opencode:s1"];
    assert.equal(entry.started_at, "2020-01-01T00:00:00+00:00");
    assert.equal(entry.state, "terminado");
  });

  it("no deja crecer el archivo sin limite y conserva la sesion en curso", async () => {
    const viejas = {};
    for (let i = 0; i < 60; i++) {
      viejas[`opencode:vieja-${i}`] = {
        state: "terminado",
        updated_at: `2020-01-01T00:${String(i).padStart(2, "0")}:00+00:00`,
      };
    }
    writeState(viejas);

    const plugin = await makePlugin({ s1: rootSession("s1") });
    await plugin.event({
      event: { type: "session.idle", properties: { sessionID: "s1" } },
    });

    const data = readState();
    assert.ok(Object.keys(data).length <= 51, "deberia purgar las mas antiguas");
    assert.ok(data["opencode:s1"], "la sesion en curso nunca se purga");
  });

  it("no deja el lock puesto tras escribir", async () => {
    const plugin = await makePlugin();
    await plugin.event(created(rootSession("s1")));

    const lock = path.join(ROOT, "claude-status-widget", "status.lock");
    assert.equal(fs.existsSync(lock), false, "el lock deberia liberarse");
  });

  it("dispose marca las sesiones conocidas como terminadas", async () => {
    const plugin = await makePlugin();
    await plugin.event(created(rootSession("s1")));
    await plugin.dispose();

    const entry = readState()["opencode:s1"];
    assert.equal(entry.state, "terminado");
    assert.equal(entry.detail, "OpenCode cerrado");
  });
});
