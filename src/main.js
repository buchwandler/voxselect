import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { SpeechGeneration } from "./sidecar_protocol.js";
import { SpeechRequestQueue } from "./speech_request_queue.js";
import {
  catalogLanguages,
  catalogOfflineSetting,
  chooseEngine,
  chooseLanguage,
  chooseTarget,
  chooseVoice,
  runtimeVoices,
  runtimeTargets,
  synthesisAvailability,
} from "./catalog_state.js";
import {
  base64ByteLength,
  createPlaybackTestWav,
  playAudioElement,
} from "./audio_playback.js";
import { canSpeak, SPEECH_STATES, speechStatus } from "./speech_ui_state.js";
import { sanitizeSettings } from "./settings_state.js";
import { initializeOptionalFeatures } from "./optional_features.js";
import {
  createSpeechTransaction,
  playbackDiagnostic,
} from "./speech_diagnostics.js";

const DEFAULTS = {
  engine: "kitten",
  target: "",
  voice: "",
  language: "en-US",
  speaker: "",
  offline: false,
  readHotkey: "CommandOrControl+Alt+Space",
  stopHotkey: "CommandOrControl+Alt+Escape",
  copyDelay: 140,
  maxChars: 8000,
  autostart: false,
};

const $ = (id) => document.getElementById(id);
let settings = loadSettings();
let currentAudio = null;
let currentAudioUrl = null;
const speechGeneration = new SpeechGeneration();
const speechRequests = new SpeechRequestQueue();
let speechTransaction = null;
let speechUiState = SPEECH_STATES.INITIALIZING;
let startupReady = false;
let synthesisAvailable = false;
let voiceCatalog = {
  capabilities: null,
  synthesis: null,
  engines: [],
  targetsByEngine: {},
  languages: [],
  voices: [],
  voiceCache: new Map(),
  offline: true,
  refresh: false,
  targetErrors: {},
  voiceError: "",
};
let shuttingDown = false;
const optionalFeatureWarnings = new Map();

