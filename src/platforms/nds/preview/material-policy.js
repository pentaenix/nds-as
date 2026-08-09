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
      mat.opacity = Math.max(0, Math.min(1, nitroAlpha));
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
      // GLTFLoader initializes opacity from baseColorFactor, but the policy
      // reset above used to discard it.  Nitro water, cloud reflections and
      // translucent object effects rely on that uniform material alpha in
      // addition to any per-texel PNG alpha.
      mat.opacity = Math.max(0, Math.min(1, nitroAlpha));
      break;
    default:
      break;
  }

  mat.needsUpdate = true;
}

export function applyMaterialPolicy(mat) {
  if (!mat) return;

  if (mat.map) {
    // GLTFLoader already translated Nitro clamp/repeat/mirrored-repeat samplers.
    // Keep them intact: shoreline and waterfall motion depends on that choice.
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
      const blend = resolvePreviewBlendMode(mat);
      const materialName = String(mat.userData?.gltfMaterial?.name || mat.name || '').toLowerCase();
      // Preserve the terrain display-list stack for the three overlapping
      // Gen 5 beach layers. GLTFLoader may otherwise sort the opaque cutout by
      // material id and let the underlay cover the wet sand, while the blended
      // crest competes with the large translucent ocean plane.
      const shorelineOrder = {
        sea_zanami2: 20,
        sea_simi_1: 21,
        sea_zanami: 22,
      }[materialName];
      const order = shorelineOrder
        ?? ((blend.mode === 'blend' || blend.mode === 'shadow') ? 2 : 1);
      obj.renderOrder = Math.max(Number(obj.renderOrder) || 0, order);
    }
  });
}

/** No-op on NDS island — eye sheets are 3DS-only. */
export function applyEyeExpressionFrame() {
  return false;
}

export function installGfEyeSheetTransform() {}

const MAP_MOTION_FPS = 30;

function inheritPatternSampling(texture, reference) {
  if (!texture) return;
  texture.flipY = false;
  if (reference) {
    texture.wrapS = reference.wrapS;
    texture.wrapT = reference.wrapT;
    if (texture.repeat && reference.repeat) texture.repeat.copy(reference.repeat);
    if (texture.center && reference.center) texture.center.copy(reference.center);
    texture.rotation = reference.rotation;
    texture.matrixAutoUpdate = reference.matrixAutoUpdate;
  }
  texture.magFilter = THREE.NearestFilter;
  texture.minFilter = THREE.NearestFilter;
  texture.generateMipmaps = false;
  if ('colorSpace' in texture) texture.colorSpace = THREE.SRGBColorSpace;
  texture.needsUpdate = true;
}

function applyMapMotionFrame(mat, track, frameIndex) {
  if (!mat || !track) return false;
  const rawFrame = Math.max(0, Number(frameIndex) || 0);
  const patternCount = Math.max(1, Number(track.frameCount) || 1);
  const patternFrame = rawFrame % patternCount;
  if (track.imageKeyframes?.length) {
    let selected = track.imageKeyframes[0];
    for (const keyframe of track.imageKeyframes) {
      if (Number(keyframe.frame) <= patternFrame) selected = keyframe;
      else break;
    }
    if (selected?._texture) {
      if (!mat.userData.raeMapMotionPatternMaps) {
        mat.userData.raeMapMotionPatternMaps = new Map();
      }
      let animatedTexture = mat.userData.raeMapMotionPatternMaps.get(selected._texture);
      if (!animatedTexture) {
        animatedTexture = selected._texture.clone();
        inheritPatternSampling(
          animatedTexture,
          mat.userData?.raeMapMotionOriginalMap || mat.map,
        );
        mat.userData.raeMapMotionPatternMaps.set(selected._texture, animatedTexture);
      }
      if (mat.map !== animatedTexture) mat.map = animatedTexture;
    }
  }
  if (!mat.map) return false;
  if (track.frameOffsets?.length) {
    const offsetFrame = Math.min(track.frameOffsets.length - 1, rawFrame % track.frameOffsets.length);
    const offset = track.frameOffsets[offsetFrame] || [0, 0];
    mat.map.offset.set(Number(offset[0]) || 0, Number(offset[1]) || 0);
  }
  mat.map.needsUpdate = true;
  mat.needsUpdate = true;
  return true;
}

function preparePatternTextures(description) {
  const loader = new THREE.TextureLoader();
  for (const clip of description?.clips || []) {
    for (const track of clip?.tracks || []) {
      for (const keyframe of track?.imageKeyframes || []) {
        if (!keyframe?.image || keyframe._texture) continue;
        loader.load(keyframe.image, (texture) => {
          inheritPatternSampling(texture, null);
          keyframe._texture = texture;
        });
      }
    }
  }
}

export function installMapMaterialMotion(root, description) {
  if (!root || !description?.clips?.length) return 0;
  preparePatternTextures(description);
  root.userData.raeMapMaterialMotion = {
    clips: description.clips,
    frameRate: Number(description.frameRate) || MAP_MOTION_FPS,
    defaultClip: description.defaultClip || description.clips[0]?.id || null,
    activeClipId: null,
    playing: false,
    elapsedMs: 0,
    lastTick: 0,
  };
  return description.clips.length;
}

