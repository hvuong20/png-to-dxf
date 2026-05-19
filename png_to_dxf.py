"""
png_to_dxf.py -- Convert technical drawing PNG to DXF (AutoCAD)

Usage:
    python png_to_dxf.py input.png output.dxf
    python png_to_dxf.py input.png output.dxf --preview --verbose
    python png_to_dxf.py input.png output.dxf --scale 0.0846 --min-line 30

Install:
    pip install opencv-contrib-python ezdxf numpy
"""
# -*- coding: utf-8 -*-

import argparse
import math
import sys

import cv2
import ezdxf
import numpy as np

# ---------------------------------------------------------------------------
# CONFIG — tất cả tham số tunable, CLI args sẽ ghi đè từng key
# ---------------------------------------------------------------------------
CONFIG = {
    # Preprocessing
    "denoise_h": 10,
    "blur_ksize": 5,

    # Hough Lines (HoughLinesP)
    "hough_rho": 1,
    "hough_theta": math.pi / 180,
    "hough_threshold": 50,
    "hough_min_line_length": 40,
    "hough_max_line_gap": 10,
    "line_merge_dist": 8,       # px — khoảng cách để gom đoạn trùng

    # Hough Circles
    "circle_dp": 1.2,
    "circle_min_dist": 20,
    "circle_param1": 50,
    "circle_param2": 50,
    "circle_min_radius": 5,
    "circle_max_radius": 0,     # 0 = không giới hạn

    # Arc — tỷ lệ chu vi có mặt để xem là circle đầy đủ
    "arc_coverage_threshold": 0.80,
    "arc_sample_points": 72,    # số điểm lấy mẫu trên chu vi

    # Contour / Polyline
    "contour_min_area": 20,     # px² — lọc nhiễu nhỏ
    "dp_epsilon_factor": 0.01,  # Douglas-Peucker epsilon = factor * perimeter

    # DXF
    "pixels_per_unit": 1.0,
    "dxf_version": "R2010",
    "use_layers": True,

    # Output
    "show_preview": False,
    "verbose": False,
    "deskew": False,
}


# ---------------------------------------------------------------------------
# 1. Load
# ---------------------------------------------------------------------------

def load_image(path: str) -> np.ndarray:
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Không tìm thấy file hoặc không đọc được: {path}")
    return img


# ---------------------------------------------------------------------------
# 2. Preprocess
# ---------------------------------------------------------------------------

def _deskew(gray: np.ndarray) -> np.ndarray:
    """Chỉnh góc nghiêng dựa trên các đường Hough dominant."""
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLines(edges, 1, math.pi / 180, 150)
    if lines is None:
        return gray
    angles = []
    for line in lines[:20]:
        theta = line[0][1]
        angle = math.degrees(theta) - 90
        if abs(angle) < 45:
            angles.append(angle)
    if not angles:
        return gray
    median_angle = float(np.median(angles))
    if abs(median_angle) < 0.3:
        return gray
    h, w = gray.shape
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, median_angle, 1.0)
    return cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)


