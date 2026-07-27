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
  const extras = mat?.userData?.gltfExtensions?.extras || gltfMat?.extras || {};
  mat.userData.raePolicy = {
    renderClass,
    alphaMode: String(gltfMat?.alphaMode || 'OPAQUE').toUpperCase(),
    alphaCutoff: Number(gltfMat?.alphaCutoff ?? 0.5),
    doubleSided: !!(gltfMat?.doubleSided || mat.doubleSided),
    nitroAlpha: nitroAlphaFromSources(mat, gltfMat, null),
    pica: extras.rae?.pica || null,
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
  const pica = pol.pica || (mat.userData.gltfMaterial?.extras?.rae?.pica || null);
  const matName = String(mat.userData.gltfMaterial?.name || mat.name || '').toLowerCase();
  const vertexAlphaBlend = matName.includes('sea_iro') && !!(
    pica?.alphaBlendEnabled
    && pica.sourceRgbFactor === 'source_alpha'
    && pica.destinationRgbFactor === 'one_minus_source_alpha'
  );

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
    if (vertexAlphaBlend) {
      return { mode: 'blend', alphaCutoff, nitroAlpha };
    }
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

  const pica = pol.pica;
  if (pica && pica.authoritative !== false) {
    mat.depthWrite = !!pica.depthWriteEnabled;
    const matName = String(src?.name || mat.name || '').toLowerCase();
    const skipAlphaTest = matName.includes('sea_iro');
    if (pica.alphaTestEnabled && !skipAlphaTest) {
      const reference = Math.max(0, Math.min(1, Number(pica.alphaTestReference) || 0));
      if (pica.alphaTestFunction === 'greater') {
        mat.alphaTest = Math.max(mat.alphaTest, reference + 1 / 255);
      } else if (pica.alphaTestFunction === 'greater_or_equal') {
        mat.alphaTest = Math.max(mat.alphaTest, reference);
      }
    }
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
  if (mat.userData?.raeOuterWaterBase) {
    // Establish the ocean color/depth before the translucent wave layers.
    // The game normally gets this destination from its battle framebuffer.
    mat.transparent = false;
    mat.depthWrite = true;
    mat.alphaTest = 0;
    mat.opacity = 1;
    mat.blending = THREE.NormalBlending;
    mat.needsUpdate = true;
  }
  if (mat.userData?.raeStandaloneBlackKey) {
    mat.transparent = true;
    mat.depthWrite = false;
    mat.alphaTest = 0.004;
    mat.opacity = 1;
    mat.blending = THREE.NormalBlending;
    mat.needsUpdate = true;
  }
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

function tevVec4(values, fallback = [0, 0, 0, 0]) {
  const v = Array.isArray(values) ? values : fallback;
  return `vec4(${[0, 1, 2, 3].map((i) => Number(v[i] ?? fallback[i]).toFixed(8)).join(',')})`;
}

function tevSource(source, constantName) {
  switch (Number(source) & 15) {
    case 0: return 'primary';
    case 1: return 'primary';
    case 2: return 'vec4(0.0)';
    case 3: return 'tex0';
    case 4: return 'tex1';
    case 5: return 'tex2';
    case 13: return 'combBuffer';
    case 14: return constantName;
    case 15: return 'previous';
    default: return 'vec4(0.0)';
  }
}

function tevColorArg(source, operand, constantName) {
  let expr = tevSource(source, constantName);
  switch ((Number(operand) & 15) & ~1) {
    case 2: expr = `(${expr}).aaaa`; break;
    case 4: expr = `(${expr}).rrrr`; break;
    case 8: expr = `(${expr}).gggg`; break;
    case 12: expr = `(${expr}).bbbb`; break;
    default: break;
  }
  return (Number(operand) & 1) ? `(vec4(1.0)-(${expr}))` : expr;
}

function tevAlphaArg(source, operand, constantName) {
  const base = tevSource(source, constantName);
  let expr;
  switch ((Number(operand) & 7) & ~1) {
    case 2: expr = `(${base}).r`; break;
    case 4: expr = `(${base}).g`; break;
    case 6: expr = `(${base}).b`; break;
    default: expr = `(${base}).a`; break;
  }
  return (Number(operand) & 1) ? `(1.0-(${expr}))` : expr;
}

function tevCombine(mode, args, color) {
  const one = color ? 'vec3(1.0)' : '1.0';
  const zero = color ? 'vec3(0.0)' : '0.0';
  const suffix = color ? '.rgb' : '';
  const a = args.map((arg) => `(${arg})${suffix}`);
  switch (Number(mode) & 15) {
    case 0: return a[0];
    case 1: return `${a[0]}*${a[1]}`;
    case 2: return `min(${a[0]}+${a[1]},${one})`;
    case 3: return `clamp(${a[0]}+${a[1]}-${color ? 'vec3(0.5)' : '0.5'},${zero},${one})`;
    case 4: return `mix(${a[1]},${a[0]},${a[2]})`;
    case 5: return `max(${a[0]}-${a[1]},${zero})`;
    case 6: return color
      ? `vec3(min(dot(${a[0]},${a[1]}),1.0))`
      : `min(dot(vec3(${a[0]}),vec3(${a[1]})),1.0)`;
    case 7: return color
      ? `vec3(min(dot((${args[0]}),(${args[1]})),1.0))`
      : `min(dot(vec4(${a[0]}),vec4(${a[1]})),1.0)`;
    case 8: return `min(${a[0]}*${a[1]}+${a[2]},${one})`;
    case 9: return `min(${a[0]}+${a[1]},${one})*${a[2]}`;
    default: return a[0];
  }
}

function buildPicaTevFunction(tev) {
  const stages = Array.isArray(tev?.stages) ? tev.stages : [];
  const assignments = Array.isArray(tev?.constantAssignments) ? tev.constantAssignments : [];
  const constants = Array.isArray(tev?.constantColors) ? tev.constantColors : [];
  const coordSets = tev?.textureCoordSets || {};
  const coord = (unit) => {
    const selected = Number(coordSets[String(unit)] ?? unit);
    return selected === 0 ? 'vRaeUv0' : selected === 1 ? 'vRaeUv1' : 'vRaeUv2';
  };
  const lines = [
    'vec4 raePicaTev(vec4 primary) {',
    `  vec4 tex0 = texture2D(raeTex0, ${coord(0)} + raeOffset0);`,
    `  vec4 tex1 = texture2D(raeTex1, ${coord(1)} + raeOffset1);`,
    `  vec4 tex2 = texture2D(raeTex2, ${coord(2)} + raeOffset2);`,
    '  vec4 previous = vec4(0.0);',
    `  vec4 combBuffer = ${tevVec4(tev?.bufferColor)};`,
    '  vec4 outputColor = previous;',
  ];
  stages.forEach((stage, index) => {
    const source = Number(stage?.source) >>> 0;
    const operand = Number(stage?.operand) >>> 0;
    const combiner = Number(stage?.combiner) >>> 0;
    const scale = Number(stage?.scale) >>> 0;
    const constant = tevVec4(constants[Number(assignments[index]) || 0], [1, 1, 1, 1]);
    lines.push(`  vec4 constant${index} = ${constant};`);
    const colorArgs = [0, 1, 2].map((arg) => tevColorArg(
      (source >> (arg * 4)) & 15,
      (operand >> (arg * 4)) & 15,
      `constant${index}`,
    ));
    const alphaArgs = [0, 1, 2].map((arg) => tevAlphaArg(
      (source >> (16 + arg * 4)) & 15,
      (operand >> (12 + arg * 4)) & 7,
      `constant${index}`,
    ));
    lines.push(`  outputColor.rgb = ${tevCombine(combiner & 15, colorArgs, true)};`);
    lines.push(`  outputColor.a = ${tevCombine((combiner >> 16) & 15, alphaArgs, false)};`);
    const colorScale = 1 << (scale & 3);
    const alphaScale = 1 << ((scale >> 16) & 3);
    if (colorScale !== 1) lines.push(`  outputColor.rgb = min(outputColor.rgb*${colorScale}.0,vec3(1.0));`);
    if (alphaScale !== 1) lines.push(`  outputColor.a = min(outputColor.a*${alphaScale}.0,1.0);`);
    if (stage?.updateColorBuffer) lines.push('  combBuffer.rgb = previous.rgb;');
    if (stage?.updateAlphaBuffer) lines.push('  combBuffer.a = previous.a;');
    lines.push('  previous = outputColor;');
  });
  if (tev?.outerWaterBase) {
    // The outer battle-beach pass is authored to blend into an ocean color
    // buffer that exists in-game but not in a standalone GLB. Preserve its
    // actual TEV colour (so it matches the inner sea), but make the exported
    // ocean surface itself authoritative instead of accepting zero alpha.
    lines.push('  outputColor.a = 1.0;');
  }
  if (tev?.standaloneBlackKey) {
    lines.push('  outputColor.a *= smoothstep(0.004,0.035,max(outputColor.r,max(outputColor.g,outputColor.b)));');
  }
  lines.push('  outputColor.rgb = pow(clamp(outputColor.rgb,0.0,1.0),vec3(2.2));');
  const effectColorScale = Math.max(0, Math.min(1, Number(tev?.effectColorScale ?? 1)));
  if (effectColorScale < 0.9999) {
    lines.push(`  outputColor.rgb *= ${effectColorScale.toFixed(6)};`);
  }
  lines.push('  return outputColor;', '}');
  return lines.join('\n');
}

async function installPicaTevMaterial(mat, gltfMat, parser) {
  const tev = gltfMat?.extras?.rae?.picaTev;
  if (!tev || !parser || !mat) return false;
  const indices = tev.textureIndices || {};
  const textures = [];
  for (let unit = 0; unit < 3; unit += 1) {
    const index = Number(indices[String(unit)]);
    let texture = unit === 0 ? mat.map : null;
    if (Number.isInteger(index)) {
      try { texture = await parser.getDependency('texture', index); } catch (_err) { texture = null; }
    }
    textures.push(texture || textures[0] || mat.map);
  }
  if (!textures[0]) return false;
  if (tev.outerWaterBase) {
    // The N ocean is only a tint/mask over a renderer-owned sea buffer in the
    // game. A standalone GLB needs an actual colour surface. Use the same ROM
    // daylight gradient as that buffer, with the darker coastal tint needed
    // to meet the inner G sea instead of the former electric-blue fallback.
    const initial = tev.initialOffsets?.['0'] || [0, 0];
    mat.map = textures[0];
    mat.map.colorSpace = THREE.SRGBColorSpace;
    mat.map.offset.set(Number(initial[0]) || 0, Number(initial[1]) || 0);
    mat.map.magFilter = THREE.LinearFilter;
    mat.map.minFilter = THREE.LinearFilter;
    mat.map.generateMipmaps = false;
    mat.map.needsUpdate = true;
    mat.color?.setRGB(1.0, 1.0, 1.0);
    mat.userData.raeOuterWaterBase = true;
    mat.transparent = false;
    mat.depthWrite = true;
    mat.alphaTest = 0;
    mat.opacity = 1;
    mat.blending = THREE.NormalBlending;
    mat.needsUpdate = true;
    return true;
  }
  for (const texture of textures) {
    if (!texture) continue;
    // PICA TEV combines the stored 8-bit channel values directly. Three.js
    // normally decodes glTF color textures from sRGB before a shader samples
    // them, which made the GF combiner receive gamma-decoded values and then
    // darkened them a second time at the final output conversion. Keep these
    // inputs in their encoded channel space; raePicaTev performs the one
    // encoded-to-linear conversion after all six stages have run.
    texture.colorSpace = THREE.NoColorSpace;
    texture.magFilter = THREE.LinearFilter;
    texture.minFilter = THREE.LinearFilter;
    texture.generateMipmaps = false;
    texture.needsUpdate = true;
  }
  const initialOffsets = tev.initialOffsets || {};
  mat.userData.raePicaTev = {
    textures,
    offsets: [0, 1, 2].map((unit) => {
      const value = initialOffsets[String(unit)] || [0, 0];
      return new THREE.Vector2(Number(value[0]) || 0, Number(value[1]) || 0);
    }),
    shader: null,
  };
  const tevFunction = buildPicaTevFunction(tev);
  mat.onBeforeCompile = (shader) => {
    const state = mat.userData.raePicaTev;
    state.shader = shader;
    for (let unit = 0; unit < 3; unit += 1) {
      shader.uniforms[`raeTex${unit}`] = { value: state.textures[unit] };
      shader.uniforms[`raeOffset${unit}`] = { value: state.offsets[unit] };
    }
    shader.vertexShader = shader.vertexShader
      .replace('void main() {', 'varying vec2 vRaeUv0;\nvarying vec2 vRaeUv1;\nvarying vec2 vRaeUv2;\nattribute vec2 uv1;\nattribute vec2 uv2;\nvoid main() {')
      .replace('#include <uv_vertex>', '#include <uv_vertex>\nvRaeUv0 = uv;\nvRaeUv1 = uv1;\nvRaeUv2 = uv2;');
    shader.fragmentShader = shader.fragmentShader
      .replace('void main() {', `uniform sampler2D raeTex0;\nuniform sampler2D raeTex1;\nuniform sampler2D raeTex2;\nuniform vec2 raeOffset0;\nuniform vec2 raeOffset1;\nuniform vec2 raeOffset2;\nvarying vec2 vRaeUv0;\nvarying vec2 vRaeUv1;\nvarying vec2 vRaeUv2;\n${tevFunction}\nvoid main() {`)
      .replace('#include <map_fragment>', 'diffuseColor = raePicaTev(diffuseColor);')
      .replace('#include <opaque_fragment>', 'gl_FragColor = diffuseColor;');
  };
  mat.customProgramCacheKey = () => `rae-pica-tev-${gltfMat?.name || mat.name}`;
  if (tev.standaloneBlackKey) {
    mat.userData.raeStandaloneBlackKey = true;
    mat.transparent = true;
    mat.depthWrite = false;
    mat.alphaTest = 0.004;
    mat.opacity = 1;
    mat.blending = THREE.NormalBlending;
  }
  mat.needsUpdate = true;
  return true;
}

export async function installTextureVariants(mat, gltfMat, parser) {
  await installPicaTevMaterial(mat, gltfMat, parser);
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
      if (obj.geometry?.attributes?.color?.itemSize >= 4) {
        mat.vertexColors = true;
      }
      if (!mat.userData.raePolicy) {
        captureMaterialPolicy(mat, mat.userData.gltfMaterial || null);
      }
      scheduleMaterialPolicyWhenMapReady(mat);
      const renderClass = mat.userData.raePolicy?.renderClass || renderClassForMaterial(mat);
      const role = materialRoleForMaterial(mat, mat.userData.gltfMaterial || null);
      const blend = resolvePreviewBlendMode(mat);
      const matName = String(mat.userData.gltfMaterial?.name || mat.name || '').toLowerCase();
      let order = renderClass === 'uniform_decal' ? 0 : (blend.mode === 'blend' || blend.mode === 'additive') ? 2 : 1;
      if (matName.includes('sea_iro')) order = mat.userData?.raeOuterWaterBase ? 0 : 1;
      if (role === 'eye_sclera') order = 2;
      if (role === 'eye_iris') order = 3;
      const compositionPriority = Number(obj.userData?.rae?.compositionPriority) || 0;
      if (blend.mode === 'blend' || blend.mode === 'additive' || mat.transparent) {
        order += compositionPriority * 10;
      }
      obj.renderOrder = order;
    }
  });
}