function loadSettings() {
  try {
    return sanitizeSettings(
      JSON.parse(localStorage.getItem("voxselect.settings") || "{}"),
      DEFAULTS
    );
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

function renderFeatureWarnings() {
  const node = $("featureWarnings");
  node.hidden = optionalFeatureWarnings.size === 0;
  node.textContent = [...optionalFeatureWarnings.values()].join("\n");
}

function showNonFatalFeatureWarning(feature, error) {
  const guidance = {
    shortcuts:
      "Global shortcuts are unavailable. Test voice and Manual test remain usable. Check desktop permissions or choose different hotkeys.",
    autostart:
      "Start at login is unavailable. Speech remains usable. Check desktop autostart permissions or disable Start at login.",
  };
  const detail = error?.message || String(error);
  console.warn(`${feature} unavailable`, error);
  optionalFeatureWarnings.set(
    feature,
    `${guidance[feature]} Details: ${detail}`
  );
  renderFeatureWarnings();
}

function clearNonFatalFeatureWarning(feature) {
  optionalFeatureWarnings.delete(feature);
  renderFeatureWarnings();
}

function setSpeechState(state, detail = "") {
  speechUiState = state;
  document.body.dataset.speechState = state;
  const status = speechStatus(state, detail);
  setStatus(status.message, status.kind);
  const enabled = startupReady && synthesisAvailable && canSpeak(state, true);
  $("testVoice").disabled = !enabled;
  $("speakManual").disabled = !enabled;
  $("stop").disabled = ![
    "capturing_selection",
    "synthesizing",
    "playing",
    "speaking",
    "stopping",
  ].includes(state);
}

async function runUiAction(label, action) {
  try {
    await action();
  } catch (error) {
    console.error(label, error);
    setSpeechState(SPEECH_STATES.ERROR, `${label}: ${error?.message || error}`);
  }
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
  $("speaker").value = value.speaker || "";
  $("offline").checked = Boolean(value.offline);
  $("readHotkey").value = value.readHotkey || DEFAULTS.readHotkey;
  $("stopHotkey").value = value.stopHotkey || DEFAULTS.stopHotkey;
  $("copyDelay").value = String(value.copyDelay || DEFAULTS.copyDelay);
  $("maxChars").value = String(value.maxChars || DEFAULTS.maxChars);
  $("autostart").checked = Boolean(value.autostart);
}

async function startSidecar() {
  await invoke("backend_start");
}

function handleSidecarEvent(message) {
  if (
    !["audio", "speech_error", "speech_canceled", "progress"].includes(
      message.event
    )
  ) {
    return;
  }
  for (const event of speechGeneration.observe(message))
    handleSpeechEvent(event);
}

function setPlaybackDiagnostics(event, details = {}) {
  const node = $("playbackInfo");
  node.hidden = false;
  node.textContent = JSON.stringify(
    playbackDiagnostic(event, speechTransaction, details),
    null,
    2
  );
}

async function refreshDiagnostics() {
  const report = await request("diagnostics");
  const panel = $("diagnosticsInfo");
  panel.hidden = false;
  panel.textContent = JSON.stringify(report, null, 2);
}

function readyOrUnavailable() {
  if (startupReady && synthesisAvailable) setSpeechState(SPEECH_STATES.READY);
  else setSpeechState(SPEECH_STATES.ENGINE_UNAVAILABLE);
}

function handleSpeechEvent(message) {
  if (!speechGeneration.matches(message.generation)) return;
  if (message.event === "audio") {
    const wavBytes =
      message.wav_bytes ?? base64ByteLength(message.wav_base64 || "");
    if (speechTransaction) {
      speechTransaction.sidecar_generation = message.generation;
      speechTransaction.audio_received_at = new Date().toISOString();
      speechTransaction.wav_bytes = wavBytes;
      speechTransaction.frames = message.frames ?? null;
      speechTransaction.duration_seconds = message.duration_seconds ?? null;
      speechTransaction.peak = message.peak ?? null;
      speechTransaction.terminal_event = "audio";
    }
    setPlaybackDiagnostics("audio_received", {
      generation: message.generation,
      bytes: wavBytes,
      sample_rate: message.sample_rate,
      channels: message.channels,
      elapsed_seconds: message.elapsed_seconds,
      warnings: message.warnings || [],
    });
    setSpeechState(SPEECH_STATES.PLAYING);
    if (message.wav_base64)
      void playBase64Wav(message.wav_base64, message.generation);
    return;
  }
  if (message.event === "speech_error") {
    if (speechTransaction) {
      speechTransaction.sidecar_generation = message.generation;
      speechTransaction.terminal_event = "speech_error";
    }
    setPlaybackDiagnostics("speech_error", {
      generation: message.generation,
      error: message.error || null,
    });
    speechGeneration.fail(message.generation);
    setSpeechState(SPEECH_STATES.ERROR, message.message || "Speech failed");
    return;
  }
  if (message.event === "speech_canceled") {
    if (speechTransaction) speechTransaction.terminal_event = "speech_canceled";
    setPlaybackDiagnostics("speech_canceled", {
      generation: message.generation,
    });
    speechGeneration.fail(message.generation);
    readyOrUnavailable();
    return;
  }
  if (message.event === "progress") {
    setPlaybackDiagnostics("synthesis_progress", {
      generation: message.generation,
      stage: message.stage || null,
      message: message.message || "",
    });
    setSpeechState(SPEECH_STATES.SYNTHESIZING, message.message || "");
  }
}

async function request(command, params = {}) {
  const calls = {
    configure: () => invoke("readio_configure", { settings: params.settings }),
    capabilities: () => invoke("readio_capabilities"),
    synthesis_capabilities: () => invoke("readio_synthesis_capabilities"),
    engines: () => invoke("readio_engines"),
    targets: () =>
      invoke("readio_targets", {
        engine: params.engine,
        language: params.language ?? null,
        offline: Boolean(params.offline),
        refresh: Boolean(params.refresh),
      }),
    voices: () =>
      invoke("readio_voices", {
        engine: params.engine,
        target: params.target,
        language: params.language ?? null,
        offline: Boolean(params.offline),
        refresh: Boolean(params.refresh),
      }),
    diagnostics: () => invoke("readio_diagnostics"),
    speak: () => invoke("readio_speak", { text: params.text }),
    stop: () => invoke("readio_stop"),
    shutdown: () => invoke("backend_shutdown"),
  };
  const call = calls[command];
  if (!call) throw new Error(`Unsupported backend command: ${command}`);
  try {
    return await call();
  } catch (error) {
    console.error(`[backend] ${command} request failed`, error);
    throw error;
  }
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

function populateSelect(id, items, selected, getValue, getLabel, emptyLabel) {
  const select = $(id);
  select.replaceChildren();
  if (!items.length) {
    select.add(new Option(emptyLabel, "", true, true));
    select.disabled = true;
    return;
  }
  for (const item of items)
    select.add(new Option(getLabel(item), getValue(item)));
  select.value = selected;
  select.disabled = false;
}

function availableEngines(language) {
  return voiceCatalog.engines.filter(
    (engine) =>
      engine.runnable &&
      runtimeTargets(voiceCatalog.targetsByEngine[engine.id] || [], language)
        .length > 0
  );
}

async function fetchVoices(
  engine,
  target,
  language,
  { offline = true, refresh = false } = {}
) {
  const key = `${engine}\u0000${target}\u0000${language}\u0000${offline}`;
  if (!refresh && voiceCatalog.voiceCache.has(key))
    return voiceCatalog.voiceCache.get(key);
  const response = await request(
    "voices",
    { engine, target, language, offline, refresh },
    30000
  );
  const voices = Array.isArray(response?.result?.items)
    ? response.result.items
    : [];
  voiceCatalog.voiceCache.set(key, voices);
  return voices;
}

async function reconcileVoiceSelection(
  value = settings,
  { configure = false, persist = false } = {}
) {
  const language =
    chooseLanguage(value.language, voiceCatalog.languages) || DEFAULTS.language;
  const engines = availableEngines(language);
  const engine = chooseEngine(
    value.engine,
    engines,
    voiceCatalog.targetsByEngine,
    language
  );
  const targets = engine
    ? runtimeTargets(voiceCatalog.targetsByEngine[engine.id] || [], language)
    : [];
  const target = chooseTarget(value.target, targets, language);
  let voices = [];
  let voiceError = "";
  if (engine && target) {
    try {
      voices = runtimeVoices(
        await fetchVoices(engine.id, target.id, language, {
          offline: voiceCatalog.offline || Boolean(value.offline),
          refresh: voiceCatalog.refresh,
        })
      );
    } catch (error) {
      voiceError = error?.message || String(error);
    }
  }
  const voice = chooseVoice(value.voice, voices, target?.default_voice);
  settings = {
    ...settings,
    ...value,
    language,
    engine: engine?.id || "",
    target: target?.id || "",
    voice: voice?.id || "",
  };
  voiceCatalog.voices = voices;
  voiceCatalog.voiceError = voiceError;
  populateSelect(
    "language",
    voiceCatalog.languages,
    language,
    (item) => item,
    (item) => item,
    "No available languages"
  );
  populateSelect(
    "engine",
    engines,
    settings.engine,
    (item) => item.id,
    (item) => item.id,
    "No runnable engines for this language"
  );
  populateSelect(
    "target",
    targets,
    settings.target,
    (item) => item.id,
    (item) => item.id,
    "No runtime-available targets"
  );
  populateSelect(
    "voice",
    voices,
    settings.voice,
    (item) => item.id,
    (item) =>
      item.language_label ? `${item.id} (${item.language_label})` : item.id,
    voiceError || "No voices available for this target"
  );
  settingsToForm(settings);
  const availability = synthesisAvailability(
    voiceCatalog.synthesis,
    engine,
    target,
    voice
  );
  if (
    !engine &&
    voiceCatalog.synthesis?.result?.state === "ready" &&
    voiceCatalog.engines.some((item) => item.runnable)
  ) {
    availability.reason =
      "No runtime-available target is available for this language.";
  }
  if (!availability.available && voiceError && engine && target && !voice) {
    availability.reason = voiceError;
  }
  synthesisAvailable = availability.available;
  if (configure && startupReady) await configureSidecar();
  if (persist) saveLocalSettings(settings);
  renderCatalogInfo(availability);
  if (startupReady) {
    setSpeechState(
      availability.available
        ? SPEECH_STATES.READY
        : SPEECH_STATES.ENGINE_UNAVAILABLE,
      availability.reason
    );
  }
  return availability;
}

function renderCatalogInfo(availability) {
  const node = $("engineInfo");
  const targetCount = Object.values(voiceCatalog.targetsByEngine).flat().length;
  node.hidden = false;
  node.textContent = JSON.stringify(
    {
      readio_version: voiceCatalog.capabilities?.result?.readio_version || null,
      synthesis: voiceCatalog.synthesis?.result?.state || "unavailable",
      engines: voiceCatalog.engines.length,
      targets: targetCount,
      voices: voiceCatalog.voices.length,
      selected: {
        language: settings.language,
        engine: settings.engine || null,
        target: settings.target || null,
        voice: settings.voice || null,
      },
      available: availability.available,
      reason: availability.reason || null,
      catalog_errors: voiceCatalog.targetErrors,
      voice_error: voiceCatalog.voiceError || null,
    },
    null,
    2
  );
}

async function loadVoiceCatalogs({ offline = true, refresh = false } = {}) {
  startupReady = false;
  setSpeechState(SPEECH_STATES.INITIALIZING, "Loading voice catalogs");
  const [capabilities, synthesis, engineCatalog] = await Promise.all([
    request("capabilities", {}, 30000),
    request("synthesis_capabilities", {}, 30000),
    request("engines", {}, 30000),
  ]);
  const engines = Array.isArray(engineCatalog?.engines)
    ? engineCatalog.engines
    : [];
  const targetsByEngine = {};
  const targetErrors = {};
  await Promise.all(
    engines
      .filter((engine) => engine.runnable)
      .map(async (engine) => {
        try {
          const response = await request(
            "targets",
            { engine: engine.id, offline, refresh },
            30000
          );
          targetsByEngine[engine.id] = Array.isArray(response?.result?.items)
            ? response.result.items
            : [];
        } catch (error) {
          targetsByEngine[engine.id] = [];
          targetErrors[engine.id] = error?.message || String(error);
        }
      })
  );
  voiceCatalog = {
    capabilities,
    synthesis,
    engines,
    targetsByEngine,
    languages: catalogLanguages(targetsByEngine),
    voices: [],
    offline,
    refresh,
    voiceCache: new Map(),
    targetErrors,
    voiceError: "",
  };
  const availability = await reconcileVoiceSelection(settings);
  voiceCatalog.refresh = false;
  await configureSidecar();
  startupReady = true;
  saveLocalSettings(settings);
  setSpeechState(
    availability.available
      ? SPEECH_STATES.READY
      : SPEECH_STATES.ENGINE_UNAVAILABLE,
    availability.reason
  );
}

async function applyVoiceSelection() {
  setSpeechState(SPEECH_STATES.INITIALIZING, "Updating voice selection");
  const availability = await reconcileVoiceSelection(formToSettings(), {
    configure: true,
    persist: true,
  });
  return availability;
}

function base64ToBlob(base64, contentType) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: contentType });
}

