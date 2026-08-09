/** Sample texture alpha on the CPU for preview blend-mode selection. */

function readAlphaSamples(map) {
  const image = map?.image;
  if (!image) return null;
  const width = image.width || image.videoWidth || 0;
  const height = image.height || image.videoHeight || 0;
  if (!width || !height) return null;
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  if (!ctx) return null;
  try {
    ctx.drawImage(image, 0, 0, width, height);
    return ctx.getImageData(0, 0, width, height).data;
  } catch (_err) {
    return null;
  }
}

export function textureMapHasCutoutAlpha(map) {
  const data = readAlphaSamples(map);
  if (!data) return false;
  for (let i = 3; i < data.length; i += 4) {
    if (data[i] < 250) return true;
  }
  return false;
}

export function textureMapHasPartialAlpha(map) {
  const data = readAlphaSamples(map);
  if (!data) return false;
  for (let i = 3; i < data.length; i += 4) {
    const value = data[i];
    if (value > 8 && value < 247) return true;
  }
  return false;
}