const MAP_MOTION_FPS = 30;

function applyMapMotionFrame(mat, track, frameIndex) {
  if (!mat || !track?.frameOffsets?.length) return false;
  const maxFrame = track.frameOffsets.length - 1;
  const frame = Math.max(0, Math.min(maxFrame, Number(frameIndex) || 0));
  const lower = Math.floor(frame);
  const upper = Math.min(maxFrame, lower + 1);
  const amount = frame - lower;
  const a = track.frameOffsets[lower] || [0, 0];
  const b = track.frameOffsets[upper] || a;
  const off = [
    (Number(a[0]) || 0) + ((Number(b[0]) || 0) - (Number(a[0]) || 0)) * amount,
    (Number(a[1]) || 0) + ((Number(b[1]) || 0) - (Number(a[1]) || 0)) * amount,
  ];
  const unit = Math.max(0, Math.min(2, Number(track.textureUnit) || 0));
  const tevState = mat.userData?.raePicaTev;
  if (tevState?.offsets?.[unit]) {
    tevState.offsets[unit].set(Number(off[0]) || 0, Number(off[1]) || 0);
  } else if (unit === 0 && mat.map) {
    mat.map.offset.set(Number(off[0]) || 0, Number(off[1]) || 0);
    mat.map.needsUpdate = true;
  } else {
    return false;
  }
  mat.needsUpdate = true;
  track.activeFrame = frame;
  return true;
}

