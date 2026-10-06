"use strict";
/* Consola web sobre GitHub Actions.
 *
 * - Dispara workflows con repository_dispatch (API de GitHub).
 * - Lee la salida commiteada en runs/<id>/run.json por polling.
 * - El token vive solo en localStorage y solo viaja a api.github.com.
 */

const $ = (selector) => document.querySelector(selector);
const CFG = window.CONSOLE_CONFIG || {};
const LS = "gh-console.v1";
const LS_HIST = "gh-console.history.v1";
const API = "https://api.github.com";
const TERMINAL_STATES = ["completed", "failed", "killed", "rejected", "error"];

const ANSI_COLORS = {
  30: "#abb2bf", 31: "#e06c75", 32: "#98c379", 33: "#e5c07b", 34: "#61afef",
  35: "#c678dd", 36: "#56b6c2", 37: "#abb2bf", 90: "#5c6370", 91: "#e06c75",
  92: "#98c379", 93: "#e5c07b", 94: "#61afef", 95: "#c678dd", 96: "#56b6c2",
  97: "#ffffff",
};

const state = {
  token: "",
  owner: "",
  repo: "",
  branch: "",
  actor: "",
  history: [],
  histIndex: -1,
  active: new Set(),
};

/* ----------------------------------------------------------------- utils */

function store(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* ignore */ }
}
function load(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch (e) { return fallback; }
}

