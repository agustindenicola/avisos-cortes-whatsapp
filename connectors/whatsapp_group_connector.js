const fs = require("fs");
const path = require("path");
const qrcode = require("qrcode-terminal");
const qrcodeImage = require("qrcode");
const { Client, LocalAuth } = require("whatsapp-web.js");

const root = path.resolve(__dirname, "..");
const configPath = process.env.WSP_GROUP_CONFIG || path.join(root, "config", "grupos_wsp_permitidos.json");
const statePath = path.join(root, "data", "group-connector-state.json");
const logPath = path.join(root, "data", "group-connector.log");
const qrImagePath = path.join(root, "data", "whatsapp-qr.png");

function loadEnvFile(filePath) {
  if (!fs.existsSync(filePath)) return;
  const lines = fs.readFileSync(filePath, "utf8").split(/\r?\n/);
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const index = line.indexOf("=");
    const key = line.slice(0, index).trim();
    const value = line.slice(index + 1).trim().replace(/^['"]|['"]$/g, "");
    if (key && process.env[key] === undefined) {
      process.env[key] = value;
    }
  }
}

function findChromeExecutable() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH && fs.existsSync(process.env.PUPPETEER_EXECUTABLE_PATH)) {
    return process.env.PUPPETEER_EXECUTABLE_PATH;
  }

  const cacheRoot = path.join(process.env.HOME || "", ".cache", "puppeteer");
  const candidates = [
    path.join(cacheRoot, "chrome-headless-shell", "linux-146.0.7680.31", "chrome-headless-shell-linux64", "chrome-headless-shell"),
    path.join(cacheRoot, "chrome", "linux-146.0.7680.31", "chrome-linux64", "chrome"),
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
  ];
  const found = candidates.find((candidate) => fs.existsSync(candidate));
  if (found) return found;
  try {
    return require("puppeteer").executablePath();
  } catch {
    return undefined;
  }
}

function loadJson(filePath, fallback) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return fallback;
  }
}

function writeJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(value, null, 2));
}

function log(message, extra = {}) {
  const line = JSON.stringify({ time: new Date().toISOString(), message, ...extra });
  console.log(line);
  fs.mkdirSync(path.dirname(logPath), { recursive: true });
  fs.appendFileSync(logPath, `${line}\n`);
}

function allowedGroups(config) {
  const byName = new Map();
  const byId = new Map();
  for (const group of config.groups || []) {
    if (!group.enabled) continue;
    if (group.name) byName.set(group.name.trim().toLowerCase(), group);
    if (group.id) byId.set(group.id, group);
  }
  return { byName, byId };
}

function mediaToDataUrl(media) {
  return `data:${media.mimetype || "image/jpeg"};base64,${media.data}`;
}

function fileNameFor(message, media) {
  const extension = (media.mimetype || "image/jpeg").split("/")[1] || "jpg";
  const safeId = String(message.id?._serialized || Date.now()).replace(/[^a-zA-Z0-9._-]/g, "-");
  return `grupo-${safeId}.${extension}`;
}

