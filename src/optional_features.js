export async function initializeOptionalFeatures({
  configureShortcuts,
  applyAutostart,
  onWarning,
  onAvailable,
}) {
  for (const [feature, configure] of [
    ["shortcuts", configureShortcuts],
    ["autostart", applyAutostart],
  ]) {
    try {
      await configure();
      onAvailable(feature);
    } catch (error) {
      onWarning(feature, error);
    }
  }
}