function stopLocalPlayback() {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio.currentTime = 0;
    currentAudio = null;
  }
  if (currentAudioUrl) {
    URL.revokeObjectURL(currentAudioUrl);
    currentAudioUrl = null;
  }
}

async function stopPlayback() {
  speechGeneration.cancel();
  stopLocalPlayback();
  if (!startupReady) {
    readyOrUnavailable();
    return;
  }

  setSpeechState(SPEECH_STATES.STOPPING);
  if (speechTransaction) speechTransaction.terminal_event = "stop_requested";
  setPlaybackDiagnostics("stop_requested");
  try {
    await speechRequests.stop(() => request("stop"));
    readyOrUnavailable();
  } catch (error) {
    setSpeechState(
      SPEECH_STATES.ERROR,
      `Stop failed: ${error.message || error}`
    );
    throw error;
  }
}

async function playBase64Wav(base64, generation) {
  if (!speechGeneration.matches(generation)) return;
  stopLocalPlayback();
  let url = null;
  let audio = null;
  try {
    const blob = base64ToBlob(base64, "audio/wav");
    url = URL.createObjectURL(blob);
    audio = new Audio(url);
    currentAudioUrl = url;
    currentAudio = audio;
    audio.addEventListener(
      "ended",
      () => {
        if (!speechGeneration.matches(generation)) return;
        if (currentAudioUrl === url) URL.revokeObjectURL(url);
        currentAudio = null;
        currentAudioUrl = null;
        speechGeneration.fail(generation);
        if (speechTransaction)
          speechTransaction.playback_ended_at = new Date().toISOString();
        setPlaybackDiagnostics("playback_ended", { generation });
        readyOrUnavailable();
      },
      { once: true }
    );
    await playAudioElement(audio, {
      onStarted: () => {
        if (!speechGeneration.matches(generation)) return;
        if (speechTransaction)
          speechTransaction.playback_started_at = new Date().toISOString();
        setPlaybackDiagnostics("playback_started", { generation });
        setSpeechState(SPEECH_STATES.SPEAKING);
      },
      onFailed: (error) => {
        if (speechGeneration.matches(generation)) {
          if (speechTransaction)
            speechTransaction.playback_failed_at = new Date().toISOString();
          setPlaybackDiagnostics("playback_failed", {
            generation,
            message: error?.message || String(error),
          });
        }
      },
    });
  } catch (error) {
    if (!speechGeneration.matches(generation)) return;
    if (url) URL.revokeObjectURL(url);
    if (currentAudio === audio) currentAudio = null;
    currentAudioUrl = null;
    speechGeneration.fail(generation);
    if (speechTransaction)
      speechTransaction.playback_failed_at = new Date().toISOString();
    console.error("Audio playback failed", error);
    setSpeechState(
      SPEECH_STATES.ERROR,
      `Audio playback failed: ${error?.message || error}`
    );
  }
}