async function postToApp(config, payload) {
  const headers = { "Content-Type": "application/json" };
  if (process.env.GROUP_CONNECTOR_TOKEN) {
    headers["X-Connector-Token"] = process.env.GROUP_CONNECTOR_TOKEN;
  }
  const response = await fetch(config.apiUrl, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  if (!response.ok) {
    throw new Error(`App responded ${response.status}: ${text}`);
  }
  return text;
}

async function checkConnector(config) {
  const chrome = findChromeExecutable();
  const groups = allowedGroups(config);
  const report = {
    configPath,
    apiUrl: config.apiUrl || "",
    enabledGroups: groups.byName.size || groups.byId.size,
    chromeExecutable: chrome || "",
    authDirectoryExists: fs.existsSync(path.join(root, ".wwebjs_auth", "session")),
    connectorTokenConfigured: Boolean(process.env.GROUP_CONNECTOR_TOKEN),
  };

  let ok = true;
  if (!config.apiUrl) {
    ok = false;
    report.api = "Falta apiUrl";
  } else {
    try {
      const response = await fetch(config.apiUrl.replace(/\/api\/group-connector$/, "/health"), { method: "GET" });
      report.api = response.ok ? "Panel responde" : `Panel respondio ${response.status}`;
      ok = ok && response.ok;
    } catch (error) {
      ok = false;
      report.api = `No responde: ${error.message}`;
    }
  }
  if (!report.enabledGroups) ok = false;
  if (!chrome) ok = false;

  console.log(JSON.stringify({ ok, ...report }, null, 2));
  process.exit(ok ? 0 : 1);
}

async function main() {
  loadEnvFile(path.join(root, ".env"));
  const config = loadJson(configPath, null);
  if (!config || !Array.isArray(config.groups)) {
    throw new Error(`Config invalida: ${configPath}`);
  }
  if (process.env.CHECK_CONNECTOR === "1") {
    await checkConnector(config);
    return;
  }

  const client = new Client({
    authStrategy: new LocalAuth({ dataPath: path.join(root, ".wwebjs_auth") }),
    puppeteer: {
      headless: true,
      executablePath: findChromeExecutable(),
      args: ["--no-sandbox", "--disable-setuid-sandbox"],
    },
  });

  client.on("qr", async (qr) => {
    await qrcodeImage.toFile(qrImagePath, qr, { width: 720, margin: 2 });
    log("Escanea este QR con el WhatsApp Business municipal", { file: qrImagePath });
    qrcode.generate(qr, { small: true });
  });

  client.on("authenticated", () => log("WhatsApp autenticado"));
  client.on("ready", async () => {
    writeJson(statePath, {
      status: "ok",
      lastReadyAt: new Date().toISOString(),
      detail: "Conector listo",
    });
    log("Conector listo");
    if (process.env.LIST_GROUPS === "1") {
      const chats = await client.getChats();
      const groups = chats
        .filter((chat) => chat.isGroup)
        .map((chat) => ({ name: chat.name, id: chat.id?._serialized || "" }))
        .sort((left, right) => left.name.localeCompare(right.name));
      console.log(JSON.stringify(groups, null, 2));
      log("Listado de grupos generado", { count: groups.length });
      await client.destroy();
      process.exit(0);
    }
  });
  client.on("auth_failure", (message) => log("Fallo de autenticacion", { detail: message }));
  client.on("disconnected", (reason) => log("WhatsApp desconectado", { reason }));

  const processedMessages = new Set();

  async function handleMessage(message, eventName) {
    try {
      const messageId = message.id?._serialized || "";
      if (messageId && processedMessages.has(messageId)) return;
      if (messageId) {
        processedMessages.add(messageId);
        if (processedMessages.size > 500) {
          processedMessages.delete(processedMessages.values().next().value);
        }
      }

      const chat = await message.getChat();
      if (!chat.isGroup) return;

      const groups = allowedGroups(config);
      const groupConfig = groups.byId.get(chat.id?._serialized) || groups.byName.get((chat.name || "").trim().toLowerCase());
      if (!groupConfig) return;

      let media = null;
      let fileName = "";
      let imageData = "";
      if (message.hasMedia) {
        media = await message.downloadMedia();
        if (!media || !media.mimetype || !media.mimetype.startsWith("image/")) {
          log("Media ignorada porque no es imagen", { group: chat.name, mimetype: media?.mimetype || "", event: eventName });
          return;
        }
        fileName = fileNameFor(message, media);
        imageData = mediaToDataUrl(media);
      }

      const contact = await message.getContact();
      const text = message.body || (message.hasMedia ? "Aviso recibido por imagen de grupo" : "");
      if (!text && !imageData) {
        log("Mensaje ignorado porque no tiene texto ni imagen", { group: chat.name, event: eventName });
        return;
      }

      const payload = {
        group: chat.name,
        groupId: chat.id?._serialized || "",
        operationalGroup: groupConfig.operationalGroup || "",
        sender: contact.pushname || contact.name || message.author || message.from,
        message: text,
        messageId,
        timestamp: message.timestamp || Math.floor(Date.now() / 1000),
        fileName,
        imageData,
      };

      const result = await postToApp(config, payload);
      const state = {
        status: "ok",
        lastProcessedAt: new Date().toISOString(),
        lastGroup: chat.name,
        lastMessageId: payload.messageId,
        lastResponse: result,
      };
      writeJson(statePath, state);
      log(imageData ? "Imagen enviada a la app" : "Texto enviado a la app", {
        group: chat.name,
        event: eventName,
        fromMe: Boolean(message.fromMe),
        response: result,
      });
    } catch (error) {
      writeJson(statePath, {
        status: "error",
        lastErrorAt: new Date().toISOString(),
        error: error.message,
      });
      log("Error procesando mensaje", { error: error.message, stack: error.stack });
    }
  }

  client.on("message", (message) => handleMessage(message, "message"));
  client.on("message_create", (message) => handleMessage(message, "message_create"));

  await client.initialize();
}

main().catch((error) => {
  log("Error fatal del conector", { error: error.message, stack: error.stack });
  process.exit(1);
});
