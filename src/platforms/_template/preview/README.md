# Preview material policy

`material-policy.js` contains rendering behavior specific to
`{{platform_id}}`. The shared viewer supplies camera, animation, and loading
infrastructure; alpha classification, culling, texture transforms, and other
format-specific rules belong here.

Keep this policy self-contained. A change in one platform's renderer should not
change the appearance of assets from another platform.