async function playPlaybackTestTone() {
  const url = URL.createObjectURL(createPlaybackTestWav());
  let timeout;
  try {
    const audio = new Audio(url);
    const ended = new Promise((resolve, reject) => {
      timeout = setTimeout(
        () => reject(new Error("Playback test timed out.")),
        5000
      );
      audio.addEventListener("ended", resolve, { once: true });
      audio.addEventListener(
        "error",
        () => reject(new Error("WebView audio playback failed.")),
        { once: true }
      );
    });
    await playAudioElement(audio, {
      onStarted: () =>
        setStatus("Playback tone started. Listen for a short beep."),
    });
    await ended;
    setStatus("Playback tone ended. Confirm audible output manually.", "ok");
  } finally {
    clearTimeout(timeout);
    URL.revokeObjectURL(url);
  }
}

async function speakText(text) {
  if (!startupReady) {
    setSpeechState(
      SPEECH_STATES.BACKEND_UNAVAILABLE,
      "The service is not ready."
    );
    return;
  }
  if (!synthesisAvailable) {
    setSpeechState(
      SPEECH_STATES.ENGINE_UNAVAILABLE,
      "Select an available engine, target, and voice."
    );
    return;
  }
  const clean = String(text || "").trim();
  if (!clean) {
    setSpeechState(SPEECH_STATES.ERROR, "No text to read");
    return;
  }
  const max = Number(settings.maxChars || DEFAULTS.maxChars);
  if (clean.length > max) {
    setSpeechState(
      SPEECH_STATES.ERROR,
      `Selection is ${clean.length} characters; limit is ${max}`
    );
    return;
  }

  stopLocalPlayback();
  const sequence = speechGeneration.begin();
  speechTransaction = createSpeechTransaction(sequence, settings);
  setSpeechState(SPEECH_STATES.STOPPING);
  setPlaybackDiagnostics("stop_requested");
  try {
    const result = await speechRequests.replace(
      () => request("stop"),
      () => {
        if (!speechGeneration.isCurrent(sequence)) return null;
        setSpeechState(SPEECH_STATES.SYNTHESIZING);
        return request("speak", { text: clean });
      }
    );
    if (!result || !speechGeneration.isCurrent(sequence)) return;
    speechTransaction.sidecar_generation = result.generation;
    speechTransaction.accepted_at = new Date().toISOString();
    const events = speechGeneration.acknowledge(sequence, result.generation);
    for (const event of events) handleSpeechEvent(event);
  } catch (error) {
    if (!speechGeneration.isCurrent(sequence)) return;
    speechGeneration.cancel();
    throw error;
  }
}

