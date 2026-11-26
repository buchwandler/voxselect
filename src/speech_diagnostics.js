export function createSpeechTransaction(sequence, settings) {
  return {
    frontend_sequence: sequence,
    sidecar_generation: null,
    engine: settings.engine || null,
    target: settings.target || null,
    voice: settings.voice || null,
    language: settings.language || null,
    offline: Boolean(settings.offline),
    accepted_at: null,
    audio_received_at: null,
    wav_bytes: null,
    frames: null,
    duration_seconds: null,
    peak: null,
    playback_started_at: null,
    playback_ended_at: null,
    playback_failed_at: null,
    terminal_event: null,
  };
}

export function playbackDiagnostic(event, transaction, details = {}) {
  return { event, ...(transaction || {}), ...details };
}
