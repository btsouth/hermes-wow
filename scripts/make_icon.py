#!/usr/bin/env python3
"""Turn generated art into what this project ships.

Two jobs, because they are the same job at different sizes:

* an addon icon: a 512px-or-larger master down to `icon.tga` (64x64, the size the
  client's addon list draws) plus `icon-128.png` for store pages, and the
  `## IconTexture` line in the toc that points the client at it
* a banner: a textless background with the wordmark composited in a real font,
  because image models garble lettering and a misspelled banner is worse than no
  banner

Resizing is done here rather than asked of the model: they produce mush at 64px
and good pixels resize down cleanly.

Run:
    python3 scripts/make_icon.py ~/Downloads/emblem.png
    python3 scripts/make_icon.py --banner ~/Downloads/banner.png --text HermesAI
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - the message is the feature
    print("Pillow is needed: pip install pillow", file=sys.stderr)
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parent.parent
ADDON_NAME = "HermesAI"
DEFAULT_ADDON = ROOT / "addon" / ADDON_NAME

ICON_SIZE = 64
ICON_LARGE = 128

# The wordmark is drawn, not generated. Any of these will do; the first that
# exists wins, and a missing font is a cosmetic problem rather than a crash.
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)

TEXT_COLOR = (232, 237, 247)  # TEXT from the UI palette
SHADOW_COLOR = (13, 18, 32)  # the panel's navy, for the wordmark's shadow


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def prepare(image: Image.Image, crop: bool = True) -> Image.Image:
    """The framed square to draw from: cut to the art, centred.

    A model asked for a 512 icon returns a bigger canvas with the art inset by
    whatever margin it felt like - 8% here. Left alone, that margin survives the
    resize and the icon the client draws at 32px is a third smaller than the space
    it was given. Blizzard's own icons are full bleed, so trim to the art and pad
    back to a centred square, which also keeps a lopsided composition centred.
    """
    if not crop:
        return image

    box = image.getbbox()
    if not box:
        return image

    art = image.crop(box)
    side = max(art.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(art, ((side - art.width) // 2, (side - art.height) // 2), art)
    return square


def write_icon(addon_dir: Path, master: Path, crop: bool = True) -> list[Path]:
    """The addon-list icon, at the two sizes worth keeping."""
    image = Image.open(master).convert("RGBA")

    # Said out loud rather than left to be discovered in the addon list: a master
    # without an alpha channel gives a solid square behind a rounded plate.
    if image.getchannel("A").getextrema() == (255, 255):
        print("note: this master has no transparency - the icon will be a solid square")

    image = prepare(image, crop)

    written: list[Path] = []
    small = addon_dir / "icon.tga"
    image.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS).save(small, format="TGA")
    written.append(small)

    large = addon_dir / "icon-128.png"
    image.resize((ICON_LARGE, ICON_LARGE), Image.LANCZOS).save(large, format="PNG")
    written.append(large)

    return written


def wire_toc(addon_dir: Path) -> bool:
    """Point the client at the icon. Returns whether the toc changed."""
    toc = addon_dir / f"{ADDON_NAME}.toc"
    lines = toc.read_text(encoding="utf-8").splitlines()

    if any(line.startswith("## IconTexture:") for line in lines):
        return False

    # After Notes, which is where the client's own addons put it.
    line = f"## IconTexture: Interface\\AddOns\\{ADDON_NAME}\\icon"
    for index, existing in enumerate(lines):
        if existing.startswith("## Notes:"):
            lines.insert(index + 1, line)
            break
    else:
        lines.insert(1, line)

    toc.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def write_banner(master: Path, out: Path, text: str) -> Path:
    """Composite the wordmark. Real letters, real font, no model lettering."""
    image = Image.open(master).convert("RGBA")
    draw = ImageDraw.Draw(image)

    # Sized to the image rather than fixed, so the same call works for a 1280x640
    # social card and anything else roughly banner-shaped.
    size = max(24, int(image.height * 0.16))
    font = load_font(size)
    left = int(image.width * 0.06)
    top = int(image.height * 0.66)

    draw.text((left + 2, top + 2), text, font=font, fill=SHADOW_COLOR)
    draw.text((left, top), text, font=font, fill=TEXT_COLOR)

    out.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(out, format="PNG")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("master", help="the generated PNG (512px or larger)")
    parser.add_argument("--addon", default=str(DEFAULT_ADDON), help="the addon folder to write into")
    parser.add_argument("--banner", action="store_true", help="compose a banner instead of an icon")
    parser.add_argument("--text", default="HermesAI", help="the wordmark for --banner")
    parser.add_argument("--out", default=str(ROOT / "dist" / "banner.png"), help="--banner output path")
    parser.add_argument("--no-crop", action="store_true",
                        help="keep the master's own framing instead of trimming to the art")
    args = parser.parse_args()

    master = Path(args.master).expanduser()
    if not master.is_file():
        print(f"no such image: {master}", file=sys.stderr)
        return 1

    if args.banner:
        written = write_banner(master, Path(args.out), args.text)
        print(f"banner with the wordmark {args.text!r} -> {written}")
        return 0

    addon_dir = Path(args.addon)
    if not addon_dir.is_dir():
        print(f"no such addon folder: {addon_dir}", file=sys.stderr)
        return 1

    for path in write_icon(addon_dir, master, crop=not args.no_crop):
        print(f"wrote {path}")

    if wire_toc(addon_dir):
        print(f"added ## IconTexture to {addon_dir / (ADDON_NAME + '.toc')}")
    print("now: make package  (the client reads the icon by name, so it has to be in the zip)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
