import { listen } from "@tauri-apps/api/event";
import { Command } from "@tauri-apps/plugin-shell";
import { register, unregisterAll } from "@tauri-apps/plugin-global-shortcut";
import { readText, writeText } from "@tauri-apps/plugin-clipboard-manager";
import {
  enable as enableAutostart,
  disable as disableAutostart,
  isEnabled as isAutostartEnabled,
} from "@tauri-apps/plugin-autostart";

const DEFAULTS = {
  engine: "kitten",
  target: "",
  voice: "",
  language: "en-US",
  speaker: "",
  offline: true,
  readHotkey: "CommandOrControl+Alt+Space",
  stopHotkey: "CommandOrControl+Alt+Escape",
  copyDelay: 140,
  maxChars: 8000,
  autostart: false,
};

const $ = (id) => document.getElementById(id);
let settings = loadSettings();
let child = null;
let sequence = 1;
let pending = new Map();
let currentAudio = null;
let currentAudioUrl = null;

function loadSettings() {
  try {
    return {
      ...DEFAULTS,
      ...JSON.parse(localStorage.getItem("voxselect.settings") || "{}"),
    };
  } catch {
    return { ...DEFAULTS };
  }
}

function saveLocalSettings(value) {
  localStorage.setItem("voxselect.settings", JSON.stringify(value));
}

function setStatus(message, kind = "") {
  const node = $("status");
  node.textContent = message;
  node.className = `status ${kind}`.trim();
}

function formToSettings() {
  return {
    engine: $("engine").value.trim(),
    target: $("target").value.trim(),
    voice: $("voice").value.trim(),
    language: $("language").value.trim() || "en-US",
    speaker: $("speaker").value.trim(),
    offline: $("offline").checked,
    readHotkey: $("readHotkey").value.trim(),
    stopHotkey: $("stopHotkey").value.trim(),
    copyDelay: Number($("copyDelay").value || 140),
    maxChars: Number($("maxChars").value || 8000),
    autostart: $("autostart").checked,
  };
}

function settingsToForm(value) {
  $("engine").value = value.engine || "";
  $("target").value = value.target || "";
  $("voice").value = value.voice || "";
  $("language").value = value.language || "en-US";
  $("speaker").value = value.speaker || "";
  $("offline").checked = Boolean(value.offline);
  $("readHotkey").value = value.readHotkey || DEFAULTS.readHotkey;
  $("stopHotkey").value = value.stopHotkey || DEFAULTS.stopHotkey;
  $("copyDelay").value = String(value.copyDelay || DEFAULTS.copyDelay);
  $("maxChars").value = String(value.maxChars || DEFAULTS.maxChars);
  $("autostart").checked = Boolean(value.autostart);
}

async function startSidecar() {
  const command = Command.sidecar("binaries/voxselect-readio-service");
  command.stdout.on("data", handleSidecarLine);
  command.stderr.on("data", (line) => console.error("[readio-sidecar]", line));
  command.on("error", (error) => setStatus(`Sidecar error: ${error}`, "error"));
  command.on("close", ({ code }) => {
    child = null;
    setStatus(`Readio service exited (${code ?? "unknown"})`, "error");
  });
  child = await command.spawn();
  await request("ping", {});
  await configureSidecar();
}

function handleSidecarLine(line) {
  const raw = typeof line === "string" ? line.trim() : String(line).trim();
  if (!raw) return;
  let message;
  try {
    message = JSON.parse(raw);
  } catch {
    console.warn("Unparsed sidecar output:", raw);
    return;
  }

  if (message.event === "audio") {
    if (message.wav_base64) playBase64Wav(message.wav_base64);
    return;
  }
  if (message.event === "speech_error") {
    setStatus(message.message || "Speech failed", "error");
    return;
  }
  if (message.event === "speech_canceled") {
    setStatus("Stopped", "ok");
    return;
  }

  if (message.id && pending.has(message.id)) {
    const { resolve, reject, timer } = pending.get(message.id);
    clearTimeout(timer);
    pending.delete(message.id);
    if (message.ok === false)
      reject(new Error(message.error || "Sidecar request failed"));
    else resolve(message.result ?? message);
  }
}

async function request(command, payload = {}, timeoutMs = 15000) {
  if (!child) throw new Error("Readio service is not running");
  const id = `r${sequence++}`;
  const body = JSON.stringify({ id, command, ...payload }) + "\n";
  const promise = new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`${command} timed out`));
    }, timeoutMs);
    pending.set(id, { resolve, reject, timer });
  });
  await child.write(body);
  return promise;
}

async function configureSidecar() {
  await request("configure", {
    settings: {
      engine: settings.engine || null,
      target: settings.target || null,
      voice: settings.voice || null,
      language: settings.language || "en-US",
      speaker: settings.speaker || null,
      offline: Boolean(settings.offline),
      max_chars: Number(settings.maxChars || DEFAULTS.maxChars),
    },
  });
}

function base64ToBlob(base64, contentType) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: contentType });
}

