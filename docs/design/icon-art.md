# Icon art

Everything image-shaped the project needs, in the order it matters, with prompts
that produce art matching the panel's existing palette rather than a generic app
icon dropped next to it.

Three of the four are optional. Only the first is worth getting right: the emblem
is the whole visual identity, and the others are that emblem re-composed.

## The palette, taken from the running UI

The panel's dark skin, so generated art and drawn UI agree. These are the exact
values from `THEMES.dark` in `addon/HermesAI/UI.lua`, not approximations:

| Role | Hex | Where it already appears |
| --- | --- | --- |
| Deep navy (the panel) | `#0D1220` | every pane's background |
| Accent blue | `#2F6BD8` | the panel border, the active tab |
| Gold | `#C7A359` | the crest `H`, the idle badge border |
| Text | `#E8EDF7` | titles |
| Muted | `#949EB8` | the trail line under each row |

Gold on deep navy is the identity: that pairing is already on screen in the
crest, the badge and the minimap button. Blue is an accent, not a second theme —
use it for at most one rim light.

## 1. The emblem — the one that matters

**Prompt (copy this whole block):**

> A single flat vector-style icon for a World of Warcraft addon, centred on a
> fully transparent background, 512 by 512 pixels. Subject: a bold geometric
> monogram of the capital letter H, formed from two thick vertical bars and a
> crossbar, with the crossbar extending past the bars on the right into a short
> pair of swept-back wings — two tapered chevrons at most — suggesting a messenger
> in motion. The H is warm gold `#C7A359`, with a lighter gold `#E3C98A` highlight
> along its top and left bevels and a darker gold `#8C6F35` along the bottom and
> right, so it reads as gently embossed metal rather than flat colour. Behind it, a
> rounded square plate in deep navy `#0D1220` fills most of the frame, with a thin
> accent-blue `#2F6BD8` rim light along its top edge only. Style: clean vector
> shapes, hard crisp edges, thick strokes, low detail count, subtle inner bevel —
> the visual idiom of a WoW interface icon. Flat colour, no gradients that band, no
> photorealism, no 3D render, no drop shadow on the canvas, no outer glow.
> Absolutely no text, no lettering other than the single letter H, no numbers, no
> watermark, no border frame. The silhouette must stay legible when scaled down to
> 32 by 32 pixels, so keep every shape thick and remove fine detail.

Then ask for **nothing else in the same image** — no mockups, no UI, no words.

Why the constraints are there: an addon icon is drawn at 32–64px in a list, so
thin strokes and fine detail turn to mud; and image models happily add invented
lettering, which is the fastest way to make a release look amateurish.

## 2. The project avatar — 1024×1024, opaque

Same emblem, re-composed for a store page. **Prompt:**

> Take the emblem described above and render it 1024 by 1024, fully opaque, on a
> deep navy `#0D1220` background with a very subtle radial lift in the centre
> (about 6% lighter) so the plate does not read as a flat black square. The emblem
> sits centred with even padding of roughly 12% on every side. Same gold palette,
> same flat vector style, same bevel. No text, no watermark, no border.

## 3. The social preview banner — 1280×640, no text

GitHub's repo social preview and most store headers. **Prompt:**

> A wide 1280 by 640 composition, fully opaque, deep navy `#0D1220` background
> with a faint accent-blue `#2F6BD8` glow in the lower left. The gold emblem from
> above is placed in the left third, occupying about 45% of the height, fully
> inside the frame. The right two thirds are left as quiet negative space with only
> a very subtle abstract suggestion of horizontal list rows — three or four thin,
> low-contrast rounded bars in slightly lighter navy, no text, no icons, no
> numbers. Flat vector style, crisp edges, no gradients that band, no text
> anywhere, no watermark.

**Leave the wordmark to a real font.** Image models garble lettering, and a
misspelled banner is worse than a textless one. Add `HermesAI` afterwards — the
`make icon` step below composites it in `#E8EDF7` using a system font, so the
letters are actually letters.

## 4. Optional: a custom minimap disc — 64×64, transparent

Only if you want to replace the client's own minimap-button disc and the gold `H`
glyph with your own art. The addon currently draws the game's disc with a font
glyph on top, deliberately, because it cannot break at an odd UI scale. **Prompt:**

> A circular minimap-button texture, 64 by 64, on a fully transparent background:
> a dark navy `#0D1220` disc that fills the frame edge to edge, with a thin gold
> `#C7A359` ring inset about 2 pixels from the edge, and the gold H emblem from
> above centred inside it at about 55% of the diameter. Soft antialiased edge, no
> outer glow, no drop shadow, no text other than the H.

## Turning the result into something the client reads

Generate at 512 or larger and let the resize happen here rather than asking the
model for a small image — models produce mush at 64px and good pixels resized
down. Then:

```bash
make icon MASTER=~/Downloads/emblem.png
```

That writes `addon/HermesAI/icon.tga` (64×64, 32-bit with alpha — the addon-list
icon), `addon/HermesAI/icon-128.png` (for the store page and the docs), and adds
`## IconTexture` to the toc if it is missing. `make package` then carries both,
because the client reads them by name rather than from the toc.

`.tga` is one of the two formats the client loads textures from, and Pillow writes
it losslessly with an alpha channel. If you would rather ship the native `.blp`,
install a BLP converter and use that instead — the wiring is the same.

If you only do one of these, do the emblem. A release with a good 64×64 icon and
no banner looks considered; a release with a banner and a placeholder icon does
not.
