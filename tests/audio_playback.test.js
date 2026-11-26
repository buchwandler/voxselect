import assert from "node:assert/strict";
import test from "node:test";
import { createPlaybackTestWav } from "../src/audio_playback.js";

test("playback test tone is a non-silent mono PCM WAV", async () => {
  const wav = createPlaybackTestWav();
  const bytes = new Uint8Array(await wav.arrayBuffer());
  const view = new DataView(bytes.buffer);

  assert.equal(wav.type, "audio/wav");
  assert.equal(new TextDecoder().decode(bytes.subarray(0, 4)), "RIFF");
  assert.equal(new TextDecoder().decode(bytes.subarray(8, 12)), "WAVE");
  assert.equal(view.getUint16(20, true), 1);
  assert.equal(view.getUint16(22, true), 1);
  assert.equal(view.getUint32(24, true), 24000);
  assert.equal(view.getUint32(40, true), 12000);
  assert.ok(bytes.subarray(44).some((byte) => byte !== 0));
});
