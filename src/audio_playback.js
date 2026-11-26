export async function playAudioElement(
  audio,
  { onStarted = () => {}, onFailed = () => {} } = {}
) {
  try {
    await audio.play();
    onStarted();
  } catch (error) {
    onFailed(error);
    throw error;
  }
}

export function createPlaybackTestWav() {
  const sampleRate = 24000;
  const frames = sampleRate / 4;
  const dataBytes = frames * 2;
  const buffer = new ArrayBuffer(44 + dataBytes);
  const view = new DataView(buffer);
  const writeAscii = (offset, value) => {
    for (let index = 0; index < value.length; index += 1) {
      view.setUint8(offset + index, value.charCodeAt(index));
    }
  };

  writeAscii(0, "RIFF");
  view.setUint32(4, 36 + dataBytes, true);
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(36, "data");
  view.setUint32(40, dataBytes, true);

  for (let frame = 0; frame < frames; frame += 1) {
    const sample = Math.sin((2 * Math.PI * 440 * frame) / sampleRate) * 0.2;
    view.setInt16(44 + frame * 2, sample * 32767, true);
  }

  return new Blob([buffer], { type: "audio/wav" });
}

export function base64ByteLength(base64) {
  const padding = base64.endsWith("==") ? 2 : base64.endsWith("=") ? 1 : 0;
  return Math.floor((base64.length * 3) / 4) - padding;
}
