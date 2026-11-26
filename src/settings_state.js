const STRING_SETTINGS = [
  "engine",
  "target",
  "voice",
  "language",
  "speaker",
  "readHotkey",
  "stopHotkey",
];

export function sanitizeSettings(saved, defaults) {
  if (!saved || typeof saved !== "object" || Array.isArray(saved)) {
    return { ...defaults };
  }
  const settings = { ...defaults, ...saved };
  for (const key of STRING_SETTINGS) {
    if (typeof settings[key] !== "string") settings[key] = defaults[key];
  }
  for (const key of ["offline", "autostart"]) {
    if (typeof settings[key] !== "boolean") settings[key] = defaults[key];
  }
  if (
    !Number.isInteger(settings.copyDelay) ||
    settings.copyDelay < 20 ||
    settings.copyDelay > 3000
  ) {
    settings.copyDelay = defaults.copyDelay;
  }
  if (
    !Number.isInteger(settings.maxChars) ||
    settings.maxChars < 1 ||
    settings.maxChars > 100000
  ) {
    settings.maxChars = defaults.maxChars;
  }
  return settings;
}