async function readSelection() {
  if (!startupReady) {
    setSpeechState(
      SPEECH_STATES.BACKEND_UNAVAILABLE,
      "The service is not ready."
    );
    return;
  }
  if (!synthesisAvailable) {
    setSpeechState(
      SPEECH_STATES.ENGINE_UNAVAILABLE,
      "Select an available engine, target, and voice."
    );
    return;
  }
  try {
    setSpeechState(SPEECH_STATES.CAPTURING_SELECTION);
    const text = await invoke("capture_selection", {
      delayMs: Number(settings.copyDelay || DEFAULTS.copyDelay),
    });
    if (!text) {
      setSpeechState(
        SPEECH_STATES.ERROR,
        "No selectable text captured. Try Read clipboard."
      );
      return;
    }
    await speakText(text);
  } catch (error) {
    setSpeechState(
      SPEECH_STATES.ERROR,
      `Selection capture failed: ${error.message || error}`
    );
  }
}

async function readClipboard() {
  if (!startupReady) {
    setSpeechState(
      SPEECH_STATES.BACKEND_UNAVAILABLE,
      "The service is not ready."
    );
    return;
  }
  let text;
  try {
    text = await invoke("read_clipboard");
  } catch (error) {
    setSpeechState(
      SPEECH_STATES.ERROR,
      `Could not read plain-text clipboard: ${error.message || error}`
    );
    return;
  }
  if (!text.trim()) {
    setSpeechState(SPEECH_STATES.ERROR, "Clipboard contains no readable text.");
    return;
  }
  await speakText(text);
}