export function installMapMaterialMotion(root, mapMotion, environmentScene = null) {
  if (!root || !mapMotion?.clips?.length) return 0;
  root.userData.raeMapMaterialMotion = {
    clips: mapMotion.clips,
    frameRate: Number(mapMotion.frameRate) || MAP_MOTION_FPS,
    defaultClip: mapMotion.defaultClip || mapMotion.clips[0]?.id || null,
    overlayClips: Array.isArray(mapMotion.overlayClips) ? mapMotion.overlayClips.slice() : [],
    defaultPoseClips: Array.isArray(mapMotion.defaultPoseClips) ? mapMotion.defaultPoseClips.slice() : [],
    activeClipId: null,
    playing: false,
    elapsedMs: 0,
    lastTick: 0,
    meshVisibility: null,
    meshVisibilityAnimated: false,
    activeClips: [],
    environmentScene,
  };
  const defaultTime = environmentScene?.defaultTime || 'day';
  const timeState = environmentScene?.states?.timeStates?.find((state) => state?.id === defaultTime);
  if (timeState?.poses?.length) {
    root.userData.raeMapMaterialMotion.defaultPoseClips = timeState.poses.slice();
  }
  applyDefaultMapMotionPose(root, root.userData.raeMapMaterialMotion);
  startMapMaterialMotion(root, root.userData.raeMapMaterialMotion.defaultClip);
  return mapMotion.clips.length;
}