def preprocess(img: np.ndarray) -> tuple:
    """Trả về (gray_denoised, binary)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.fastNlMeansDenoising(
        gray, h=CONFIG["denoise_h"],
        templateWindowSize=7, searchWindowSize=21
    )
    if CONFIG["deskew"]:
        gray = _deskew(gray)

    _, binary = cv2.threshold(gray, 0, 255,
                              cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Skeletonize nếu có opencv-contrib
    try:
        binary = cv2.ximgproc.thinning(
            binary, thinningType=cv2.ximgproc.THINNING_ZHANGSUEN
        )
    except AttributeError:
        # Fallback: erode nhẹ để bỏ thick strokes
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        binary = cv2.erode(binary, kernel, iterations=1)

    return gray, binary


# ---------------------------------------------------------------------------
# 3A. Line detection
# ---------------------------------------------------------------------------

def _angle_of_segment(x1, y1, x2, y2) -> float:
    return math.atan2(y2 - y1, x2 - x1)


def _segments_are_duplicate(s1, s2) -> bool:
    """True nếu 2 đoạn gần như trùng nhau (cùng góc, cùng vị trí)."""
    x1, y1, x2, y2 = s1
    x3, y3, x4, y4 = s2
    mid1 = ((x1 + x2) / 2, (y1 + y2) / 2)
    mid2 = ((x3 + x4) / 2, (y3 + y4) / 2)
    dist = math.hypot(mid1[0] - mid2[0], mid1[1] - mid2[1])
    if dist > CONFIG["line_merge_dist"] * 3:
        return False
    a1 = _angle_of_segment(x1, y1, x2, y2) % math.pi
    a2 = _angle_of_segment(x3, y3, x4, y4) % math.pi
    angle_diff = abs(a1 - a2)
    angle_diff = min(angle_diff, math.pi - angle_diff)
    return dist < CONFIG["line_merge_dist"] and angle_diff < math.radians(5)


def detect_lines(binary: np.ndarray) -> list:
    """Trả về list các (x1, y1, x2, y2)."""
    raw = cv2.HoughLinesP(
        binary,
        rho=CONFIG["hough_rho"],
        theta=CONFIG["hough_theta"],
        threshold=CONFIG["hough_threshold"],
        minLineLength=CONFIG["hough_min_line_length"],
        maxLineGap=CONFIG["hough_max_line_gap"],
    )
    if raw is None:
        return []

    segments = [tuple(r[0]) for r in raw]

    # Deduplicate
    kept = []
    used = [False] * len(segments)
    for i, s1 in enumerate(segments):
        if used[i]:
            continue
        best = s1
        best_len = math.hypot(s1[2] - s1[0], s1[3] - s1[1])
        for j, s2 in enumerate(segments[i + 1:], start=i + 1):
            if not used[j] and _segments_are_duplicate(s1, s2):
                used[j] = True
                l = math.hypot(s2[2] - s2[0], s2[3] - s2[1])
                if l > best_len:
                    best = s2
                    best_len = l
        kept.append(best)

    return kept


# ---------------------------------------------------------------------------
# 3B. Circle / Arc detection
# ---------------------------------------------------------------------------

def _arc_coverage(binary: np.ndarray, cx, cy, r) -> tuple:
    """
    Trả về (coverage_fraction, start_angle_deg, end_angle_deg).
    Angles tính từ +X, counter-clockwise, trong image space.
    Kiểm tra vùng lân cận tol px quanh mỗi điểm mẫu để xử lý ảnh sau thinning.
    """
    n = CONFIG["arc_sample_points"]
    tol = max(3, int(r * 0.08))   # tolerance ~8% of radius, min 3px
    present = []
    h, w = binary.shape
    for i in range(n):
        angle = 2 * math.pi * i / n
        px = int(round(cx + r * math.cos(angle)))
        py = int(round(cy + r * math.sin(angle)))
        found = False
        for dy in range(-tol, tol + 1):
            for dx in range(-tol, tol + 1):
                nx, ny = px + dx, py + dy
                if 0 <= nx < w and 0 <= ny < h and binary[ny, nx] > 0:
                    found = True
                    break
            if found:
                break
        present.append(found)

    coverage = sum(present) / n

    # Tìm span góc của arc
    start_idx = end_idx = None
    for i in range(n):
        if present[i]:
            start_idx = i
            break
    if start_idx is None:
        return 0.0, 0.0, 0.0

    # Tìm run dài nhất
    best_start = best_end = 0
    best_len = 0
    cur_start = start_idx
    cur_len = 0
    for i in range(n):
        idx = (start_idx + i) % n
        if present[idx]:
            cur_len += 1
            if cur_len > best_len:
                best_len = cur_len
                best_start = cur_start
                best_end = idx
        else:
            cur_start = (idx + 1) % n
            cur_len = 0

    start_angle = math.degrees(2 * math.pi * best_start / n)
    end_angle = math.degrees(2 * math.pi * best_end / n)
    return coverage, start_angle, end_angle


def detect_circles_and_arcs(gray: np.ndarray, binary: np.ndarray) -> tuple:
    """Trả về (circles, arcs). Mỗi phần tử là dict."""
    blurred = cv2.GaussianBlur(gray, (CONFIG["blur_ksize"], CONFIG["blur_ksize"]), 0)
    raw = cv2.HoughCircles(
        blurred,
        cv2.HOUGH_GRADIENT,
        dp=CONFIG["circle_dp"],
        minDist=CONFIG["circle_min_dist"],
        param1=CONFIG["circle_param1"],
        param2=CONFIG["circle_param2"],
        minRadius=CONFIG["circle_min_radius"],
        maxRadius=CONFIG["circle_max_radius"],
    )

    circles = []
    arcs = []
    if raw is None:
        return circles, arcs

    for c in np.round(raw[0]).astype(int):
        cx, cy, r = float(c[0]), float(c[1]), float(c[2])
        coverage, start_a, end_a = _arc_coverage(binary, cx, cy, r)
        if coverage >= CONFIG["arc_coverage_threshold"]:
            circles.append({"cx": cx, "cy": cy, "r": r})
        else:
            arcs.append({"cx": cx, "cy": cy, "r": r,
                         "start_angle": start_a, "end_angle": end_a})

    return circles, arcs


# ---------------------------------------------------------------------------
# 3C. Contour / Polyline detection
# ---------------------------------------------------------------------------

def _point_near_circle(px, py, circles, tol=8) -> bool:
    for c in circles:
        dist = math.hypot(px - c["cx"], py - c["cy"])
        if abs(dist - c["r"]) < tol:
            return True
    return False


def detect_contours(binary: np.ndarray, circles: list, lines: list) -> list:
    """Trả về list các polyline, mỗi polyline là list (x, y) float."""
    # Mask vùng đã detect để tránh trùng lặp
    mask = binary.copy()
    h, w = mask.shape
    for c in circles:
        cv2.circle(mask, (int(c["cx"]), int(c["cy"])), int(c["r"]) + 3, 0, 3)
    for seg in lines:
        x1, y1, x2, y2 = seg
        cv2.line(mask, (x1, y1), (x2, y2), 0, 3)

    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)

    result = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < CONFIG["contour_min_area"]:
            continue
        perimeter = cv2.arcLength(cnt, True)
        epsilon = CONFIG["dp_epsilon_factor"] * perimeter
        approx = cv2.approxPolyDP(cnt, epsilon, True)
        pts = [(float(p[0][0]), float(p[0][1])) for p in approx]
        if len(pts) >= 2:
            result.append(pts)

    return result


# ---------------------------------------------------------------------------
# 5. Coordinate transform
# ---------------------------------------------------------------------------

def img_to_dxf(x: float, y: float, img_height: int) -> tuple:
    scale = CONFIG["pixels_per_unit"]
    return (x * scale, (img_height - y) * scale)


def convert_arc_angles(img_start: float, img_end: float) -> tuple:
    """Chuyển góc image space (Y↓) sang DXF space (Y↑)."""
    dxf_start = (-img_end) % 360
    dxf_end = (-img_start) % 360
    return dxf_start, dxf_end


# ---------------------------------------------------------------------------
# 6. DXF Export
# ---------------------------------------------------------------------------

def export_dxf(lines, circles, arcs, contours, img_height, output_path):
    doc = ezdxf.new(CONFIG["dxf_version"])
    msp = doc.modelspace()

    if CONFIG["use_layers"]:
        doc.layers.add("LINES",     color=2)   # yellow
        doc.layers.add("CIRCLES",   color=5)   # blue
        doc.layers.add("ARCS",      color=1)   # red
        doc.layers.add("POLYLINES", color=3)   # green

    def layer(name):
        return {"layer": name} if CONFIG["use_layers"] else {}

    H = img_height

    for x1, y1, x2, y2 in lines:
        msp.add_line(img_to_dxf(x1, y1, H), img_to_dxf(x2, y2, H),
                     dxfattribs=layer("LINES"))

    for c in circles:
        center = img_to_dxf(c["cx"], c["cy"], H)
        msp.add_circle(center, c["r"] * CONFIG["pixels_per_unit"],
                       dxfattribs=layer("CIRCLES"))

    for a in arcs:
        center = img_to_dxf(a["cx"], a["cy"], H)
        start_dxf, end_dxf = convert_arc_angles(a["start_angle"], a["end_angle"])
        msp.add_arc(center, a["r"] * CONFIG["pixels_per_unit"],
                    start_dxf, end_dxf,
                    dxfattribs=layer("ARCS"))

    for pts in contours:
        pts_dxf = [img_to_dxf(x, y, H) for x, y in pts]
        first, last = pts[0], pts[-1]
        is_closed = math.hypot(first[0] - last[0], first[1] - last[1]) < 5
        msp.add_lwpolyline(pts_dxf, close=is_closed,
                           dxfattribs=layer("POLYLINES"))

    doc.saveas(output_path)


# ---------------------------------------------------------------------------
# 7. Preview
# ---------------------------------------------------------------------------

def show_preview(original, lines, circles, arcs, contours):
    preview = original.copy()
    for x1, y1, x2, y2 in lines:
        cv2.line(preview, (x1, y1), (x2, y2), (0, 255, 0), 1)
    for c in circles:
        cv2.circle(preview, (int(c["cx"]), int(c["cy"])), int(c["r"]),
                   (255, 0, 0), 2)
    for a in arcs:
        axes = (int(a["r"]), int(a["r"]))
        cv2.ellipse(preview, (int(a["cx"]), int(a["cy"])), axes, 0,
                    a["start_angle"], a["end_angle"], (0, 0, 255), 2)
    for pts in contours:
        pts_int = np.array([(int(x), int(y)) for x, y in pts], dtype=np.int32)
        cv2.polylines(preview, [pts_int], False, (0, 255, 255), 1)

    cv2.imshow("Preview — nhan phim bat ky de luu DXF", preview)
    cv2.waitKey(0)
    cv2.destroyAllWindows()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Chuyển PNG bản vẽ kỹ thuật → file DXF"
    )
    p.add_argument("input",  help="File PNG đầu vào")
    p.add_argument("output", help="File DXF đầu ra")
    p.add_argument("--scale",       type=float, default=None,
                   help="Pixels per DXF unit (mặc định 1.0)")
    p.add_argument("--min-line",    type=int,   default=None,
                   help="Độ dài line tối thiểu (px)")
    p.add_argument("--max-gap",     type=int,   default=None,
                   help="Khoảng hở tối đa Hough line (px)")
    p.add_argument("--epsilon",     type=float, default=None,
                   help="Douglas-Peucker epsilon factor (mặc định 0.01)")
    p.add_argument("--circle-p2",   type=int,   default=None,
                   help="HoughCircles param2 — độ nhạy (thấp = nhiều hơn)")
    p.add_argument("--preview",     action="store_true",
                   help="Hiện cửa sổ xem trước trước khi lưu DXF")
    p.add_argument("--deskew",      action="store_true",
                   help="Tự động chỉnh góc nghiêng ảnh")
    p.add_argument("--no-layers",   action="store_true",
                   help="Ghi tất cả entity vào layer 0")
    p.add_argument("--verbose",     action="store_true",
                   help="In số lượng entity mỗi giai đoạn")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    if args.scale is not None:
        CONFIG["pixels_per_unit"] = args.scale
    if args.min_line is not None:
        CONFIG["hough_min_line_length"] = args.min_line
    if args.max_gap is not None:
        CONFIG["hough_max_line_gap"] = args.max_gap
    if args.epsilon is not None:
        CONFIG["dp_epsilon_factor"] = args.epsilon
    if args.circle_p2 is not None:
        CONFIG["circle_param2"] = args.circle_p2
    if args.preview:
        CONFIG["show_preview"] = True
    if args.deskew:
        CONFIG["deskew"] = True
    if args.no_layers:
        CONFIG["use_layers"] = False
    if args.verbose:
        CONFIG["verbose"] = True

    print(f"Processing: {args.input}")

    img = load_image(args.input)
    H, W = img.shape[:2]
    if CONFIG["verbose"]:
        print(f"  Image size: {W}x{H} px")

    gray, binary = preprocess(img)
    if CONFIG["verbose"]:
        print("  Preprocess done")

    lines = detect_lines(binary)
    if CONFIG["verbose"]:
        print(f"  Lines: {len(lines)}")

    circles, arcs = detect_circles_and_arcs(gray, binary)
    if CONFIG["verbose"]:
        print(f"  Circles: {len(circles)}  Arcs: {len(arcs)}")

    contours = detect_contours(binary, circles, lines)
    if CONFIG["verbose"]:
        print(f"  Polylines: {len(contours)}")

    if CONFIG["show_preview"]:
        show_preview(img, lines, circles, arcs, contours)

    export_dxf(lines, circles, arcs, contours, H, args.output)
    print(f"Saved: {args.output}")
    print(f"  {len(lines)} lines | {len(circles)} circles | "
          f"{len(arcs)} arcs | {len(contours)} polylines")


if __name__ == "__main__":
    main()
