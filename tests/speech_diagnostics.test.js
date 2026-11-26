import assert from "node:assert/strict";
import test from "node:test";
import {
  createSpeechTransaction,
  playbackDiagnostic,
} from "../src/speech_diagnostics.js";

test("speech diagnostics expose transaction metadata without spoken text", () => {
  const transaction = createSpeechTransaction(7, {
    engine: "kitten",
    target: "kitten-v1",
    voice: "voice-a",
    language: "en-US",
    offline: false,
  });
  transaction.sidecar_generation = 12;
  transaction.accepted_at = "2026-11-25T12:00:00.000Z";

  const diagnostic = playbackDiagnostic("audio_received", transaction, {
    wav_bytes: 128,
    frames: 24000,
    duration_seconds: 1,
    peak: 0.4,
  });

  assert.equal(diagnostic.frontend_sequence, 7);
  assert.equal(diagnostic.sidecar_generation, 12);
  assert.equal(diagnostic.engine, "kitten");
  assert.equal(diagnostic.target, "kitten-v1");
  assert.equal(diagnostic.voice, "voice-a");
  assert.equal(diagnostic.language, "en-US");
  assert.equal(diagnostic.offline, false);
  assert.equal(diagnostic.accepted_at, "2026-11-25T12:00:00.000Z");
  assert.equal(diagnostic.wav_bytes, 128);
  assert.equal(diagnostic.frames, 24000);
  assert.equal(diagnostic.duration_seconds, 1);
  assert.equal(diagnostic.peak, 0.4);
  assert.equal("text" in diagnostic, false);
});