function findMapMotionClip(state, clipId) {
  if (!state?.clips?.length) return null;
  const wanted = clipId ?? state.defaultClip;
  return state.clips.find((clip) => clip.id === wanted) || state.clips[0];
}

function resetMapMotionOffset(mat) {
  if (!mat) return;
  const animatedMap = mat.userData?.raeMapMotionAnimatedMap;
  const patternMaps = mat.userData?.raeMapMotionPatternMaps;
  if (mat.userData?.raeMapMotionOriginalMap) {
    mat.map = mat.userData.raeMapMotionOriginalMap;
    delete mat.userData.raeMapMotionOriginalMap;
  }
  if (animatedMap?.dispose) animatedMap.dispose();
  if (patternMaps instanceof Map) {
    for (const texture of patternMaps.values()) {
      if (texture?.dispose) texture.dispose();
    }
  }
  delete mat.userData.raeMapMotionAnimatedMap;
  delete mat.userData.raeMapMotionPatternMaps;
  if (!mat.map) return;
  mat.map.needsUpdate = true;
  mat.needsUpdate = true;
}

function applyMapMotionClip(root, clip) {
  if (!root || !clip) return 0;
  const tracks = new Map((clip.tracks || []).map((track) => [String(track.material).toLowerCase(), track]));
  let count = 0;
  root.traverse((obj) => {
    if (!obj.isMesh || !obj.material) return;
    const materials = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const mat of materials) {
      const name = String(mat.userData?.gltfMaterial?.name || mat.name || '').toLowerCase();
      const track = tracks.get(name);
      if (!track) {
        if (mat.userData?.raeMapMotionTrack) {
          delete mat.userData.raeMapMotionTrack;
          resetMapMotionOffset(mat);
        }
        continue;
      }
      if (!mat.userData.raeMapMotionOriginalMap && mat.map) {
        mat.userData.raeMapMotionOriginalMap = mat.map;
        // GLTFLoader may share one THREE.Texture between several materials.
        // Texture offsets are mutable, so animating that shared object can
        // make adjacent rock/cliff parts scroll with the water material.
        mat.userData.raeMapMotionAnimatedMap = mat.map.clone();
        mat.map = mat.userData.raeMapMotionAnimatedMap;
        mat.map.needsUpdate = true;
      }
      if (!track.imageKeyframes?.length && mat.userData.raeMapMotionAnimatedMap) {
        mat.map = mat.userData.raeMapMotionAnimatedMap;
      }
      mat.userData.raeMapMotionTrack = track;
      applyMapMotionFrame(mat, track, 0);
      count += 1;
    }
  });
  return count;
}

export function startMapMaterialMotion(root, clipId) {
  const state = root?.userData?.raeMapMaterialMotion;
  if (!state) return false;
  const clip = findMapMotionClip(state, clipId);
  if (!clip?.tracks?.length || applyMapMotionClip(root, clip) <= 0) return false;
  state.activeClipId = clip.id;
  state.playing = true;
  state.elapsedMs = 0;
  state.lastTick = 0;
  return true;
}

export function pauseMapMaterialMotion(root) {
  const state = root?.userData?.raeMapMaterialMotion;
  if (!state) return;
  state.playing = false;
  state.lastTick = 0;
}

export function stopMapMaterialMotion(root) {
  const state = root?.userData?.raeMapMaterialMotion;
  if (!state) return;
  state.playing = false;
  state.lastTick = 0;
  state.elapsedMs = 0;
  state.activeClipId = null;
  root.traverse((obj) => {
    if (!obj.isMesh || !obj.material) return;
    const materials = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const mat of materials) {
      if (!mat.userData?.raeMapMotionTrack) continue;
      delete mat.userData.raeMapMotionTrack;
      resetMapMotionOffset(mat);
    }
  });
}

export function mapMaterialMotionClipIds(root) {
  const clips = root?.userData?.raeMapMaterialMotion?.clips || [];
  return clips.filter((clip) => clip?.tracks?.length).map((clip) => String(clip.id));
}

export function advanceMapMaterialMotion(root, now) {
  const state = root?.userData?.raeMapMaterialMotion;
  if (!state?.playing) return false;
  const elapsed = state.lastTick ? now - state.lastTick : 0;
  state.lastTick = now;
  state.elapsedMs += elapsed;
  const clip = findMapMotionClip(state, state.activeClipId);
  if (!clip) return false;
  const defaultFrameRate = Number(state.frameRate) || MAP_MOTION_FPS;
  const clipFrame = Math.floor((state.elapsedMs / 1000) * defaultFrameRate);
  root.traverse((obj) => {
    if (!obj.isMesh || !obj.material) return;
    const materials = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const mat of materials) {
      const track = mat.userData?.raeMapMotionTrack;
      const count = Math.max(
        track?.frameOffsets?.length || 0,
        Number(track?.frameCount) || 0,
        track?.imageKeyframes?.length ? 1 : 0,
      );
      if (!count) continue;
      const trackFrame = Math.floor(
        (state.elapsedMs / 1000) * (Number(track?.frameRate) || defaultFrameRate),
      );
      applyMapMotionFrame(mat, track, clip.loop === false ? Math.min(trackFrame, count - 1) : trackFrame);
    }
  });
  if (clip.loop === false && clipFrame >= Math.max(1, Number(clip.frameCount) || 1) - 1) {
    state.playing = false;
    return false;
  }
  return true;
}
