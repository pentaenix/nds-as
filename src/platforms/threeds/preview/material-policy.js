/** Honor extras.rae.renderClass, glTF alpha modes, and texture alpha in the WebEngine viewer. */
import * as THREE from 'three';
import { textureMapHasCutoutAlpha, textureMapHasPartialAlpha } from './texture-alpha.js';

export function renderClassForMaterial(mat, gltfMat) {
  const extras = mat?.userData?.gltfExtensions?.extras || gltfMat?.extras || mat?.extras || {};
  const rae = extras.rae || {};
  return rae.renderClass || null;
}

export function materialRoleForMaterial(mat, gltfMat) {
  const extras = mat?.userData?.gltfExtensions?.extras || gltfMat?.extras || mat?.extras || {};
  const rae = extras.rae || {};
  return rae.materialRole || null;
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
  const additive = renderClass === 'additive';
  const role = materialRoleForMaterial(mat);

  if (role === 'eye_iris' && (renderClass === 'mask' || alphaMode === 'MASK')) {
    return { mode: 'cutout', alphaCutoff, nitroAlpha };
  }

  if (additive) {
    return { mode: 'additive', alphaCutoff, nitroAlpha };
  }

  if (renderClass === 'opaque') {
    return { mode: 'opaque', alphaCutoff, nitroAlpha };
  }

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
  const role = materialRoleForMaterial(mat, src);
  const doubleSided = !!(pol.doubleSided || src.doubleSided || mat.doubleSided);
  const alphaCutoff = Number(blend.alphaCutoff ?? 0.5);
  const nitroAlpha = Number(blend.nitroAlpha ?? 1);

  mat.alphaTest = 0;
  mat.transparent = false;
  mat.depthWrite = true;
  mat.depthTest = true;
  mat.opacity = 1;
  mat.blending = THREE.NormalBlending;
  mat.side = doubleSided ? THREE.DoubleSide : THREE.FrontSide;
  mat.polygonOffset = false;
  mat.polygonOffsetFactor = 0;
  mat.polygonOffsetUnits = 0;
  mat.stencilWrite = false;
  mat.stencilRef = 0;
  mat.stencilFunc = THREE.AlwaysStencilFunc;
  mat.stencilFail = THREE.KeepStencilOp;
  mat.stencilZFail = THREE.KeepStencilOp;
  mat.stencilZPass = THREE.KeepStencilOp;

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
      if (role === 'eye_iris') {
        mat.polygonOffset = false;
        mat.depthWrite = false;
      }
      break;
    case 'blend':
      mat.transparent = true;
      mat.depthWrite = role === 'eye_iris';
      mat.alphaTest = 0;
      mat.opacity = 1;
      if (role === 'eye_iris') {
        mat.polygonOffset = false;
        mat.depthWrite = false;
      }
      break;
    case 'additive':
      mat.transparent = true;
      mat.depthWrite = false;
      mat.alphaTest = 0.04;
      mat.opacity = 1;
      mat.blending = THREE.AdditiveBlending;
      break;
    default:
      break;
  }

  mat.needsUpdate = true;
}

export function applyMaterialPolicy(mat) {
  if (!mat) return;

  if (mat.map) {
    if (mat.map.wrapS !== THREE.MirroredRepeatWrapping) mat.map.wrapS = THREE.RepeatWrapping;
    if (mat.map.wrapT !== THREE.MirroredRepeatWrapping) mat.map.wrapT = THREE.RepeatWrapping;
    mat.map.magFilter = THREE.NearestFilter;
    mat.map.minFilter = THREE.NearestFilter;
    mat.map.generateMipmaps = false;
    if ('colorSpace' in mat.map) mat.map.colorSpace = THREE.SRGBColorSpace;
    mat.map.needsUpdate = true;
  }

  applyPreviewBlendMode(mat, resolvePreviewBlendMode(mat));
  installGfEyeSheetTransform(mat, mat.userData.gltfMaterial || null);
}

function gfGlWrap(mode) {
  return Number(mode) === 3 ? THREE.MirroredRepeatWrapping : THREE.RepeatWrapping;
}

function inferEyeSheetCol(tx, bindTx, cols) {
  const n = Number(cols) || 2;
  if (n <= 1) return 0;
  if (Math.abs(bindTx - 1.0) < 0.01) {
    return Math.abs(tx - 1.0) < 0.01 ? 0 : 1;
  }
  if (Math.abs(bindTx - 0.5) < 0.01) {
    return Math.abs(tx - 0.5) < 0.01 ? 0 : 1;
  }
  return Math.max(0, Math.min(n - 1, Math.round((bindTx - tx) / 0.5)));
}

export function applyEyeTextureTransform(mat, scale, gfTranslation, bindTranslation, cols) {
  if (!mat?.map) return;
  const sx = Math.abs(Number(scale?.[0]) || 1);
  const tx = Number(gfTranslation?.[0]) || 0;
  const ty = Number(gfTranslation?.[1]) || 0;
  const bindTx = Number(bindTranslation?.[0]) || 0;
  const bindTy = Number(bindTranslation?.[1]) || 0;
  const sheetCols = Number(cols) || 2;
  const col = inferEyeSheetCol(tx, bindTx, sheetCols);
  const ox = col * sx / sheetCols;
  const oy = ty - bindTy;
  mat.map.repeat.set(sx, 1);
  mat.map.offset.set(ox, oy);
  mat.map.needsUpdate = true;
  mat.needsUpdate = true;
}

