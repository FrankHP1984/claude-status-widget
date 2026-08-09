"""Genera assets/widget.ico, el icono del acceso directo de escritorio.

Se guarda el generador y no solo el .ico para que el icono se pueda
rehacer si cambia la paleta del panel, que es de donde salen los
colores.

    python assets/make_icon.py

El dibujo se adapta al tamano: en los iconos grandes se ve el panel con
sus tres filas; en los pequenos (16 y 24 px) esas filas serian papilla,
asi que solo queda el punto verde, que es lo unico que hay que
reconocer en la barra de tareas.
"""
from pathlib import Path

from PIL import Image, ImageDraw

# Mismos colores que el panel (widget/app.py).
BG_CARD = (26, 30, 39, 255)
BORDER = (38, 43, 54, 255)
VERDE = (47, 208, 122, 255)
AMBAR = (240, 180, 60, 255)
GRIS = (100, 108, 125, 255)

SIZES = (16, 24, 32, 48, 64, 128, 256)


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    s = size / 256  # factor de escala respecto al diseno base

    # Tarjeta redondeada de fondo, con un margen que no se come el
    # dibujo en los tamanos pequenos.
    margin = max(1, round(12 * s))
    radius = max(2, round(48 * s))
    draw.rounded_rectangle(
        (margin, margin, size - margin - 1, size - margin - 1),
        radius=radius, fill=BG_CARD, outline=BORDER, width=max(1, round(4 * s)),
    )

    if size < 32:
        # Un solo punto centrado: a este tamano es lo unico legible.
        r = max(2, round(size * 0.22))
        cx = cy = size / 2
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=VERDE)
        return img

    # Tres filas = tres sesiones, cada una con su punto de estado.
    dot_r = max(1, round(16 * s))
    dot_x = round(60 * s)
    bar_x0 = round(92 * s)
    bar_x1 = size - round(52 * s)
    bar_h = max(1, round(14 * s))
    for i, color in enumerate((VERDE, AMBAR, GRIS)):
        cy = round((80 + i * 48) * s)
        draw.ellipse((dot_x - dot_r, cy - dot_r, dot_x + dot_r, cy + dot_r), fill=color)
        # La barra se acorta un poco en cada fila para que no parezca
        # una rejilla y se lea como texto.
        x1 = bar_x1 - round(i * 18 * s)
        draw.rounded_rectangle(
            (bar_x0, cy - bar_h // 2, x1, cy - bar_h // 2 + bar_h),
            radius=bar_h // 2, fill=(*color[:3], 150),
        )
    return img


def main() -> None:
    destino = Path(__file__).resolve().parent / "widget.ico"
    imagenes = [draw_icon(s) for s in SIZES]
    # El .ico guarda todas las resoluciones; Windows elige la que toque
    # segun donde se pinte (escritorio, barra de tareas, alt-tab).
    imagenes[-1].save(destino, format="ICO",
                      sizes=[(s, s) for s in SIZES], append_images=imagenes[:-1])
    print(f"Icono escrito en {destino}")


if __name__ == "__main__":
    main()
