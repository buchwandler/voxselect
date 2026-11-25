import assert from "node:assert/strict";
import test from "node:test";

import {
  JsonlTransport,
  PROTOCOL_VERSION,
  SERVICE_NAME,
  SpeechGeneration,
} from "../src/sidecar_protocol.js";

function readyMessage(overrides = {}) {
  return {
    event: "ready",
    protocol: PROTOCOL_VERSION,
    service: SERVICE_NAME,
    service_version: "0.2.0",
    ...overrides,
  };
}

test("transport accepts a matching ready handshake and routes responses", async () => {
  const events = [];
  const transport = new JsonlTransport({ onEvent: (event) => events.push(event) });
  const ready = transport.waitForReady(1000);
  transport.handleLine(JSON.stringify(readyMessage()));
  assert.equal((await ready).service, SERVICE_NAME);

  const writes = [];
  transport.attach({ write: async (line) => writes.push(JSON.parse(line)) });
  const response = transport.request("ping", {});
  const request = writes[0];
  assert.deepEqual(request, {
    id: "r1",
    protocol: PROTOCOL_VERSION,
    command: "ping",
    params: {},
  });
  transport.handleLine(
    JSON.stringify({ id: request.id, protocol: PROTOCOL_VERSION, ok: true, result: { ok: true } })
  );
  assert.deepEqual(await response, { ok: true });
  transport.handleLine(
    JSON.stringify({ event: "progress", protocol: PROTOCOL_VERSION, generation: 1 })
  );
  assert.equal(events.length, 1);
});

test("transport rejects protocol mismatch and startup timeout", async () => {
  const errors = [];
  const incompatible = new JsonlTransport({ onFailure: (error) => errors.push(error) });
  const ready = incompatible.waitForReady(1000);
  incompatible.handleLine(JSON.stringify(readyMessage({ protocol: 1 })));
  await assert.rejects(ready, /Unsupported sidecar protocol/);
  assert.equal(errors.length, 1);

  const timedOut = new JsonlTransport();
  await assert.rejects(timedOut.waitForReady(1), /was not ready/);
});

test("transport rejects pending requests immediately when the process exits", async () => {
  const transport = new JsonlTransport();
  const writes = [];
  transport.attach({ write: async (line) => writes.push(line) });
  const response = transport.request("ping", {}, 10000);
  assert.equal(writes.length, 1);
  transport.fail(new Error("sidecar exited"));
  await assert.rejects(response, /sidecar exited/);
  assert.equal(transport.pending.size, 0);
});

test("transport rejects request writes and response protocol mismatches", async () => {
  const writeFailure = new JsonlTransport();
  writeFailure.attach({ write: async () => { throw new Error("stdin closed"); } });
  await assert.rejects(writeFailure.request("ping"), /stdin closed/);
  assert.equal(writeFailure.pending.size, 0);

  const mismatch = new JsonlTransport();
  mismatch.attach({ write: async () => {} });
  const response = mismatch.request("ping");
  mismatch.handleLine(JSON.stringify({ id: "r1", protocol: 1, ok: true, result: {} }));
  await assert.rejects(response, /protocol mismatch/);
});

test("speech generation buffers early events and ignores stale or stopped output", () => {
  const state = new SpeechGeneration();
  const firstRequest = state.begin();
  assert.deepEqual(state.observe({ generation: 4, event: "audio" }), []);
  assert.deepEqual(state.observe({ generation: 5, event: "audio" }), []);
  assert.deepEqual(state.acknowledge(firstRequest, 5), [
    { generation: 5, event: "audio" },
  ]);
  assert.deepEqual(state.observe({ generation: 4, event: "audio" }), []);

  const secondRequest = state.begin();
  assert.deepEqual(state.observe({ generation: 5, event: "speech_canceled" }), []);
  assert.deepEqual(state.acknowledge(secondRequest, 6), []);
  state.cancel();
  assert.deepEqual(state.observe({ generation: 6, event: "audio" }), []);
});