export function applyEyeTextureOffsets(mat, du, dv) {
  if (!mat) return;
  const sh = mat.userData?.raeEyeSheet;
  if (sh?.isSheet && sh.scale) {
    applyEyeTextureTransform(
      mat,
      sh.scale,
      [du, dv],
      sh.bindTranslation ?? [0, 0],
      sh.cols,
    );
    return;
  }
  for (const tex of [mat.map]) {
    if (!tex) continue;
    tex.offset.set(du, dv);
    tex.repeat.set(1, 1);
    tex.needsUpdate = true;
  }
  mat.needsUpdate = true;
}

export function applyEyeExpressionFrame(mat, frameIndex) {
  const sh = mat?.userData?.raeEyeSheet;
  if (!sh?.isSheet || !mat?.map) return false;
  const translations = sh.frameTranslations;
  const offsets = sh.frameOffsets;
  const maxFrame = Math.max(
    0,
    (translations?.length ?? offsets?.length ?? 1) - 1,
  );
  const frame = Math.max(0, Math.min(maxFrame, Number(frameIndex) || 0));
  sh.activeFrame = frame;
  const trans = translations?.[frame];
  if (trans) {
    applyEyeTextureTransform(mat, sh.scale, trans, sh.bindTranslation ?? [0, 0], sh.cols);
  } else {
    const off = offsets?.[frame] ?? [0, 0];
    mat.map.repeat.set(Math.abs(Number(sh.scale?.[0]) || 1), 1);
    mat.map.offset.set(Number(off[0]) || 0, Number(off[1]) || 0);
    mat.map.needsUpdate = true;
    mat.needsUpdate = true;
  }
  return true;
}

export function installGfEyeSheetTransform(mat, gltfMat) {
  const sheet = gltfMat?.extras?.rae?.eyeSheet;
  const expr = gltfMat?.extras?.rae?.eyeExpression;
  if (!sheet || !mat?.map) return;
  const scale = sheet.scale || [2, 1];
  const bind = sheet.translation || [1, 0];
  const prev = mat.userData.raeEyeSheet;
  const wrap = sheet.wrap || [2, 2];
  mat.map.wrapS = gfGlWrap(wrap[0]);
  mat.map.wrapT = gfGlWrap(wrap[1]);
  mat.userData.raeEyeSheet = {
    scale: [Number(scale[0]) || 1, Number(scale[1]) || 1],
    bindTranslation: [Number(bind[0]) || 0, Number(bind[1]) || 0],
    cols: Number(sheet?.cols) || 1,
    rows: Number(sheet?.rows) || 1,
    frameTranslations: expr?.frameTranslations || null,
    frameOffsets: expr?.frameOffsets || null,
    activeFrame: prev?.activeFrame ?? Number(expr?.defaultFrame ?? 0),
    isSheet: true,
    rawU: sheet?.uvLayout === 'raw_u',
  };
  applyEyeExpressionFrame(mat, mat.userData.raeEyeSheet.activeFrame);
  mat.userData.setEyeExpressionFrame = (frameIndex) => applyEyeExpressionFrame(mat, frameIndex);
}

export async function installTextureVariants(mat, gltfMat, parser) {
  const shinyIndex = gltfMat?.extras?.rae?.shinyMaterialIndex;
  if (!Number.isInteger(shinyIndex) || !parser || !mat) return;
  const shinyGltf = parser.json?.materials?.[shinyIndex];
  const texInfo = shinyGltf?.pbrMetallicRoughness?.baseColorTexture;
  if (!texInfo || texInfo.index == null) return;
  try {
    const shinyTex = await parser.getDependency('texture', texInfo.index);
    if (!shinyTex) return;
    mat.userData.raeTextureVariants = {
      normal: mat.map || null,
      shiny: shinyTex,
      active: 'normal',
    };
  } catch (_err) {
    // Shiny sibling missing or unloadable — leave normal-only.
  }
}

export function applyTextureVariant(mat, variantId) {
  const variants = mat?.userData?.raeTextureVariants;
  if (!variants) return false;
  const nextId = variantId === 'shiny' ? 'shiny' : 'normal';
  const nextMap = variants[nextId];
  if (!nextMap) return false;
  mat.map = nextMap;
  variants.active = nextId;
  const eyeSheet = mat.userData?.raeEyeSheet;
  if (eyeSheet?.isSheet) {
    applyEyeExpressionFrame(mat, eyeSheet.activeFrame ?? 0);
  }
  applyMaterialPolicy(mat);
  return true;
}

export function applyTextureVariantToRoot(root, variantId) {
  if (!root) return 0;
  let count = 0;
  root.traverse((obj) => {
    if (!obj.isMesh || !obj.material) return;
    const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const mat of mats) {
      if (applyTextureVariant(mat, variantId)) count += 1;
    }
  });
  return count;
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
      const role = materialRoleForMaterial(mat, mat.userData.gltfMaterial || null);
      const blend = resolvePreviewBlendMode(mat);
      let order = renderClass === 'uniform_decal' ? 0 : (blend.mode === 'blend' || blend.mode === 'additive') ? 2 : 1;
      if (role === 'eye_sclera') order = 2;
      if (role === 'eye_iris') order = 3;
      obj.renderOrder = order;
    }
  });
}
