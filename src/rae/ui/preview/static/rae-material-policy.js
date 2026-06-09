/** Honor extras.rae.renderClass from the GLB policy post-processor. */
import * as THREE from 'three';

export function renderClassForMaterial(mat) {
  const extras = mat?.userData?.gltfExtensions?.extras || mat?.extras || {};
  const rae = extras.rae || {};
  return rae.renderClass || null;
}

export function applyRaeMaterialPolicy(root) {
  root.traverse((obj) => {
    if (!obj.isMesh) return;
    const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const mat of mats) {
      if (!mat) continue;
      if (mat.map) {
        mat.map.wrapS = THREE.RepeatWrapping;
        mat.map.wrapT = THREE.RepeatWrapping;
        mat.map.magFilter = THREE.NearestFilter;
        mat.map.minFilter = THREE.NearestFilter;
        mat.map.generateMipmaps = false;
        if ('colorSpace' in mat.map) mat.map.colorSpace = THREE.SRGBColorSpace;
        mat.map.needsUpdate = true;
      }
      const renderClass = renderClassForMaterial(mat);
      mat.depthWrite = true;
      if (renderClass === 'uniform_decal') {
        mat.transparent = true;
        mat.depthWrite = true;
        mat.side = THREE.DoubleSide;
      } else if (renderClass === 'mask') {
        mat.transparent = false;
        mat.alphaTest = mat.alphaTest || 0.5;
        mat.side = THREE.FrontSide;
      } else if (renderClass === 'blend') {
        mat.transparent = true;
        mat.side = mat.doubleSided ? THREE.DoubleSide : THREE.FrontSide;
      } else {
        mat.transparent = false;
        mat.side = mat.doubleSided ? THREE.DoubleSide : THREE.FrontSide;
      }
      const order = renderClass === 'uniform_decal' ? 0 : renderClass === 'blend' ? 2 : 1;
      obj.renderOrder = order;
    }
  });
}
