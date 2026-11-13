---
name: render-and-look
description: Use for any change to a 3D scene, model, material or camera. A change is not done until it has been rendered and the pictures have been looked at.
---
# Render and look

Numbers pass while pictures fail: a wall can have the right length and the wrong side, a wheel the right radius and
the wrong brand. So every scene change ends with images that a model (or a person) actually opens.

1. Keep a fixed list of cameras: one per room, plus close-ups of the hard parts (wheels, badges, lamps, stairs, doors).
   Fixed cameras make before/after comparable.
2. After the change, render every camera that could be affected, headless (Blender `-b`, or the web viewer in headless
   Chromium with a fixed seed and time).
3. Open each image and describe what is in it, in words, before judging it. Look for: missing or floating objects,
   z-fighting, wrong materials, objects inside walls, text that should not be there, lighting that went black.
4. Where an earlier render exists, make a side-by-side A | B | 8x difference image; a difference you cannot explain is a bug.
5. Write down what each picture showed in the change log, with the image path. "Rendered, looks fine" is not enough;
   say what you saw.
6. If a fix does not change the picture, the fix did not happen. Re-render after every fix, not only at the end.
