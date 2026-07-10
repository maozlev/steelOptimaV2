from pathlib import Path

import cv2
import numpy as np
from shapely.geometry import Point, Polygon

from app.extraction.vector import MIN_CUTOUT_AREA_PT2, Candidate, build_candidates

ADAPTIVE_BLOCK = 51
ADAPTIVE_C = 15
CLOSE_KERNEL = 3
# skew below this is noise; above the max it's a rotated page, not scanner skew
DESKEW_MIN_DEG = 0.3
DESKEW_MAX_DEG = 10.0
DESKEW_EST_MAX_PX = 2000
APPROX_EPS_PX = 1.5
BORDER_MARGIN_PX = 2
# pixel-fill of the min enclosing circle: disks score ~0.85+ even when heavily
# pixelated; an inscribed square scores 2/pi ~ 0.64
CIRCLE_SNAP_FILL = 0.8
CIRCLE_SNAP_SEGS = 16
# centerline crosshairs quarter a hole's interior into slivers; re-fuse
# components that are small (bbox diagonal vs page) but not dust
SLIVER_MAX_DIAG_FRAC = 0.1
SLIVER_MIN_AREA_FACTOR = 0.25
SLIVER_CLOSE_PT = 1.5
# fused quarters keep boundary notches where centerlines cross the rim, so
# roundness is judged two ways: radial consistency (share of contour points
# within tolerance of the median centroid distance — robust to a protruding
# centerline dash) or convex-hull fill of the enclosing circle. Solidity
# rejects sparse glyph clusters whose hull happens to look round.
SLIVER_MIN_SOLIDITY = 0.6
SLIVER_RADIUS_TOL = 0.15
SLIVER_MIN_INLIERS = 0.79
# ink blobs smaller than this fraction of the page are annotations/text, not
# part silhouettes — without the gate every glyph outline becomes a shell and
# letter counters gain parents (= false cutouts)
PART_SHELL_MIN_AREA_FRAC = 0.005


