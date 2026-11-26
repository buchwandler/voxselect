import assert from "node:assert/strict";
import test from "node:test";
import { initializeOptionalFeatures } from "../src/optional_features.js";

const noAvailable = () => {};

test("shortcut failure warns without blocking autostart setup", async () => {
  const failure = new Error("Wayland shortcuts are unavailable");
  const calls = [];
  const warnings = [];
  const available = [];

  await initializeOptionalFeatures({
    configureShortcuts: async () => {
      calls.push("shortcuts");
      throw failure;
    },
    applyAutostart: async () => calls.push("autostart"),
    onWarning: (feature, error) => warnings.push([feature, error]),
    onAvailable: (feature) => available.push(feature),
  });

  assert.deepEqual(calls, ["shortcuts", "autostart"]);
  assert.deepEqual(warnings, [["shortcuts", failure]]);
  assert.deepEqual(available, ["autostart"]);
});

test("autostart failure warns without rejecting optional setup", async () => {
  const failure = new Error("autostart plugin unavailable");
  const calls = [];
  const warnings = [];

  await initializeOptionalFeatures({
    configureShortcuts: async () => calls.push("shortcuts"),
    applyAutostart: async () => {
      calls.push("autostart");
      throw failure;
    },
    onWarning: (feature, error) => warnings.push([feature, error]),
    onAvailable: noAvailable,
  });

  assert.deepEqual(calls, ["shortcuts", "autostart"]);
  assert.deepEqual(warnings, [["autostart", failure]]);
});
