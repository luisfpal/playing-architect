---
name: no-nerf-optimization
description: Use when making a heavy asset (scene, texture, bundle) smaller or faster to load. Only steps that are provably invisible are kept.
---
# Optimize without nerfing

1. Measure first: size on disk, size over the wire (gzip), load time on a phone-class device, triangle count.
2. Prefer lossless steps: prune unused attributes (unused UV sets were a large share here), dedup, weld identical
   vertices, PNG to lossless WebP (assert every pixel identical), mesh compression (meshopt).
3. For each lossy step (quantization, lossy textures), render fixed views before and after and make an 8x difference
   image. Keep the step only if the difference is invisible at 1x and explainable at 8x. Here: 16-bit positions and
   12-bit normals stayed; 8-bit normals (banding on car paint) and lossy WebP (visible in the difference) were dropped.
4. Assert in the script that the triangle count and the named nodes are unchanged; fail the build if not.
5. Serve with gzip and ETag revalidation so a repeat visit costs one 304, not the whole file.
