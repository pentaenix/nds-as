# Windows ISO preview policy

`material-policy.js` applies the rendering rules required by decoded Windows
game assets. The shared viewer provides the camera, animation loop, and loading
infrastructure.

Keep format-specific alpha, culling, and material behavior in this directory so
other platform previews remain unaffected.
