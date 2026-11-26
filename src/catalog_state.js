export function runtimeTargets(targets, language = "") {
  return targets.filter(
    (target) =>
      target.runtime_available &&
      !target.reference_only &&
      (!language || target.languages.includes(language))
  );
}

export function catalogOfflineSetting(settings) {
  return Boolean(settings.offline);
}

export function catalogLanguages(targetsByEngine) {
  return [
    ...new Set(
      Object.values(targetsByEngine)
        .flat()
        .filter((target) => target.runtime_available && !target.reference_only)
        .flatMap((target) => target.languages)
    ),
  ].sort((left, right) => left.localeCompare(right));
}

export function chooseLanguage(savedLanguage, languages) {
  if (languages.includes(savedLanguage)) return savedLanguage;
  if (languages.includes("en-US")) return "en-US";
  return languages[0] || "";
}

export function chooseEngine(
  savedEngine,
  engines,
  targetsByEngine,
  language,
  preferredEngine = null
) {
  const runnable = engines.filter(
    (engine) =>
      engine.runnable &&
      runtimeTargets(targetsByEngine[engine.id] || [], language).length > 0
  );
  return (
    runnable.find((engine) => engine.id === savedEngine) ||
    runnable.find((engine) => engine.id === preferredEngine) ||
    runnable[0] ||
    null
  );
}

export function chooseTarget(savedTarget, targets, language) {
  const available = runtimeTargets(targets, language);
  return (
    available.find((target) => target.id === savedTarget) ||
    available[0] ||
    null
  );
}

export function runtimeVoices(voices) {
  return voices.filter((voice) => voice.runtime_available !== false);
}

export function chooseVoice(savedVoice, voices, defaultVoice) {
  const available = runtimeVoices(voices);
  return (
    available.find((voice) => voice.id === savedVoice) ||
    available.find((voice) => voice.id === defaultVoice) ||
    available[0] ||
    null
  );
}

export function synthesisAvailability(capabilities, engine, target, voice) {
  const result = capabilities?.result;
  if (result?.state !== "ready") {
    return {
      available: false,
      reason:
        result?.reason ||
        "Readio reports that speech synthesis is unavailable.",
    };
  }
  if (!engine?.runnable) {
    return {
      available: false,
      reason: "No runnable speech engine is available.",
    };
  }
  if (!target) {
    return {
      available: false,
      reason: "No runtime-available target is available for this language.",
    };
  }
  if (!voice) {
    return {
      available: false,
      reason: "No voice is available for this target.",
    };
  }
  if (voice.runtime_available === false) {
    return {
      available: false,
      reason: "The selected voice is not available at runtime.",
    };
  }
  return { available: true, reason: "" };
}
