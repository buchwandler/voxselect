import assert from "node:assert/strict";
import test from "node:test";
import { sanitizeSettings } from "../src/settings_state.js";

import {
  catalogLanguages,
  catalogOfflineSetting,
  runtimeVoices,
  chooseEngine,
  chooseLanguage,
  chooseTarget,
  chooseVoice,
  runtimeTargets,
  synthesisAvailability,
} from "../src/catalog_state.js";
import { base64ByteLength, playAudioElement } from "../src/audio_playback.js";
import {
  canSpeak,
  SPEECH_STATES,
  speechStatus,
} from "../src/speech_ui_state.js";

const engines = [
  { id: "missing", runnable: false },
  { id: "first", runnable: true },
  { id: "saved", runnable: true },
];
const targetsByEngine = {
  first: [
    {
      id: "offline",
      languages: ["en-US"],
      runtime_available: true,
      reference_only: false,
    },
  ],
  saved: [
    {
      id: "unavailable",
      languages: ["en-US"],
      runtime_available: false,
      reference_only: false,
    },
    {
      id: "reference",
      languages: ["en-US"],
      runtime_available: true,
      reference_only: true,
    },
    {
      id: "default",
      languages: ["en-US", "fr-FR"],
      runtime_available: true,
      reference_only: false,
      default_voice: "voice-default",
    },
  ],
};

test("persisted settings are type-checked before catalog reconciliation", () => {
  const defaults = {
    engine: "kitten",
    target: "cpu",
    voice: "voice",
    language: "en-US",
    speaker: "",
    offline: false,
    readHotkey: "read",
    stopHotkey: "stop",
    copyDelay: 140,
    maxChars: 8000,
    autostart: false,
  };
  const settings = sanitizeSettings(
    {
      engine: 7,
      target: "saved-target",
      offline: "false",
      copyDelay: 9000,
      maxChars: 200,
    },
    defaults
  );
  assert.equal(settings.engine, defaults.engine);
  assert.equal(settings.target, "saved-target");
  assert.equal(settings.offline, defaults.offline);
  assert.equal(settings.copyDelay, defaults.copyDelay);
  assert.equal(settings.maxChars, 200);
});

test("catalog choices preserve valid saved IDs and apply ordered fallbacks", () => {
  const languages = catalogLanguages(targetsByEngine);
  assert.deepEqual(languages, ["en-US", "fr-FR"]);
  assert.equal(chooseLanguage("fr-FR", languages), "fr-FR");
  assert.equal(chooseLanguage("invalid", languages), "en-US");
  assert.equal(
    chooseEngine("saved", engines, targetsByEngine, "en-US").id,
    "saved"
  );
  assert.equal(
    chooseEngine("invalid", engines, targetsByEngine, "en-US").id,
    "first"
  );
  assert.deepEqual(
    runtimeTargets(targetsByEngine.saved, "en-US").map(({ id }) => id),
    ["default"]
  );
  assert.equal(
    chooseTarget("default", targetsByEngine.saved, "en-US").id,
    "default"
  );
  assert.equal(
    chooseTarget("invalid", targetsByEngine.saved, "en-US").id,
    "default"
  );
  assert.equal(
    chooseVoice(
      "invalid",
      [{ id: "voice-default" }, { id: "other" }],
      "voice-default"
    ).id,
    "voice-default"
  );
  assert.equal(
    chooseVoice(
      "other",
      [{ id: "voice-default" }, { id: "other" }],
      "voice-default"
    ).id,
    "other"
  );
});

test("unavailable capabilities and missing catalogs never enter ready state", () => {
  assert.deepEqual(
    synthesisAvailability(
      { result: { state: "unavailable", reason: "engine missing" } },
      null,
      null,
      null
    ),
    { available: false, reason: "engine missing" }
  );
  assert.equal(chooseEngine("missing", engines, {}, "en-US"), null);
  assert.equal(canSpeak(SPEECH_STATES.ENGINE_UNAVAILABLE, true), false);
  assert.equal(canSpeak(SPEECH_STATES.READY, false), false);
  assert.deepEqual(
    speechStatus(SPEECH_STATES.ENGINE_UNAVAILABLE, "No local model"),
    {
      message: "Speech synthesis unavailable: No local model",
      kind: "error",
    }
  );
});

test("speech controls are gated until ready and playback failures remain observable", async () => {
  assert.equal(canSpeak(SPEECH_STATES.INITIALIZING, true), false);
  assert.equal(canSpeak(SPEECH_STATES.READY, true), true);
  assert.equal(canSpeak(SPEECH_STATES.ERROR, true), true);

  assert.equal(base64ByteLength("UklG"), 3);
  assert.equal(base64ByteLength("UklGRg=="), 4);
  const failure = new Error("autoplay denied");
  let reported = null;
  await assert.rejects(
    playAudioElement(
      {
        play: async () => {
          throw failure;
        },
      },
      {
        onFailed: (error) => {
          reported = error;
        },
      }
    ),
    /autoplay denied/
  );
  assert.equal(reported, failure);
});

test("catalog language identifiers remain canonical for voice filtering", () => {
  const languages = catalogLanguages({
    kitten: [
      {
        id: "kitten-v1",
        languages: ["en-US"],
        runtime_available: true,
        reference_only: false,
      },
    ],
  });

  assert.deepEqual(languages, ["en-US"]);
  assert.equal(chooseLanguage("English", languages), "en-US");
});

test("startup catalog discovery honors the online/offline setting", () => {
  assert.equal(catalogOfflineSetting({ offline: false }), false);
  assert.equal(catalogOfflineSetting({ offline: true }), true);
});

test("runtime-unavailable saved and default voices cannot be selected", () => {
  const unavailable = [
    { id: "saved", runtime_available: false },
    { id: "default", runtime_available: false },
  ];
  const voices = [...unavailable, { id: "available", runtime_available: true }];

  assert.deepEqual(runtimeVoices(voices), [
    { id: "available", runtime_available: true },
  ]);
  assert.equal(chooseVoice("saved", voices, "default").id, "available");
  assert.equal(chooseVoice("saved", unavailable, "default"), null);
  assert.deepEqual(
    synthesisAvailability(
      { result: { state: "ready" } },
      { runnable: true },
      { id: "target" },
      unavailable[0]
    ),
    {
      available: false,
      reason: "The selected voice is not available at runtime.",
    }
  );
});
