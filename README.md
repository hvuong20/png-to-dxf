# png-to-dxf

Convert a technical drawing PNG image into an AutoCAD-compatible DXF file.

Detects straight lines, full circles, arcs, and complex contours using OpenCV,
then exports each as a native DXF entity via ezdxf.

```
Input PNG                          Output DXF
┌─────────────────────┐            ┌─────────────────────┐
│  ╔═══════╗          │            │  Layer LINES   (2)  │
│  ║       ║   ○      │  ──────►  │  Layer CIRCLES (5)  │
│  ╚═══════╝  / \     │            │  Layer ARCS    (1)  │
│            /   \    │            │  Layer POLYLINES(3) │
└─────────────────────┘            └─────────────────────┘
```

---

## Requirements

- Python 3.8+
- Windows / macOS / Linux

## Installation

```bash
pip install opencv-contrib-python ezdxf numpy
```

> **Note:** Install `opencv-contrib-python` only — do **not** install `opencv-python`
> alongside it; they conflict with each other.

---

## Quick Start

```bash
python png_to_dxf.py input.png output.dxf
```

Preview detected shapes before saving:

```bash
python png_to_dxf.py input.png output.dxf --preview --verbose
```

---

## All Options

```
python png_to_dxf.py <input.png> <output.dxf> [options]

Positional arguments:
  input.png             Source PNG file path
  output.dxf            Destination DXF file path

Detection options:
  --min-line  INT       Minimum line length in pixels to detect (default: 40)
                        Lower = detects shorter lines but more noise
  --max-gap   INT       Max pixel gap to bridge within a line (default: 10)
                        Higher = merges broken/dashed lines
  --epsilon   FLOAT     Douglas-Peucker simplification factor (default: 0.01)
                        Higher = fewer polyline vertices, less detail
  --circle-p2 INT       HoughCircles sensitivity threshold (default: 50)
                        Lower = detects more circles (may add false positives)

Scale options:
  --scale     FLOAT     Pixels per DXF unit (default: 1.0)
                        Use 0.0846 for 300 DPI → millimeters
                        Use 0.0353 for 72 DPI  → millimeters

Output options:
  --preview             Show a colour-coded preview window before saving
  --deskew              Auto-correct image rotation (useful for scanned drawings)
  --no-layers           Write all entities to layer 0 (simpler output)
  --verbose             Print entity counts after each detection stage
```

---

## Output DXF Structure

The output DXF uses four named layers, each with a default colour:

| Layer | Colour | Entity type | Contains |
|---|---|---|---|
| `LINES` | Yellow | `LINE` | Straight line segments |
| `CIRCLES` | Blue | `CIRCLE` | Full circles |
| `ARCS` | Red | `ARC` | Partial arcs |
| `POLYLINES` | Green | `LWPOLYLINE` | Curved or irregular shapes |

Open the DXF in AutoCAD, LibreCAD, or FreeCAD and toggle layers as needed.

---

## Examples by Drawing Type

### P&ID / Piping diagram
```bash
python png_to_dxf.py piping.png piping.dxf --min-line 20 --epsilon 0.015 --verbose
```
Piping diagrams have many short connecting lines — lower `--min-line` to capture them.

### Electrical schematic
```bash
python png_to_dxf.py schematic.png schematic.dxf --min-line 15 --circle-p2 40 --verbose
```
Schematics have small component circles (diodes, transistors) — lower `--circle-p2` helps detect them.

### Mechanical / architectural drawing (scanned)
```bash
python png_to_dxf.py scan.png scan.dxf --deskew --scale 0.0846 --preview
```
Use `--deskew` for scanned sheets that may be slightly rotated.
Use `--scale 0.0846` to convert 300 DPI pixels → real millimeters.

### High-noise scan
```bash
python png_to_dxf.py noisy.png noisy.dxf --min-line 50 --circle-p2 60 --epsilon 0.02 --verbose
```
Raise thresholds to filter out speckles and scan artifacts.

---

## Scale Reference

To get real-world units in the DXF, calculate: `scale = 25.4 / DPI`

| Scan DPI | `--scale` value | DXF unit |
|---|---|---|
| 72 | 0.353 | mm |
| 96 | 0.265 | mm |
| 150 | 0.169 | mm |
| 300 | 0.0846 | mm |
| 600 | 0.0423 | mm |

---

## Troubleshooting

**Too many false arc/circle detections**
→ Increase `--circle-p2` (try 60–80). Use `--preview` to inspect visually.

**Real circles not being detected**
→ Decrease `--circle-p2` (try 30–40).

**Lines broken into many short segments**
→ Increase `--max-gap` (try 20–30).

**Short lines missing**
→ Decrease `--min-line` (try 15–25).

**Drawing appears mirrored vertically in AutoCAD**
→ This should not happen — `img_to_dxf()` handles the Y-flip automatically. If it does,
check that your DXF viewer is not applying its own Y transform.

**UnicodeEncodeError on Windows**
→ Run with `PYTHONIOENCODING=utf-8 python png_to_dxf.py ...` or use PowerShell instead of CMD.

**Very slow on large images (A0 scan at 300 DPI)**
→ Downsample before processing:
```python
import cv2
img = cv2.imread("large.png")
small = cv2.resize(img, None, fx=0.5, fy=0.5)
cv2.imwrite("large_half.png", small)
```
Then run with `--scale 0.169` (compensating for the 2x downsample at 300 DPI → effectively 150 DPI).

---

## How It Works

```
PNG
 │
 ├─ Grayscale + fastNlMeansDenoising
 ├─ Otsu threshold (THRESH_BINARY_INV) — lines become white
 └─ Zhang-Suen thinning → 1-pixel skeleton
        │
        ├─ HoughLinesP ──────────── LINE entities
        ├─ HoughCircles + coverage check:
        │    coverage ≥ 80% → CIRCLE entity
        │    coverage  < 80% → ARC entity
        └─ findContours + Douglas-Peucker → LWPOLYLINE entities
               │
               └─ ezdxf export with Y-axis flip (image Y↓ → DXF Y↑)
```

See [DECISIONS.md](DECISIONS.md) for the reasoning behind each technical choice.

---

## Viewing the Output

Free DXF viewers:
- **LibreCAD** — full-featured, cross-platform: https://librecad.org
- **FreeCAD** — parametric CAD, opens DXF: https://freecad.org
- **AutoCAD web** — browser-based viewer (requires Autodesk account)

---

## License

MIT
