/** Honor extras.rae.renderClass, glTF alpha modes, and texture alpha in the WebEngine viewer. */
import * as THREE from 'three';
import { textureMapHasCutoutAlpha, textureMapHasPartialAlpha } from './texture-alpha.js';

export function renderClassForMaterial(mat, gltfMat) {
  const extras = mat?.userData?.gltfExtensions?.extras || gltfMat?.extras || mat?.extras || {};
  const rae = extras.rae || {};
  return rae.renderClass || null;
}

function nitroAlphaFromSources(mat, gltfMat, pol) {
  if (pol?.nitroAlpha != null && Number.isFinite(pol.nitroAlpha)) {
    return Math.max(0, Math.min(1, Number(pol.nitroAlpha)));
  }
  const extras = mat?.userData?.gltfExtensions?.extras || gltfMat?.extras || {};
  const nitro = extras.rae?.nitro || {};
  if (nitro.alpha != null) {
    return Math.max(0, Math.min(1, Number(nitro.alpha)));
  }
  const src = gltfMat || mat?.userData?.gltfMaterial || {};
  const factor = src.pbrMetallicRoughness?.baseColorFactor;
  if (Array.isArray(factor) && factor.length >= 4) {
    return Math.max(0, Math.min(1, Number(factor[3])));
  }
  return Math.max(0, Math.min(1, Number(mat?.opacity ?? 1)));
}

export function captureMaterialPolicy(mat, gltfMat) {
  if (!mat) return;
  if (gltfMat) {
    mat.userData.gltfMaterial = gltfMat;
  }
  const renderClass = renderClassForMaterial(mat, gltfMat);
  mat.userData.raePolicy = {
    renderClass,
    alphaMode: String(gltfMat?.alphaMode || 'OPAQUE').toUpperCase(),
    alphaCutoff: Number(gltfMat?.alphaCutoff ?? 0.5),
    doubleSided: !!(gltfMat?.doubleSided || mat.doubleSided),
    nitroAlpha: nitroAlphaFromSources(mat, gltfMat, null),
  };
}

function resolvePreviewBlendMode(mat) {
  const pol = mat.userData.raePolicy || {};
  const src = mat.userData.gltfMaterial || {};
  const renderClass = pol.renderClass || renderClassForMaterial(mat);
  const alphaMode = String(pol.alphaMode || src.alphaMode || 'OPAQUE').toUpperCase();
  const alphaCutoff = Number(pol.alphaCutoff ?? src.alphaCutoff ?? 0.5);
  const nitroAlpha = nitroAlphaFromSources(mat, src, pol);
  const hasCutout = textureMapHasCutoutAlpha(mat.map);
  const hasPartial = textureMapHasPartialAlpha(mat.map);
  const shadowDecal = renderClass === 'uniform_decal';

  if (alphaMode === 'MASK' || renderClass === 'mask') {
    if (hasPartial) {
      return { mode: 'blend', alphaCutoff, nitroAlpha };
    }
    return { mode: 'cutout', alphaCutoff, nitroAlpha };
  }

  if (alphaMode === 'BLEND' || renderClass === 'blend') {
    if (hasCutout && !hasPartial) {
      return { mode: 'cutout', alphaCutoff, nitroAlpha };
    }
    if (hasPartial) {
      return { mode: 'blend', alphaCutoff, nitroAlpha };
    }
    if (shadowDecal) {
      return { mode: 'shadow', alphaCutoff, nitroAlpha };
    }
    if (nitroAlpha < 0.999) {
      return { mode: 'blend', alphaCutoff, nitroAlpha };
    }
    return { mode: 'opaque', alphaCutoff, nitroAlpha };
  }

  if (shadowDecal) {
    return { mode: 'shadow', alphaCutoff, nitroAlpha };
  }
  if (hasCutout) {
    return { mode: 'cutout', alphaCutoff, nitroAlpha };
  }
  return { mode: 'opaque', alphaCutoff, nitroAlpha };
}

function applyPreviewBlendMode(mat, blend) {
  const pol = mat.userData.raePolicy || {};
  const src = mat.userData.gltfMaterial || {};
  const doubleSided = !!(pol.doubleSided || src.doubleSided || mat.doubleSided);
  const alphaCutoff = Number(blend.alphaCutoff ?? 0.5);
  const nitroAlpha = Number(blend.nitroAlpha ?? 1);

  mat.alphaTest = 0;
  mat.transparent = false;
  mat.depthWrite = true;
  mat.depthTest = true;
  mat.opacity = 1;
  mat.side = doubleSided ? THREE.DoubleSide : THREE.FrontSide;

  switch (blend.mode) {
    case 'shadow':
      mat.transparent = true;
      mat.depthWrite = false;
      mat.side = THREE.DoubleSide;
      mat.opacity = 1;
      break;
    case 'cutout':
      mat.transparent = false;
      mat.alphaTest = Math.max(0.01, Math.min(1, alphaCutoff));
      mat.depthWrite = true;
      mat.side = doubleSided ? THREE.DoubleSide : THREE.FrontSide;
      break;
    case 'blend':
      mat.transparent = true;
      mat.depthWrite = false;
      mat.alphaTest = 0;
      mat.opacity = 1;
      break;
    default:
      break;
  }

  mat.needsUpdate = true;
}

export function applyMaterialPolicy(mat) {
  if (!mat) return;

  if (mat.map) {
    // Keep MIRRORED_REPEAT from the GLB sampler (3DS models mirror the body
    // across U); everything else gets the legacy repeat default.
    if (mat.map.wrapS !== THREE.MirroredRepeatWrapping) mat.map.wrapS = THREE.RepeatWrapping;
    if (mat.map.wrapT !== THREE.MirroredRepeatWrapping) mat.map.wrapT = THREE.RepeatWrapping;
    mat.map.magFilter = THREE.NearestFilter;
    mat.map.minFilter = THREE.NearestFilter;
    mat.map.generateMipmaps = false;
    if ('colorSpace' in mat.map) mat.map.colorSpace = THREE.SRGBColorSpace;
    mat.map.needsUpdate = true;
  }

  applyPreviewBlendMode(mat, resolvePreviewBlendMode(mat));
}

export function scheduleMaterialPolicyWhenMapReady(mat) {
  if (!mat) return;
  const apply = () => applyMaterialPolicy(mat);
  apply();
  const map = mat.map;
  if (!map) return;
  const img = map.image;
  if (img && (img.complete || img.width)) {
    apply();
    return;
  }
  if (img?.addEventListener) {
    img.addEventListener('load', apply, { once: true });
  }
}

export function applyRaeMaterialPolicy(root) {
  root.traverse((obj) => {
    if (!obj.isMesh || !obj.material) return;
    const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const mat of mats) {
      if (!mat) continue;
      if (!mat.userData.raePolicy) {
        captureMaterialPolicy(mat, mat.userData.gltfMaterial || null);
      }
      scheduleMaterialPolicyWhenMapReady(mat);
      const renderClass = mat.userData.raePolicy?.renderClass || renderClassForMaterial(mat);
      const blend = resolvePreviewBlendMode(mat);
      const order = renderClass === 'uniform_decal' ? 0 : blend.mode === 'blend' ? 2 : 1;
      obj.renderOrder = order;
    }
  });
}
