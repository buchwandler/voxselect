import assert from "node:assert/strict";
import test from "node:test";
import { SpeechRequestQueue } from "../src/speech_request_queue.js";

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

test("replacement does not speak until remote stop is acknowledged", async () => {
  const queue = new SpeechRequestQueue();
  const stopAcknowledged = deferred();
  const stopStarted = deferred();
  const calls = [];
  const replacement = queue.replace(
    () => {
      calls.push("stop");
      stopStarted.resolve();
      return stopAcknowledged.promise;
    },
    () => {
      calls.push("speak");
      return "accepted";
    }
  );

  await stopStarted.promise;
  assert.deepEqual(calls, ["stop"]);
  stopAcknowledged.resolve();
  assert.equal(await replacement, "accepted");
  assert.deepEqual(calls, ["stop", "speak"]);
});

test("stop and replacement requests run in invocation order", async () => {
  const queue = new SpeechRequestQueue();
  const firstStopAcknowledged = deferred();
  const stopStarted = deferred();
  const calls = [];
  const first = queue.replace(
    () => {
      calls.push("replace-stop");
      stopStarted.resolve();
      return firstStopAcknowledged.promise;
    },
    () => {
      calls.push("speak");
      return "accepted";
    }
  );
  const stop = queue.stop(() => calls.push("stop"));

  await stopStarted.promise;
  assert.deepEqual(calls, ["replace-stop"]);
  firstStopAcknowledged.resolve();
  await Promise.all([first, stop]);
  assert.deepEqual(calls, ["replace-stop", "speak", "stop"]);
});
