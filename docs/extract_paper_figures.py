"""Extract unchanged figure crops from the supplied LUMIXED manuscript.

Requires Poppler's pdftoppm. Run from any directory with the PDF path.
Coordinates refer to a page rendered at a longest dimension of 1100 pixels;
the actual crops are rendered at twice that resolution.
"""
import argparse
from pathlib import Path
import subprocess


FIGURES = {
    "figure-1-dataflow": (3, 108, 121, 280, 255),
    "figure-2-selection": (4, 438, 112, 340, 300),
    "figure-3-pipeline": (5, 442, 211, 335, 123),
    "figure-4-resources": (7, 440, 116, 338, 146),
    "figure-5-vit-pareto": (8, 76, 483, 336, 175),
    "figure-6-qkv": (8, 472, 188, 282, 139),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    args = parser.parse_args()
    destination = Path(__file__).resolve().parent / "assets" / "paper"
    destination.mkdir(parents=True, exist_ok=True)
    for name, (page, x, y, width, height) in FIGURES.items():
        subprocess.run([
            "pdftoppm", "-f", str(page), "-l", str(page), "-singlefile",
            "-scale-to", "2200", "-x", str(x * 2), "-y", str(y * 2),
            "-W", str(width * 2), "-H", str(height * 2), "-png",
            str(args.pdf), str(destination / name),
        ], check=True)
        print(destination / f"{name}.png")


if __name__ == "__main__":
    main()
