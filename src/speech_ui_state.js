export const SPEECH_STATES = Object.freeze({
  INITIALIZING: "initializing",
  BACKEND_UNAVAILABLE: "backend_unavailable",
  ENGINE_UNAVAILABLE: "engine_unavailable",
  READY: "ready",
  CAPTURING_SELECTION: "capturing_selection",
  SYNTHESIZING: "synthesizing",
  PLAYING: "playing",
  SPEAKING: "speaking",
  STOPPING: "stopping",
  ERROR: "error",
});

const STATUS = {
  initializing: ["Starting Readio…", ""],
  backend_unavailable: ["Readio service unavailable", "error"],
  engine_unavailable: ["Speech synthesis unavailable", "error"],
  ready: ["Ready", "ok"],
  capturing_selection: ["Reading selection…", ""],
  synthesizing: ["Synthesizing…", ""],
  playing: ["Starting audio playback…", ""],
  speaking: ["Speaking…", "ok"],
  stopping: ["Stopping…", ""],
  error: ["Operation failed", "error"],
};

export function speechStatus(state, detail = "") {
  const [message, kind] = STATUS[state] || STATUS.error;
  return {
    message: detail ? `${message}: ${detail}` : message,
    kind: state === "ready" ? "ok" : kind,
  };
}

export function canSpeak(state, available) {
  return (
    available &&
    ![
      SPEECH_STATES.INITIALIZING,
      SPEECH_STATES.BACKEND_UNAVAILABLE,
      SPEECH_STATES.ENGINE_UNAVAILABLE,
      SPEECH_STATES.CAPTURING_SELECTION,
      SPEECH_STATES.SYNTHESIZING,
      SPEECH_STATES.PLAYING,
      SPEECH_STATES.SPEAKING,
      SPEECH_STATES.STOPPING,
    ].includes(state)
  );
}