async function configureShortcuts() {
  await invoke("configure_shortcuts", {
    readHotkey: settings.readHotkey || "",
    stopHotkey: settings.stopHotkey || "",
    copyDelay: Number(settings.copyDelay || DEFAULTS.copyDelay),
  });
}

async function applyAutostart() {
  const enabled = Boolean(settings.autostart);
  const currently = await invoke("autostart_status");
  if (enabled !== currently) await invoke("set_autostart", { enabled });
}

async function configureOptionalDesktopFeatures() {
  await initializeOptionalFeatures({
    configureShortcuts,
    applyAutostart,
    onWarning: showNonFatalFeatureWarning,
    onAvailable: clearNonFatalFeatureWarning,
  });
}

async function saveSettings() {
  settings = { ...settings, ...formToSettings() };
  const availability = await applyVoiceSelection();
  await configureOptionalDesktopFeatures();
  setSpeechState(
    availability.available
      ? SPEECH_STATES.READY
      : SPEECH_STATES.ENGINE_UNAVAILABLE,
    availability.available ? "Settings saved" : availability.reason
  );
}

async function refreshCatalogs() {
  settings = { ...settings, offline: Boolean($("offline").checked) };
  await loadVoiceCatalogs({ offline: settings.offline, refresh: true });
}

let voiceSelectionUpdate = Promise.resolve();

