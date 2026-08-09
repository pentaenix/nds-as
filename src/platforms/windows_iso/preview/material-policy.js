/**
 * Windows CD/ISO viewport material policy (platform-owned).
 * Loaded by glb_viewer.html for windows_iso previews only.
 */
export function captureMaterialPolicy(mat, gltfMat) {
  if (!mat || !gltfMat) return;
  mat.userData.gltfMaterial = gltfMat;
}

export function applyMaterialPolicy(mat) {
  if (!mat) return;
  mat.needsUpdate = true;
}

export function scheduleMaterialPolicyWhenMapReady(mat) {
  applyMaterialPolicy(mat);
}

export function applyRaeMaterialPolicy(root) {
  root.traverse((obj) => {
    if (!obj.isMesh || !obj.material) return;
    const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const mat of mats) {
      if (!mat) continue;
      captureMaterialPolicy(mat, mat.userData.gltfMaterial || null);
      scheduleMaterialPolicyWhenMapReady(mat);
    }
  });
}
