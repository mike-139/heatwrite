"""Erzeugt Icon und Logo für home-assistant/brands → custom_integrations/heatwrite.

Eigenes Motiv, keine fremden Marken: Regler-Symbol ("einstellbare Parameter")
auf einem Verlauf von Kühl-Blau nach Heiz-Orange — die beiden Betriebsarten
einer Wärmepumpe.
"""

from PIL import Image, ImageDraw, ImageFont

COLD = (28, 126, 214)   # #1c7ed6
WARM = (232, 89, 12)    # #e8590c
WHITE = (255, 255, 255, 255)
SS = 4                  # Supersampling

FONT_BOLD = "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf"
FONT_MED = "/usr/share/fonts/truetype/google-fonts/Poppins-Medium.ttf"


def gradient(width: int, height: int) -> Image.Image:
    """Diagonaler Verlauf, unten links kalt, oben rechts warm."""
    img = Image.new("RGB", (width, height))
    px = img.load()
    lo, hi = 0.36, 0.64  # Übergang nur um die Diagonale, sonst bleiben die
    for y in range(height):  # Farben satt statt matschig
        for x in range(width):
            t = (x / max(width - 1, 1) + (height - 1 - y) / max(height - 1, 1)) / 2
            t = min(1.0, max(0.0, (t - lo) / (hi - lo)))
            t = t * t * (3 - 2 * t)  # smoothstep
            px[x, y] = tuple(
                round(COLD[i] + (WARM[i] - COLD[i]) * t) for i in range(3)
            )
    return img


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius, fill=255)
    return mask


def sliders(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    """Drei Regler — Sinnbild für einstellbare Parameter."""
    left, top, right, bottom = box
    width = right - left
    height = bottom - top
    bar = round(height * 0.088)          # Balkenstärke
    knob = round(height * 0.165)         # Knopfradius
    rows = [top + height * f for f in (0.12, 0.5, 0.88)]
    positions = (0.64, 0.30, 0.72)       # Knopfposition je Regler

    for y, pos in zip(rows, positions):
        draw.rounded_rectangle(
            [left, y - bar / 2, right, y + bar / 2], bar / 2, fill=WHITE
        )
        cx = left + width * pos
        # Loch im Balken, damit der Knopf freisteht
        draw.ellipse(
            [cx - knob * 1.42, y - knob * 1.42, cx + knob * 1.42, y + knob * 1.42],
            fill=(0, 0, 0, 0),
        )
        draw.ellipse([cx - knob, y - knob, cx + knob, y + knob], fill=WHITE)


def make_icon(size: int) -> Image.Image:
    s = size * SS
    tile = gradient(s, s).convert("RGBA")
    tile.putalpha(rounded_mask((s, s), round(s * 0.222)))

    glyph = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    sliders(ImageDraw.Draw(glyph), (round(s * 0.21), round(s * 0.30), round(s * 0.79), round(s * 0.70)))

    tile.alpha_composite(glyph)
    return tile.resize((size, size), Image.LANCZOS)


def make_logo(height: int) -> Image.Image:
    """Quadratisches Zeichen links, Schriftzug rechts."""
    s = height * SS
    pad = round(s * 0.08)
    mark = make_icon(s)

    font_top = ImageFont.truetype(FONT_BOLD, round(s * 0.30))
    font_bottom = ImageFont.truetype(FONT_MED, round(s * 0.155))
    gap = round(s * 0.16)

    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    w_top = probe.textbbox((0, 0), "HeatWrite", font=font_top)[2]
    w_bottom = probe.textbbox((0, 0), "für Wolf-Heizungen", font=font_bottom)[2]
    text_w = max(w_top, w_bottom)

    canvas = Image.new("RGBA", (s + gap + text_w + pad, s), (0, 0, 0, 0))
    canvas.alpha_composite(mark, (0, 0))

    draw = ImageDraw.Draw(canvas)
    x = s + gap
    draw.text((x, round(s * 0.30)), "HeatWrite", font=font_top, fill=(26, 26, 26, 255), anchor="ls")
    draw.text((x, round(s * 0.66)), "für Wolf-Heizungen", font=font_bottom, fill=WARM + (255,), anchor="ls")

    scale = height / s
    return canvas.resize((round(canvas.width * scale), height), Image.LANCZOS)


if __name__ == "__main__":
    import sys

    out = sys.argv[1]  # Zielordner, hier custom_components/heatwrite/brand
    make_icon(256).save(f"{out}/icon.png", optimize=True)
    make_icon(512).save(f"{out}/icon@2x.png", optimize=True)
    make_logo(256).save(f"{out}/logo.png", optimize=True)
    make_logo(512).save(f"{out}/logo@2x.png", optimize=True)
    print("fertig")