function findMapMotionClip(mapMotion, clipId) {
  if (!mapMotion?.clips?.length) return null;
  if (clipId == null) {
    const wanted = mapMotion.defaultClip;
    return mapMotion.clips.find((clip) => clip.id === wanted) || mapMotion.clips[0];
  }
  return mapMotion.clips.find((clip) => clip.id === clipId) || mapMotion.clips[0];
}

function applyDefaultMapMotionPose(root, mapMotion) {
  if (!root || !mapMotion?.defaultPoseClips?.length) return 0;
  let applied = 0;
  for (const pose of mapMotion.defaultPoseClips) {
    const clip = findMapMotionClip(mapMotion, pose?.clip);
    if (!clip) continue;
    const frame = Math.max(0, Number(pose?.frame) || 0);
    const byMaterial = new Map();
    for (const track of clip.tracks || []) {
      const tracks = byMaterial.get(track.material) || [];
      tracks.push(track);
      byMaterial.set(track.material, tracks);
    }
    root.traverse((obj) => {
      if (!obj.isMesh || !obj.material) return;
      const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
      for (const mat of mats) {
        const name = mat.userData?.gltfMaterial?.name || mat.name;
        for (const track of byMaterial.get(name) || []) {
          if (applyMapMotionFrame(mat, track, frame)) applied += 1;
        }
      }
    });
  }
  return applied;
}