function scheduleVoiceSelectionUpdate() {
  voiceSelectionUpdate = voiceSelectionUpdate
    .catch(() => {})
    .then(() => applyVoiceSelection());
  return voiceSelectionUpdate;
}

async function init() {
  settingsToForm(settings);
  setSpeechState(SPEECH_STATES.INITIALIZING);
  $("save").addEventListener("click", () =>
    runUiAction("Could not apply settings", saveSettings)
  );
  $("stop").addEventListener("click", () =>
    runUiAction("Stop failed", stopPlayback)
  );
  $("speakManual").addEventListener("click", () =>
    runUiAction("Speech failed", () => speakText($("manualText").value))
  );
  $("playbackTest").addEventListener("click", () => {
    void playPlaybackTestTone().catch((error) => {
      console.error("Playback test failed", error);
      setStatus(`Playback test failed: ${error?.message || error}`, "error");
    });
  });
  $("testVoice").addEventListener("click", () =>
    runUiAction("Test voice failed", async () => {
      const availability = await applyVoiceSelection();
      if (availability.available) {
        await speakText("VoxSelect is ready to read selected text locally.");
      }
    })
  );
  $("discover").addEventListener("click", () =>
    runUiAction("Catalog refresh failed", refreshCatalogs)
  );
  $("refreshDiagnostics").addEventListener("click", () =>
    runUiAction("Diagnostics unavailable", refreshDiagnostics)
  );
  for (const id of ["language", "engine", "target", "voice"]) {
    $(id).addEventListener("change", () =>
      runUiAction("Voice selection failed", scheduleVoiceSelectionUpdate)
    );
  }

  await listen("readio-event", (event) => handleSidecarEvent(event.payload));
  await listen("backend-error", (event) => {
    startupReady = false;
    if (!shuttingDown) {
      setSpeechState(
        SPEECH_STATES.BACKEND_UNAVAILABLE,
        String(event.payload || "Backend failed")
      );
    }
  });
  await listen("selection-captured", (event) =>
    runUiAction("Selection speech failed", () => speakText(event.payload))
  );
  await listen("selection-capture-empty", () =>
    setSpeechState(
      SPEECH_STATES.ERROR,
      "No selectable text captured. Try Read clipboard."
    )
  );
  await listen("shortcut-stop", () => runUiAction("Stop failed", stopPlayback));
  await listen("tray-open", () => window.focus());
  await listen("tray-read-selection", () =>
    runUiAction("Selection speech failed", readSelection)
  );
  await listen("tray-read-clipboard", () =>
    runUiAction("Clipboard speech failed", readClipboard)
  );
  await listen("tray-stop", () => runUiAction("Stop failed", stopPlayback));

  try {
    const autostart = await invoke("autostart_status");
    settings.autostart = autostart;
    $("autostart").checked = autostart;
  } catch (error) {
    showNonFatalFeatureWarning("autostart", error);
  }

  try {
    await startSidecar();
  } catch (error) {
    startupReady = false;
    setSpeechState(
      SPEECH_STATES.BACKEND_UNAVAILABLE,
      error.message || String(error)
    );
    return;
  }

  await loadVoiceCatalogs({ offline: catalogOfflineSetting(settings) });
  await configureOptionalDesktopFeatures();
}

window.addEventListener("beforeunload", () => {
  shuttingDown = true;
  speechGeneration.cancel();
  invoke("backend_shutdown").catch((error) =>
    console.error("[backend] shutdown request failed", error)
  );
});

void runUiAction("Application initialization failed", init);