def _load_ink(render_path: Path) -> np.ndarray:
    # np.fromfile + imdecode: cv2.imread chokes on non-ASCII Windows paths
    data = np.fromfile(str(render_path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"cannot decode image: {render_path}")
    ink = cv2.adaptiveThreshold(
        img,
        255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY_INV,
        ADAPTIVE_BLOCK,
        ADAPTIVE_C,
    )
    kernel = np.ones((CLOSE_KERNEL, CLOSE_KERNEL), np.uint8)
    return cv2.morphologyEx(ink, cv2.MORPH_CLOSE, kernel)


def _skew_angle(ink: np.ndarray) -> float:
    scale = min(1.0, DESKEW_EST_MAX_PX / max(ink.shape))
    small = (
        cv2.resize(ink, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        if scale < 1.0
        else ink
    )
    pts = cv2.findNonZero(small)
    if pts is None:
        return 0.0
    angle = cv2.minAreaRect(pts)[2]
    if angle > 45:
        angle -= 90
    return angle


def extract_raster_candidates(
    render_path: Path, dpi: int, page_area_pt2: float
) -> list[Candidate]:
    """Enclosed white regions in the binarized render become cutout candidates.

    Mirrors the vector pipeline's planar-face idea: ink strokes partition the
    page into connected white components; every component not touching the
    border is a closed face (part interior, hole, or annotation box), and
    build_candidates() sorts out the hierarchy.
    """
    ink = _load_ink(render_path)
    h, w = ink.shape

    inv_m = None
    angle = _skew_angle(ink)
    if DESKEW_MIN_DEG <= abs(angle) <= DESKEW_MAX_DEG:
        m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        ink = cv2.warpAffine(
            ink, m, (w, h), flags=cv2.INTER_NEAREST, borderValue=0
        )
        inv_m = cv2.invertAffineTransform(m)

    free = cv2.bitwise_not(ink)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(free, connectivity=4)

    px_per_pt = dpi / 72
    min_area_px = MIN_CUTOUT_AREA_PT2 * px_per_pt**2

    # open to the page edge: background, not an enclosed face
    border_ids = {
        i
        for i in range(1, n)
        if stats[i, 0] <= BORDER_MARGIN_PX
        or stats[i, 1] <= BORDER_MARGIN_PX
        or stats[i, 0] + stats[i, 2] >= w - BORDER_MARGIN_PX
        or stats[i, 1] + stats[i, 3] >= h - BORDER_MARGIN_PX
    }

    tagged: list[tuple[Polygon, bool]] = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < min_area_px or i in border_ids:
            continue
        mask = (labels[y : y + bh, x : x + bw] == i).astype(np.uint8)
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)

        # snap pixelated disks to ideal circles: at low effective DPI a small
        # hole's jagged contour misses the circularity threshold in _classify.
        # contourArea (not the component's pixel count) so ring-shaped holes —
        # drawn as two concentric circles — still read as full disks
        (ccx, ccy), r = cv2.minEnclosingCircle(contour)
        fill_area = cv2.contourArea(contour)
        if r > 0 and fill_area / (np.pi * r * r) >= CIRCLE_SNAP_FILL:
            center = np.array([ccx + x, ccy + y], dtype=np.float64)
            if inv_m is not None:
                center = center @ inv_m[:, :2].T + inv_m[:, 2]
            r_fit = float(np.sqrt(fill_area / np.pi))  # less biased than enclosing r
            poly = Point(center / px_per_pt).buffer(
                r_fit / px_per_pt, quad_segs=CIRCLE_SNAP_SEGS
            )
            if poly.area >= MIN_CUTOUT_AREA_PT2:
                tagged.append((poly, False))
            continue

        contour = cv2.approxPolyDP(contour, APPROX_EPS_PX, True)
        if len(contour) < 3:
            continue
        pts = contour.reshape(-1, 2).astype(np.float64) + (x, y)
        if inv_m is not None:
            pts = pts @ inv_m[:, :2].T + inv_m[:, 2]
        try:
            poly = Polygon(pts / px_per_pt)
        except Exception:
            continue
        if poly.is_valid and poly.area >= MIN_CUTOUT_AREA_PT2:
            tagged.append((poly, False))

    # part-outline shells: everything that is not background (ink strokes plus
    # the enclosed white faces) forms the part silhouette. Without these outer
    # shells, enclosed candidates have no parent in build_candidates and are
    # dropped as "part outlines" themselves.
    part_mask = np.where(np.isin(labels, list(border_ids)), 0, 255).astype(np.uint8)
    part_contours, _ = cv2.findContours(
        part_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    shell_min_px = PART_SHELL_MIN_AREA_FRAC * page_area_pt2 * px_per_pt**2
    for contour in part_contours:
        if cv2.contourArea(contour) < shell_min_px:
            continue
        contour = cv2.approxPolyDP(contour, APPROX_EPS_PX, True)
        if len(contour) < 3:
            continue
        pts = contour.reshape(-1, 2).astype(np.float64)
        if inv_m is not None:
            pts = pts @ inv_m[:, :2].T + inv_m[:, 2]
        try:
            poly = Polygon(pts / px_per_pt)
            if not poly.is_valid:
                # thin lines attached to the silhouette trace out-and-back
                # spikes that make the ring self-touching; buffer(0) repairs
                poly = poly.buffer(0)
                if poly.geom_type == "MultiPolygon":
                    poly = max(poly.geoms, key=lambda g: g.area)
        except Exception:
            continue
        if poly.is_valid and poly.area >= MIN_CUTOUT_AREA_PT2:
            tagged.append((poly, False))

    # sliver re-fusion: centerline crosshairs cut a hole's interior into
    # quarter slivers, each too jagged to classify. Morph-close the small
    # non-border components together and snap round unions back to circles.
    max_diag_sq = (SLIVER_MAX_DIAG_FRAC * min(w, h)) ** 2
    sliver_ids = [
        i
        for i in range(1, n)
        if i not in border_ids
        and stats[i, 4] >= min_area_px * SLIVER_MIN_AREA_FACTOR
        and stats[i, 2] ** 2 + stats[i, 3] ** 2 <= max_diag_sq
    ]
    if sliver_ids:
        sliver_mask = (np.isin(labels, sliver_ids).astype(np.uint8)) * 255
        k = max(3, int(round(SLIVER_CLOSE_PT * px_per_pt)) | 1)
        closed = cv2.morphologyEx(
            sliver_mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8)
        )
        fuse_contours, _ = cv2.findContours(
            closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
        )
        merged: list[Polygon] = []
        for contour in fuse_contours:
            moments = cv2.moments(contour)
            ca = cv2.contourArea(contour)
            hull_area = cv2.contourArea(cv2.convexHull(contour))
            if moments["m00"] == 0 or hull_area <= 0:
                continue
            if ca / hull_area < SLIVER_MIN_SOLIDITY:
                continue
            ctr = np.array(
                [moments["m10"] / moments["m00"], moments["m01"] / moments["m00"]]
            )
            pts = contour.reshape(-1, 2).astype(np.float64)
            dists = np.hypot(*(pts - ctr).T)
            r_med = float(np.median(dists))
            if r_med <= 0:
                continue
            inliers = float(
                np.mean(np.abs(dists - r_med) <= SLIVER_RADIUS_TOL * r_med)
            )
            (_, _), r_enc = cv2.minEnclosingCircle(contour)
            hull_fill = hull_area / (np.pi * r_enc * r_enc) if r_enc else 0.0
            if inliers < SLIVER_MIN_INLIERS and hull_fill < CIRCLE_SNAP_FILL:
                continue
            if inv_m is not None:
                ctr = ctr @ inv_m[:, :2].T + inv_m[:, 2]
            poly = Point(ctr / px_per_pt).buffer(
                r_med / px_per_pt, quad_segs=CIRCLE_SNAP_SEGS
            )
            if poly.area >= MIN_CUTOUT_AREA_PT2:
                merged.append(poly)
        if merged:
            tagged = [
                (p, fl)
                for p, fl in tagged
                if not any(
                    mc.intersects(p)
                    and p.intersection(mc).area >= 0.9 * p.area
                    for mc in merged
                )
            ]
            tagged.extend((mc, False) for mc in merged)

    return build_candidates(tagged, page_area_pt2, [], source="raster_cv")