function meshVisibilityIsAnimated(visMap) {
  if (!visMap) return false;
  return Object.values(visMap).some((values) => {
    if (!values?.length) return false;
    return new Set(values).size > 1;
  });
}

function resetMapMotionOffset(mat) {
  const tevState = mat?.userData?.raePicaTev;
  if (tevState?.offsets) {
    for (const offset of tevState.offsets) offset.set(0, 0);
  } else if (mat?.map) {
    mat.map.offset.set(0, 0);
    mat.map.needsUpdate = true;
  }
  if (!mat) return;
  mat.needsUpdate = true;
}

function applyMapMotionClipToRoot(root, clip, extraClips = []) {
  if (!root || !clip) return 0;
  const clips = [clip, ...(extraClips || [])].filter(Boolean);
  const byMaterial = new Map();
  for (const activeClip of clips) {
    for (const track of activeClip.tracks || []) {
      const tracks = byMaterial.get(track.material) || [];
      tracks.push({ track, clip: activeClip });
      byMaterial.set(track.material, tracks);
    }
  }
  let count = 0;
  root.traverse((obj) => {
    if (!obj.isMesh || !obj.material) return;
    const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const mat of mats) {
      const gltfMat = mat.userData?.gltfMaterial;
      const name = gltfMat?.name || mat.name;
      const tracks = byMaterial.get(name);
      if (!tracks?.length) {
        if (mat.userData?.raeMapMotionTracks) {
          delete mat.userData.raeMapMotionTracks;
          resetMapMotionOffset(mat);
        }
        continue;
      }
      mat.userData.raeMapMotionTracks = tracks;
      for (const active of tracks) {
        applyMapMotionFrame(mat, active.track, 0);
        count += 1;
      }
    }
  });
  return count;
}

