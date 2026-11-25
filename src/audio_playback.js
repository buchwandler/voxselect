export async function playAudioElement(audio, { onStarted = () => {}, onFailed = () => {} } = {}) {
  try {
    await audio.play();
    onStarted();
  } catch (error) {
    onFailed(error);
    throw error;
  }
}


export function base64ByteLength(base64) {
  const padding = base64.endsWith("==") ? 2 : base64.endsWith("=") ? 1 : 0;
  return Math.floor((base64.length * 3) / 4) - padding;
}
