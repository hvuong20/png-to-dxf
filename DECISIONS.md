# Architecture & Design Decisions

## 1. Library Choices

### OpenCV (`opencv-contrib-python`) — not `opencv-python`
The contrib variant is required for `cv2.ximgproc.thinning()` (Zhang-Suen skeletonization).
Installing both packages in the same environment causes conflicts — only install the contrib version.

### ezdxf — not `dxfwrite` or `pyautocad`
- `ezdxf` supports modern DXF versions (R2010+), reads and writes, actively maintained.
- `dxfwrite` is archived and only supports older DXF R12.
- `pyautocad` requires a live AutoCAD instance running — not portable.

---

## 2. Preprocessing Pipeline

### Otsu threshold with `THRESH_BINARY_INV`
Technical drawings are dark lines on a light background. `THRESH_BINARY_INV` inverts so that
**lines = white, background = black** — which is the convention OpenCV detection functions expect.
Otsu automatically finds the best threshold without manual tuning per image.

### Zhang-Suen thinning (skeletonization) before Hough detection
Scanned or rasterized drawings often have strokes 2–5px wide. Without thinning:
- `HoughLinesP` detects two parallel edges of each stroke instead of one center line.
- `HoughCircles` finds overlapping circles at each edge of the stroke.

Thinning reduces all strokes to 1-pixel width, dramatically improving accuracy.
Fallback (when `ximgproc` unavailable): light morphological erosion, accepting a small coordinate offset.

### `fastNlMeansDenoising` over Gaussian blur
Gaussian blur softens edges and degrades Hough detection.
`fastNlMeansDenoising` removes noise while preserving sharp edges — better for line detection.

---

## 3. Line Detection

### `HoughLinesP` (probabilistic) over `HoughLines` (standard)
`HoughLines` returns (rho, theta) — the infinite line equation. Endpoints must be calculated
separately and are approximate. `HoughLinesP` returns actual endpoints `(x1, y1, x2, y2)` which
map directly to DXF `LINE` entities without extra math.

### Deduplication by midpoint + angle proximity
`HoughLinesP` frequently detects the same physical line as multiple overlapping short segments
(especially after thinning creates minor pixel gaps). The deduplication step:
1. Groups segments with midpoints within `line_merge_dist` (8px default) AND angle within 5°.
2. Keeps the longest segment from each group.

---

## 4. Circle & Arc Detection

### Neighborhood check in `_arc_coverage` (tolerance = 8% of radius, min 3px)
After Zhang-Suen thinning, circle pixels are sparse — the exact pixel on the theoretical
circumference is often missing even when the circle is present. Checking a neighborhood of
~8% of the radius finds the actual nearby pixel without false positives.

Without this fix, a clean drawn circle scored only 0.17 coverage and was misclassified as a
broken arc.

### `param2 = 50` as default for `HoughCircles`
This is the accumulator threshold. Lower values detect more circles but increase false positives.
At param2=30, the test image produced 51 arc detections; at param2=50 it reduced to 7.
Users can tune this with `--circle-p2` based on their drawing's density.

### `arc_coverage_threshold = 0.80`
A circle must have at least 80% of its circumference present to be classified as a full CIRCLE
entity. Below 80% it becomes an ARC. This handles:
- Circles with small physical breaks (gaps at intersections with other lines).
- True arcs that span 80%+ of a circle (edge case — exported as ARC, which is correct).

---

## 5. Contour Detection

### `RETR_LIST` over `RETR_TREE`
`RETR_TREE` builds a parent-child hierarchy. For technical drawings this adds complexity
without benefit — every shape is a peer at the same logical level.

### Douglas-Peucker epsilon = 1% of perimeter
`approxPolyDP` simplifies the raw contour (which has one point per pixel) into fewer vertices.
At 1% of perimeter, fine detail is retained while removing pixel-level noise.
Increase to 2–3% for cleaner output on noisy scans.

### Masking already-detected entities before `findContours`
Without masking, `findContours` re-traces every circle, every line, and every rectangle corner
producing duplicate DXF entities. Painting over detected circles and lines (3px brush) with black
before `findContours` prevents this.

---

## 6. Coordinate System

### Y-axis flip: `dxf_y = (image_height - y) * scale`
PNG images use Y increasing downward (origin at top-left).
DXF uses Y increasing upward (origin at bottom-left).

All coordinates pass through the single function `img_to_dxf()` — no ad-hoc flips elsewhere.

### Arc angle conversion: `dxf_start = (360 - img_end) % 360`
The Y-flip reverses the direction arcs are drawn (clockwise ↔ counter-clockwise) and swaps
start/end angles. Swapping and negating compensates for both effects simultaneously.

---

## 7. DXF Output

### Four named layers (LINES, CIRCLES, ARCS, POLYLINES)
Allows the CAD user to toggle visibility or assign linetypes/colors per entity type.
Disable with `--no-layers` for simpler single-layer output (all on layer `0`).

### DXF version R2010 (AC1024)
Compatible with AutoCAD 2010+ and all major free viewers (LibreCAD, DraftSight, FreeCAD).
Older R12 lacks LWPOLYLINE support. Newer R2018 has limited viewer support.