function sampleMapMotionMeshVisibility(root, visMap, frameIndex) {
  if (!root || !visMap) return;
  const frame = Math.max(0, Number(frameIndex) || 0);
  root.traverse((obj) => {
    if (!obj.isMesh || !obj.name) return;
    const values = visMap[obj.name];
    if (!values?.length) return;
    const idx = Math.min(frame, values.length - 1);
    obj.visible = !!values[idx];
  });
}

export function startMapMaterialMotion(root, clipId) {
  const mapMotion = root?.userData?.raeMapMaterialMotion;
  if (!mapMotion) return false;
  const resolvedId = clipId ?? mapMotion.defaultClip;
  const clip = findMapMotionClip(mapMotion, resolvedId);
  if (!clip?.tracks?.length && !clip?.meshVisibility) return false;
  const overlayClips = resolvedId === mapMotion.defaultClip
    ? (mapMotion.overlayClips || [])
        .map((id) => findMapMotionClip(mapMotion, id))
        .filter((overlay) => overlay?.tracks?.length || overlay?.meshVisibility)
    : [];
  applyMapMotionClipToRoot(root, clip, overlayClips);
  mapMotion.activeClipId = clip.id;
  mapMotion.playing = true;
  mapMotion.elapsedMs = 0;
  mapMotion.lastTick = 0;
  mapMotion.meshVisibility = clip.meshVisibility || null;
  mapMotion.activeClips = [clip, ...overlayClips];
  mapMotion.meshVisibilityAnimated = mapMotion.activeClips.some((active) => meshVisibilityIsAnimated(active.meshVisibility));
  if (mapMotion.meshVisibilityAnimated) {
    for (const active of mapMotion.activeClips) {
      if (active.meshVisibility) sampleMapMotionMeshVisibility(root, active.meshVisibility, 0);
    }
  }
  return true;
}

