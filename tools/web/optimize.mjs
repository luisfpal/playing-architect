// Web delivery of the scene: the raw Blender export was 189 MB and took minutes to load on a phone. Rule: do not nerf the model.
//
//   node tools/web/optimize.mjs build/house.glb build/web/house.glb          (make web-glb)
//
// build/house.glb stays the uncompressed artifact every check reads; this writes the copy the site serves. Same
// triangles, same nodes, names and extras; nothing is simplified. Steps:
//   1. prune unused vertex attributes (meshes carried up to 8 UV sets that no material reads) and unused data;
//   2. dedup identical accessors, textures and materials; weld bit-identical vertices;
//   3. PNG textures re-encoded as lossless WebP at full size (checked below: every pixel identical); JPEG textures are
//      left byte for byte. Lossy WebP (q90, q95) was tried and dropped: compare_shots showed it in the 8x difference
//      (rule: if any lossy step shows a visible difference, drop that step);
//   4. EXT_meshopt_compression after quantization (positions 16 bit: 0.08 mm on a 5.3 m truck, 0.3 mm over a 20 m
//      wall run; normals 12 bit, 'medium' filter: the 8-bit 'high' filter showed faint banding on the truck paint). Each mesh first moves to an unnamed child of its node, so the dequantization transform lands on that
//      child and every named node keeps its exact matrix (the viewer turns cabinet doors about their node origin).
//      The child carries extras {mesh_of: 1}; viewer/main.js reads the shadow flag from its parent.
// tools/web/compare_shots.py proves the result looks the same as build/house.glb at fixed views in the real viewer.
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { prune, dedup, weld, textureCompress, meshopt } from '@gltf-transform/functions';
import { MeshoptEncoder, MeshoptDecoder } from 'meshoptimizer';
import sharp from 'sharp';
import fs from 'node:fs';
import path from 'node:path';

const [src = 'build/house.glb', out = 'build/web/house.glb'] = process.argv.slice(2);
const LEVEL = process.env.MESHOPT_LEVEL ?? 'medium';   // 'high' adds the octahedral 8-bit normal filter (lossy, dropped)
const QPOS = Number(process.env.QPOS ?? 16);
const QNRM = Number(process.env.QNRM ?? 12);
await Promise.all([MeshoptEncoder.ready, MeshoptDecoder.ready]);
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ 'meshopt.encoder': MeshoptEncoder, 'meshopt.decoder': MeshoptDecoder });

const doc = await io.read(src);
const before = { nodes: doc.getRoot().listNodes().length, tris: tris(doc) };
const pngs = new Map();
for (const t of doc.getRoot().listTextures()) if (t.getMimeType() === 'image/png') pngs.set(t, t.getImage().slice());
await doc.transform(
  prune({ keepAttributes: false, keepLeaves: true, keepSolidTextures: true }),
  dedup(),
  weld(),
  ...(process.env.NO_TEX === '1' ? [] : [
    textureCompress({ encoder: sharp, targetFormat: 'webp', formats: /^image\/png$/, lossless: true, effort: 100 }),
  ]),
);
let same = 0;
const kept = [];
for (const [t, png] of pngs) {
  if (t.isDisposed() || t.getMimeType() !== 'image/webp') continue;
  const meta = await sharp(png).metadata();
  if (meta.depth !== 'uchar') throw new Error(`${t.getName()}: ${meta.depth} PNG would lose bits as WebP`);
  const raw = (x) => sharp(x).ensureAlpha().raw().toBuffer();
  if (!(await raw(png)).equals(await raw(t.getImage()))) {
    // sharp drops the colour under fully transparent pixels (bilinear filtering reads it): keep that PNG as it was
    t.setImage(png).setMimeType('image/png');
    if (t.getURI()) t.setURI(t.getURI().replace(/\.webp$/, '.png'));
    kept.push(t.getName());
    continue;
  }
  same++;
}
for (const n of doc.getRoot().listNodes()) {
  const m = n.getMesh();
  if (m) n.setMesh(null).addChild(doc.createNode('').setMesh(m).setExtras({ mesh_of: 1 }));
}
await doc.transform(
  meshopt({ encoder: MeshoptEncoder, level: LEVEL, quantizePosition: QPOS, quantizeNormal: QNRM, quantizeTexcoord: 14 }),
  prune({ keepLeaves: true, keepAttributes: true, keepSolidTextures: true }),
);
const after = { named: doc.getRoot().listNodes().filter((n) => n.getName()).length, tris: tris(doc) };
if (after.tris !== before.tris) throw new Error(`triangle count changed: ${before.tris} -> ${after.tris}`);
if (after.named !== before.nodes) throw new Error(`named nodes changed: ${before.nodes} -> ${after.named}`);
fs.mkdirSync(path.dirname(out), { recursive: true });
await io.write(out, doc);
console.log(`${src} (${(fs.statSync(src).size / 1e6).toFixed(1)} MB) -> ${out} (${(fs.statSync(out).size / 1e6).toFixed(1)} MB), `
  + `${after.tris} triangles in both, ${after.named} named nodes in both, ${same} PNG -> WebP pixel-identical, kept as PNG: ${kept.join(' ') || 'none'}`);

function tris(d) {
  let t = 0;
  for (const n of d.getRoot().listNodes()) {
    for (const p of n.getMesh()?.listPrimitives() ?? []) t += (p.getIndices()?.getCount() ?? p.getAttribute('POSITION').getCount()) / 3;
  }
  return t;
}