function esc(text) {
  return String(text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function ansiToHtml(text) {
  const cleaned = String(text)
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    // SGR se procesa abajo; el resto de secuencias se descartan.
    .replace(/\x1b\[[0-9;]*[A-Za-z]/g, (m) =>
      /[JKHABCDfsu]$/.test(m) ? "" : m);
  const parts = cleaned.split(/\x1b\[([0-9;]*)m/g);
  let html = "";
  const open = [];
  parts.forEach((part, i) => {
    if (i % 2 === 0) {
      html += esc(part);
      return;
    }
    for (const raw of part.split(";")) {
      const code = raw === "" ? 0 : parseInt(raw, 10);
      if (code === 0) {
        html += open.splice(0).reverse().join("");
      } else if (code === 1) {
        html += "<b>"; open.push("</b>");
      } else if (code === 4) {
        html += "<u>"; open.push("</u>");
      } else if (ANSI_COLORS[code]) {
        html += `<span style="color:${ANSI_COLORS[code]}">`;
        open.push("</span>");
      }
    }
  });
  return html + open.splice(0).reverse().join("");
}

function now() { return new Date().toLocaleTimeString(); }

/* ------------------------------------------------------------- terminal */

function print(html, cls = "") {
  const line = document.createElement("div");
  line.className = `line ${cls}`;
  line.innerHTML = html;
  const term = $("#terminal");
  term.appendChild(line);
  term.scrollTop = term.scrollHeight;
  return line;
}

function printText(text, cls) { return print(ansiToHtml(text), cls); }

function setStatus(cls, text) {
  const el = $("#status");
  el.className = cls;
  el.textContent = text;
}

function updatePrompt() {
  const where = state.repo ? `${state.repo}` : "repo?";
  const who = state.actor || "guest";
  $("#prompt").textContent = `${who}@${where}:~$`;
  $("#whoami").textContent = state.actor
    ? `↪ ${state.actor}`
    : (state.token ? "verificando…" : "no autenticado");
}

function autosizeHint() { /* placeholder para futuras florituras */ }

/* --------------------------------------------------------------- GitHub */

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function gh(path, options = {}) {
  if (!state.token) throw new ApiError(401, "No hay token. Escribe 'auth' para configurarlo.");
  const url = new URL(API + path);
  if (!("cacheBust" in options) || options.cacheBust !== false) {
    url.searchParams.set("_t", String(Date.now()));
  }
  let response;
  try {
    response = await fetch(url, {
      method: options.method || "GET",
      headers: {
        Authorization: `Bearer ${state.token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
      },
      body: options.body ? JSON.stringify(options.body) : null,
    });
  } catch (e) {
    throw new ApiError(0, `No se pudo conectar con ${API}: ${e.message}`);
  }
  if (response.status === 204) return null;
  let payload = null;
  const text = await response.text();
  try { payload = text ? JSON.parse(text) : null; } catch (e) { payload = text; }
  if (!response.ok) {
    const msg = payload && payload.message ? payload.message : response.statusText;
    if (response.status === 401 || response.status === 403) {
      const scopes = response.headers.get("x-oauth-scopes");
      const remaining = response.headers.get("x-ratelimit-remaining");
      const hint = remaining === "0"
        ? " (rate limit agotado: espera unos minutos)"
        : scopes !== null && !scopes.includes("repo") && !scopes.includes("workflow")
          ? " (el token no tiene los permisos necesarios)"
          : "";
      throw new ApiError(response.status, `${msg}${hint}`);
    }
    throw new ApiError(response.status, msg);
  }
  return payload;
}

function repoPath(extra = "") {
  return `/repos/${state.owner}/${state.repo}${extra}`;
}

async function verifyToken() {
  try {
    const user = await gh("/user", { cacheBust: false });
    state.actor = user.login || "";
    updatePrompt();
    return state.actor;
  } catch (e) {
    state.actor = "";
    updatePrompt();
    throw e;
  }
}

/* ----------------------------------------------------------- ejecución */

async function dispatch(command) {
  const id = (crypto.randomUUID && crypto.randomUUID()) ||
    `run-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const body = {
    event_type: "console_run",
    client_payload: { id, command, ref: state.branch || undefined },
  };
  await gh(repoPath("/dispatches"), { method: "POST", body });
  return id;
}

function artifactHtml(name) {
  const ext = name.split(".").pop().toLowerCase();
  const image = ["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext);
  if (!image) {
    return `<div class="line dim">📎 ${esc(name)} (descárgalo desde runs/${esc(name)})</div>`;
  }
  return `<figure><img alt="${esc(name)}" data-artifact="${esc(name)}" loading="lazy" />
          <figcaption>${esc(name)}</figcaption></figure>`;
}

// La API de contents no devuelve contenido inline si el archivo pasa de 1MB;
// en ese caso cae a la blob API (necesita el sha, que viene en la respuesta).
async function readFile(file) {
  if (file.encoding !== "base64" && file.git_blob_url) {
    const sha = file.git_blob_url.split("/").pop();
    const blob = await gh(repoPath(`/git/blobs/${sha}`));
    return atob(blob.content.replace(/\n/g, ""));
  }
  return atob((file.content || "").replace(/\n/g, ""));
}

async function loadArtifactImages(container, runId) {
  for (const img of container.querySelectorAll("img[data-artifact]")) {
    const name = img.getAttribute("data-artifact");
    try {
      const file = await gh(repoPath(
        `/contents/runs/${encodeURIComponent(runId)}/${encodeURIComponent(name)}`));
      const data = await readFile(file);
      img.src = `data:image/${name.split(".").pop()};base64,${data}`;
    } catch (e) {
      img.alt = `No se pudo cargar ${name}: ${e.message}`;
    }
  }
}

async function pollRun(runId, outLine, startedAt) {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let lastSeen = "";
  const ref = state.branch ? `?ref=${encodeURIComponent(state.branch)}` : "";
  const path = repoPath(
    `/contents/runs/${encodeURIComponent(runId)}/run.json${ref}`);

  while (Date.now() - startedAt < (CFG.maxRunWaitMs || 600000)) {
    let run;
    try {
      const file = await gh(path);
      run = JSON.parse(await readFile(file));
    } catch (e) {
      if (e.status === 404) {
        // El workflow está en cola o todavía no commitea la primera versión.
        if (lastSeen !== "queued") {
          lastSeen = "queued";
          outLine.innerHTML =
            `<span class="line system">⏳ trabajo en cola… (concurrencia: 1 por repo)</span>`;
          setStatus("st-queued", "◌ en cola");
        }
        await sleep(CFG.pollIntervalMs || 3000);
        continue;
      }
      outLine.innerHTML = `<span class="line error">✗ error leyendo la salida: ${esc(e.message)}</span>`;
      setStatus("st-error", "✗ error");
      return;
    }

    const body = (run.stdout || "") +
      (run.stderr ? `\n--- stderr ---\n${run.stderr}` : "") +
      (run.error ? `\n${run.error}` : "");
    if (body !== lastSeen) {
      lastSeen = body;
      outLine.innerHTML = ansiToHtml(body) || "";
    }

    if (TERMINAL_STATES.includes(run.status)) {
      const label = {
        completed: "✓ completado", failed: "✗ falló", killed: "⌫ cancelado por timeout",
        rejected: "⛔ rechazado por seguridad", error: "✗ error interno",
      }[run.status] || run.status;
      const cls = run.status === "completed" ? "ok" : "error";
      const meta = [
        `exit ${run.exit_code ?? "n/a"}`,
        run.duration_ms != null ? `${(run.duration_ms / 1000).toFixed(1)}s` : null,
        run.mode ? `modo ${run.mode}` : null,
        run.actor ? `por ${run.actor}` : null,
        run.runner && run.runner.os ? `runner ${run.runner.os}` : null,
      ].filter(Boolean).join(" · ");
      printText(`${label} — ${meta}`, cls === "ok" ? "meta ok" : "meta error");
      if (run.run_url) {
        printText(`ver workflow: ${run.run_url}`, "dim");
      }
      if (Array.isArray(run.artifacts) && run.artifacts.length) {
        const art = print(`<div class="artifact">${
          run.artifacts.map((a) => artifactHtml(a.name)).join("")}</div>`);
        loadArtifactImages(art, runId);
      }
      setStatus(
        run.status === "completed" ? "st-completed" : `st-${run.status}`,
        run.status === "completed" ? "● ok" : label);
      return;
    }
    setStatus("st-running", "⟳ ejecutando");
    await sleep(CFG.pollIntervalMs || 3000);
  }
  printText("⏱ tiempo de espera agotado; el trabajo sigue en el runner. " +
    "Recarga y usa 'runs' para verlo.", "error");
  setStatus("st-idle", "● idle");
}

async function runRemote(command) {
  const promptLine = print(
    `<span class="p">${esc($("#prompt").textContent)}</span> ${esc(command)}`,
    "prompt-line");
  if (!state.token) {
    printText("✗ No hay token. Escribe 'auth' o pulsa ⚙ para configurarlo.", "error");
    return;
  }
  if (!state.owner || !state.repo) {
    printText("✗ No sé a qué repositorio apuntar. Escribe 'auth' para configurarlo.", "error");
    return;
  }
  let runId;
  try {
    runId = await dispatch(command);
  } catch (e) {
    printText(`✗ No se pudo disparar el workflow: ${e.message}`, "error");
    if (e.status === 404) {
      printText(`  ¿Existe ${state.owner}/${state.repo}? ¿Está el workflow ` +
        "console-run.yml en la rama principal?", "dim");
    }
    return;
  }
  const startedAt = Date.now();
  const outLine = print("", "");
  printText(`▶ run ${runId} enviado a GitHub Actions…`, "system");
  setStatus("st-queued", "◌ en cola");
  state.active.add(runId);
  try {
    await pollRun(runId, outLine, startedAt);
  } finally {
    state.active.delete(runId);
  }
}

/* ------------------------------------------------------- comandos locales */

const CLIENT_COMMANDS = {
  help: () => [
    "Comandos locales (no se ejecutan en el runner):",
    "  help        esta ayuda",
    "  auth        configurar token / repo (o pulsa ⚙)",
    "  whoami      usuario autenticado en GitHub",
    "  repo        repositorio y rama configurados",
    "  runs        últimas ejecuciones guardadas en runs/",
    "  show <id>   muestra la salida de una ejecución pasada",
    "  history     historial de comandos de esta sesión",
    "  clear       limpia la pantalla (Ctrl+L)",
    "  about       arquitectura y notas de seguridad",
    "",
    "Comandos remotos (se ejecutan de verdad en el runner):",
    "  ls -la, pwd, whoami, date, df -h, free -h, ps aux | head",
    "  cat examples/hello.sh, echo 'hola' | wc -c",
    "  python3 examples/colors.py   (ANSI + streaming)",
    "  bash examples/sysinfo.sh     (info del runner)",
    "  bash examples/gui_screenshot.sh  (GUI con Xvfb → captura PNG)",
    "",
    "Seguridad: lista blanca estricta, sin shell, sin -c. Usa el modo docker",
    "(variable CONSOLE_MODE=docker) para código arbitrario.",
  ],
  auth: () => { openSettings(); return ["Abriendo configuración…"]; },
  whoami: async () => {
    if (!state.token) return ["No hay token. Usa 'auth'."];
    try {
      const u = await gh("/user", { cacheBust: false });
      return [`✓ autenticado como ${u.login} (${u.name || "sin nombre"})`,
              `  plan: ${u.plan ? u.plan.name : "?"} · repos públicos: ${u.public_repos}`];
    } catch (e) { return [`✗ ${e.message}`]; }
  },
  repo: () => [
    `owner : ${state.owner || "(no configurado)"}`,
    `repo  : ${state.repo || "(no configurado)"}`,
    `rama  : ${state.branch || "(principal)"}`,
    `token : ${state.token ? "presente (solo en este navegador)" : "(no configurado)"}`,
  ],
  runs: async () => {
    if (!state.token) return ["No hay token. Usa 'auth'."];
    try {
      const list = await gh(repoPath("/contents/runs"), { cacheBust: false });
      if (!Array.isArray(list) || !list.length) return ["runs/ todavía está vacío."];
      const dirs = list.filter((e) => e.type === "dir").slice(-12).reverse();
      return ["Ejecuciones guardadas (más recientes primero):",
              ...dirs.map((d) => `  ${d.name}`),
              "Usa 'show <id>' para ver una completa."];
    } catch (e) { return [`✗ ${e.message}`]; }
  },
  show: async (args) => {
    if (!state.token) return ["No hay token. Usa 'auth'."];
    const id = args[0];
    if (!id) return ["Uso: show <id-de-ejecución>"];
    try {
      const ref = state.branch ? `?ref=${encodeURIComponent(state.branch)}` : "";
      const file = await gh(repoPath(`/contents/runs/${encodeURIComponent(id)}/run.json${ref}`));
      const run = JSON.parse(await readFile(file));
      printText(`──── run ${id} ────`, "meta");
      printText(`$ ${run.command}`, "prompt-line");
      if (run.stdout) printText(run.stdout);
      if (run.stderr) printText(`--- stderr ---\n${run.stderr}`, "stderr");
      if (run.error) printText(run.error, "error");
      if (Array.isArray(run.artifacts) && run.artifacts.length) {
        const art = print(`<div class="artifact">${
          run.artifacts.map((a) => artifactHtml(a.name)).join("")}</div>`);
        loadArtifactImages(art, id);
      }
      printText(`estado: ${run.status} · exit ${run.exit_code ?? "n/a"}`, "meta");
      return [];
    } catch (e) { return [`✗ ${e.message}`]; }
  },
  history: () => {
    if (!state.history.length) return ["(historial vacío)"];
    return state.history.map((c, i) => `  ${String(i + 1).padStart(3)}  ${c}`);
  },
  clear: () => { $("#terminal").innerHTML = ""; return null; },
  about: () => [
    "github-console — terminal web sobre GitHub Actions (sin servidor propio).",
    "",
    "Arquitectura:",
    "  1. Esta página (GitHub Pages) dispara repository_dispatch por la API.",
    "  2. El workflow .github/workflows/console-run.yml ejecuta el comando en",
    "     un runner de GitHub dentro de un sandbox.",
    "  3. El runner commitea la salida en runs/<id>/run.json cada ~3s.",
    "  4. La página hace polling a ese archivo y lo muestra (casi tiempo real).",
    "",
    "Seguridad:",
    "  · El token solo vive en localStorage de TU navegador.",
    "  · Lista blanca de comandos + sin shell + sin metacaracteres.",
    "  · Copia del repo sin .git (sin GITHUB_TOKEN al alcance).",
    "  · Namespace PID propio (unshare), ulimits y timeout por ejecución.",
    "  · Salida redactada de tokens antes de commitearse.",
    "",
    "Limitaciones: sin streaming bidireccional real (latencia ~3-6s),",
    "1 trabajo a la vez por repo, 6h máx de ejecución, sin puertos entrantes",
    "(no hay VNC accesible desde Internet sin un relay externo).",
  ],
};

async function handleClient(name, args) {
  const handler = CLIENT_COMMANDS[name];
  if (!handler) return false;
  const result = await handler(args);
  if (Array.isArray(result)) {
    result.forEach((line) => printText(line, line.startsWith("✗") ? "error" : ""));
  }
  return true;
}

/* ------------------------------------------------------------ settings */

function openSettings() {
  $("#set-token").value = state.token || "";
  $("#set-owner").value = state.owner || "";
  $("#set-repo").value = state.repo || "";
  $("#set-branch").value = state.branch || "";
  $("#set-verify").textContent = "";
  $("#settings-modal").hidden = false;
  $("#set-token").focus();
}

function closeSettings() { $("#settings-modal").hidden = true; $("#cmd").focus(); }

function saveSettings() {
  const token = $("#set-token").value.trim();
  const owner = $("#set-owner").value.trim();
  const repo = $("#set-repo").value.trim();
  const branch = $("#set-branch").value.trim();
  state.token = token;
  state.owner = owner || state.owner;
  state.repo = repo || state.repo;
  state.branch = branch;
  store(LS, { token: state.token, owner: state.owner, repo: state.repo, branch: state.branch });
  closeSettings();
  printText(`⚙ configuración guardada → ${state.owner}/${state.repo}`, "system");
  if (state.token) verifyToken().then(() => {
    printText(`✓ token válido: ${state.actor}`, "ok");
  }).catch((e) => printText(`✗ token inválido: ${e.message}`, "error"));
  updatePrompt();
}

function clearToken() {
  state.token = ""; state.actor = "";
  store(LS, { token: "", owner: state.owner, repo: state.repo, branch: state.branch });
  $("#set-token").value = "";
  $("#set-verify").textContent = "";
  updatePrompt();
  printText("Token borrado de este navegador.", "system");
}

/* ------------------------------------------------------------- arranque */

function detectRepo() {
  if (CFG.owner && CFG.repo) {
    return { owner: CFG.owner, repo: CFG.repo };
  }
  const host = location.hostname;
  if (host.endsWith(".github.io")) {
    const owner = host.slice(0, -".github.io".length);
    const repo = location.pathname.split("/").filter(Boolean)[0] || "";
    if (owner && repo) return { owner, repo };
  }
  return { owner: "", repo: "" };
}

function welcome() {
  printText("⬡ github-console — terminal sobre GitHub Actions", "system");
  printText(`repo: ${state.owner}/${state.repo || "?"} · rama: ${state.branch || "principal"}`, "dim");
  printText("");
  if (!state.token) {
    printText("1. Pulsa ⚙ (o escribe 'auth') y pega un token de GitHub.", "");
    printText("   Créalo en: https://github.com/settings/tokens?type=beta", "dim");
    printText("   Permisos: Actions (read/write) + Contents (read), solo este repo.", "dim");
    printText("2. Escribe 'help' para ver los comandos disponibles.", "");
  } else {
    printText("Token presente. Verificando… escribe 'help' para empezar.", "");
  }
  printText("");
}

function bindInput() {
  const input = $("#cmd");
  input.addEventListener("keydown", async (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      const raw = input.value;
      input.value = "";
      if (!raw.trim()) return;
      state.history.push(raw);
      if (state.history.length > (CFG.maxHistory || 100)) state.history.shift();
      store(LS_HIST, state.history);
      state.histIndex = state.history.length;
      const [name, ...args] = raw.trim().split(/\s+/);
      const handled = await handleClient(name, args);
      if (!handled) await runRemote(raw.trim());
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (state.histIndex > 0) {
        state.histIndex -= 1;
        input.value = state.history[state.histIndex] || "";
      }
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      if (state.histIndex < state.history.length - 1) {
        state.histIndex += 1;
        input.value = state.history[state.histIndex] || "";
      } else {
        state.histIndex = state.history.length;
        input.value = "";
      }
    } else if (event.key === "l" && event.ctrlKey) {
      event.preventDefault();
      CLIENT_COMMANDS.clear();
    } else if (event.key === "c" && event.ctrlKey) {
      // Cancela la última ejecución pendiente del lado cliente.
      event.preventDefault();
      if (state.active.size) {
        printText("^C (cliente: deja de hacer polling; el trabajo del runner " +
          "sigue hasta terminar o agotar el timeout)", "system");
      } else {
        printText("^C", "system");
      }
    }
  });

  $("#terminal").addEventListener("click", () => input.focus());
  $("#settings-btn").addEventListener("click", openSettings);
  $("#set-save").addEventListener("click", saveSettings);
  $("#set-close").addEventListener("click", closeSettings);
  $("#set-clear").addEventListener("click", clearToken);
  $("#settings-modal").addEventListener("click", (e) => {
    if (e.target.id === "settings-modal") closeSettings();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeSettings();
  });
}

function boot() {
  const saved = load(LS, {});
  const detected = detectRepo();
  state.token = saved.token || "";
  state.owner = saved.owner || CFG.owner || detected.owner || "";
  state.repo = saved.repo || CFG.repo || detected.repo || "";
  state.branch = saved.branch || CFG.branch || "";
  state.history = load(LS_HIST, []);
  state.histIndex = state.history.length;
  updatePrompt();
  bindInput();
  welcome();
  if (state.token) {
    verifyToken().catch((e) => printText(`✗ token inválido: ${e.message}`, "error"));
  }
  $("#cmd").focus();
}

document.addEventListener("DOMContentLoaded", boot);