export function pauseMapMaterialMotion(root) {
  const mapMotion = root?.userData?.raeMapMaterialMotion;
  if (!mapMotion) return;
  mapMotion.playing = false;
  mapMotion.lastTick = 0;
}

export function stopMapMaterialMotion(root) {
  const mapMotion = root?.userData?.raeMapMaterialMotion;
  if (!mapMotion) return;
  mapMotion.playing = false;
  mapMotion.lastTick = 0;
  mapMotion.elapsedMs = 0;
  mapMotion.activeClipId = null;
  mapMotion.meshVisibility = null;
  mapMotion.meshVisibilityAnimated = false;
  mapMotion.activeClips = [];
  if (!root) return;
  root.traverse((obj) => {
    if (!obj.isMesh || !obj.material) return;
    const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const mat of mats) {
      if (mat.userData?.raeMapMotionTracks) {
        delete mat.userData.raeMapMotionTracks;
        resetMapMotionOffset(mat);
      }
    }
  });
  applyDefaultMapMotionPose(root, mapMotion);
}

export function mapMaterialMotionClipIds(root) {
  const clips = root?.userData?.raeMapMaterialMotion?.clips;
  if (!clips?.length) return [];
  return clips.filter((clip) => clip?.tracks?.length || clip?.meshVisibility).map((clip) => String(clip.id));
}

export function advanceMapMaterialMotion(root, now) {
  const mapMotion = root?.userData?.raeMapMaterialMotion;
  if (!mapMotion?.playing) return false;
  const dt = mapMotion.lastTick ? now - mapMotion.lastTick : 0;
  mapMotion.lastTick = now;
  mapMotion.elapsedMs += dt;
  const clip = findMapMotionClip(mapMotion, mapMotion.activeClipId);
  if (!clip) return false;
  const frameRate = Number(mapMotion.frameRate) || MAP_MOTION_FPS;
  const clipFrame = (mapMotion.elapsedMs / 1000) * frameRate;
  const clipFrameCount = Math.max(1, Number(clip.frameCount) || 1);
  let clipDone = false;
  if (!clip.loop && clipFrame >= clipFrameCount - 1) {
    clipDone = true;
    mapMotion.playing = false;
  }
  root.traverse((obj) => {
    if (!obj.isMesh || !obj.material) return;
    const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const mat of mats) {
      const tracks = mat.userData?.raeMapMotionTracks || [];
      for (const active of tracks) {
        const track = active.track;
        if (track?.motionKind === 'palette') continue;
        const trackClip = active.clip || clip;
        const frameCount = Math.max(1, track.frameOffsets?.length || Number(trackClip.frameCount) || clipFrameCount);
        let frame = (mapMotion.elapsedMs / 1000) * frameRate;
        if (trackClip.loop) {
          frame %= frameCount;
        } else {
          frame = Math.min(frame, frameCount - 1);
        }
        applyMapMotionFrame(mat, track, frame);
      }
    }
  });
  if (mapMotion.meshVisibilityAnimated) {
    for (const active of mapMotion.activeClips || [clip]) {
      if (!active.meshVisibility) continue;
      const activeCount = Math.max(1, Number(active.frameCount) || 1);
      const raw = Math.floor((mapMotion.elapsedMs / 1000) * frameRate);
      const visFrame = active.loop ? raw % activeCount : Math.min(raw, activeCount - 1);
      sampleMapMotionMeshVisibility(root, active.meshVisibility, visFrame);
    }
  }
  return !clipDone;
}