function stopPlayback(sendStop = true) {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio.currentTime = 0;
    currentAudio = null;
  }
  if (currentAudioUrl) {
    URL.revokeObjectURL(currentAudioUrl);
    currentAudioUrl = null;
  }
  if (sendStop && child) request("stop", {}).catch(() => {});
  setStatus("Stopped", "ok");
}

async function playBase64Wav(base64) {
  stopPlayback(false);
  const blob = base64ToBlob(base64, "audio/wav");
  currentAudioUrl = URL.createObjectURL(blob);
  currentAudio = new Audio(currentAudioUrl);
  currentAudio.addEventListener(
    "ended",
    () => {
      if (currentAudioUrl) URL.revokeObjectURL(currentAudioUrl);
      currentAudio = null;
      currentAudioUrl = null;
      setStatus("Ready", "ok");
    },
    { once: true }
  );
  try {
    await currentAudio.play();
    setStatus("Speaking…", "ok");
  } catch (error) {
    setStatus(`Audio playback failed: ${error}`, "error");
  }
}

async function speakText(text) {
  const clean = String(text || "").trim();
  if (!clean) {
    setStatus("No text to read", "error");
    return;
  }
  const max = Number(settings.maxChars || DEFAULTS.maxChars);
  if (clean.length > max) {
    setStatus(
      `Selection is ${clean.length} characters; limit is ${max}`,
      "error"
    );
    return;
  }
  stopPlayback();
  setStatus("Synthesizing…");
  await request("speak", { text: clean });
}

async function copyCurrentSelection() {
  const oldClipboard = await readText().catch(() => "");
  const sentinel = `__VOXSELECT_${Date.now()}_${Math.random()
    .toString(16)
    .slice(2)}__`;
  await writeText(sentinel);
  await request("copy_selection", {});
  await new Promise((resolve) =>
    setTimeout(resolve, Number(settings.copyDelay || 140))
  );
  const selected = await readText().catch(() => sentinel);
  await writeText(oldClipboard || "").catch(() => {});
  if (!selected || selected === sentinel) return "";
  return selected;
}

async function readSelection() {
  try {
    setStatus("Reading selection…");
    const text = await copyCurrentSelection();
    if (!text) {
      setStatus("No selectable text captured. Try Read clipboard.", "error");
      return;
    }
    await speakText(text);
  } catch (error) {
    setStatus(`Selection capture failed: ${error.message || error}`, "error");
  }
}

async function readClipboard() {
  const text = await readText().catch(() => "");
  if (!text.trim()) {
    setStatus("Clipboard has no text", "error");
    return;
  }
  await speakText(text);
}

async function registerHotkeys() {
  await unregisterAll();
  if (settings.readHotkey) {
    await register(settings.readHotkey, (event) => {
      const state = String(event.state || "").toLowerCase();
      if (!state || state.includes("release")) readSelection();
    });
  }
  if (settings.stopHotkey) {
    await register(settings.stopHotkey, (event) => {
      const state = String(event.state || "").toLowerCase();
      if (!state || state.includes("release")) stopPlayback();
    });
  }
}

async function applyAutostart() {
  const currently = await isAutostartEnabled().catch(() => false);
  if (settings.autostart && !currently) await enableAutostart();
  if (!settings.autostart && currently) await disableAutostart();
}

async function saveSettings() {
  settings = formToSettings();
  saveLocalSettings(settings);
  try {
    await configureSidecar();
    await registerHotkeys();
    await applyAutostart();
    setStatus("Settings saved", "ok");
  } catch (error) {
    setStatus(`Could not apply settings: ${error.message || error}`, "error");
  }
}

async function inspectEngines() {
  try {
    const result = await request("engines", {}, 30000);
    const node = $("engineInfo");
    node.hidden = false;
    node.textContent = JSON.stringify(result, null, 2);
  } catch (error) {
    setStatus(`Engine inspection failed: ${error.message || error}`, "error");
  }
}

async function init() {
  settingsToForm(settings);
  try {
    $("autostart").checked = await isAutostartEnabled().catch(
      () => settings.autostart
    );
    settings.autostart = $("autostart").checked;
    await startSidecar();
    await registerHotkeys();
    setStatus("Ready", "ok");
  } catch (error) {
    setStatus(`Startup failed: ${error.message || error}`, "error");
  }

  $("save").addEventListener("click", saveSettings);
  $("stop").addEventListener("click", () => stopPlayback());
  $("speakManual").addEventListener("click", () =>
    speakText($("manualText").value)
  );
  $("testVoice").addEventListener("click", async () => {
    settings = formToSettings();
    saveLocalSettings(settings);
    await configureSidecar();
    await speakText("VoxSelect is ready to read selected text locally.");
  });
  $("discover").addEventListener("click", inspectEngines);

  await listen("tray-open", () => window.focus());
  await listen("tray-read-selection", () => readSelection());
  await listen("tray-read-clipboard", () => readClipboard());
  await listen("tray-stop", () => stopPlayback());
}

window.addEventListener("beforeunload", () => {
  try {
    child?.write(
      JSON.stringify({ id: "shutdown", command: "shutdown" }) + "\n"
    );
  } catch {}
});

init();
