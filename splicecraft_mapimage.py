"""Circular plasmid-map image export (layer 1) — SVG + PNG.

A pure, app-free renderer that turns a plasmid (a normalised feature list
+ length, or a ``SeqRecord``) into a publication-quality circular map:

* the backbone circle,
* colour-coded feature arcs (arrowheads for stranded features), stacked
  onto concentric lanes when they overlap,
* feature labels in outer left/right columns with leader lines,
* restriction-site ticks + enzyme names just outside the backbone,
* a centre block carrying the plasmid name + bp count.

Two rendering back-ends consume ONE shared geometry pass
(:func:`_build_primitives`):

* :func:`render_plasmid_map_svg` — pure-stdlib string emission. True
  vector, infinitely scalable, and a transparent background is just the
  absence of the backing rect — ideal for figure editing.
* :func:`render_plasmid_map_png` — rasterised with Pillow (a hard
  dependency of the app). Drawn at a super-sampled resolution and
  down-scaled with LANCZOS so the aliased arcs/lines come out smooth.

The module is deliberately decoupled from ``PlasmidApp``: the live map
hands us its already-parsed ``pm._feats`` / ``pm._restr_feats`` so the
export matches the screen exactly, while :func:`_map_feats_from_record`
re-derives the same shape from a bare record for bulk/off-screen export.

Layer 1: imports biology / util / logging (L0) only — NOT the hub.
"""
from __future__ import annotations

import math
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

import splicecraft_biology as _bio
from splicecraft_logging import _log
from splicecraft_util import _feat_label_full, _sanitize_label

# ── Tunables ─────────────────────────────────────────────────────────────────

_MAP_IMAGE_DEFAULT_SIZE = 1400   # px, square canvas (SVG viewBox units too)
_MAP_IMAGE_MIN_SIZE     = 300
_MAP_IMAGE_MAX_SIZE     = 6000   # guard against a runaway allocation
_PNG_SUPERSAMPLE        = 2      # draw at N× then LANCZOS-downscale for AA

# Feature-label / site-label caps keep dense plasmids legible instead of a
# hairball of overlapping text. Anything beyond the cap is dropped and logged
# (never silently truncated — the caller surfaces the count).
_MAP_MAX_FEATURE_LABELS = 60
_MAP_MAX_SITE_TICKS     = 60
_MAP_MAX_LANES          = 14   # bounds lane assignment on pathological plasmids
_LABEL_MAX_CHARS        = 18

# Radii + thicknesses as a fraction of the canvas side. Features stack
# INWARD from the backbone; labels fan OUTWARD into fixed left/right
# columns; sites sit just outside. The circle is kept modest (0.26) so the
# label columns have room to hold text WITHOUT clipping the canvas edge.
_R_BACKBONE   = 0.260
_BAND         = 0.050   # feature band thickness
_LANE_STEP    = 0.062   # inward radius drop per stacked lane
_R_MIN_LANE   = 0.100   # innermost a feature band may reach (protect centre)
_ARROW_HEAD   = 0.020   # arrowhead angular reach, radians (clamped to arc)
_SITE_TICK    = 0.024   # pink tick length outside the backbone
_R_SITE_LABEL = 0.300   # radius the enzyme name sits at (inner zone)
_R_LEADER_OUT = 0.380   # feature label's vertical target = cy + this·sin(θ)
_LABEL_COL_X  = 0.335   # |x-cx|/S of the outer label columns (outside site ring)
_SITE_LABEL_MIN_GAP = 0.16   # radians; suppress an enzyme label this close to
                             # the previous one so clustered cutters don't pile up

_BACKBONE_COLOR = "#5A6472"
_SITE_COLOR     = "#FF69B4"   # mirrors hub _RESTR_SITE_FEATURE_COLOR
_TITLE_COLOR    = "#1A1D24"
_SUBTITLE_COLOR = "#5A6472"
_BG_COLOR       = "#FFFFFF"

# Mirror of splicecraft_widgets._FEATURE_PALETTE (xterm-256 indices). Kept
# inline so the pure renderer needn't import the Textual widget layer; a
# test (test_mapimage.py::test_palette_matches_widgets) guards against drift.
_FALLBACK_PALETTE: list[str] = [
    "color(39)",   "color(118)",  "color(208)",  "color(213)",  "color(51)",
    "color(220)",  "color(196)",  "color(46)",   "color(201)",  "color(129)",
    "color(166)",  "color(33)",   "color(226)",  "color(160)",  "color(87)",
    "color(105)",  "color(154)",  "color(203)",  "color(81)",   "color(185)",
]

# Export formats this module handles: fmt -> (extension, human label).
_MAP_EXPORT_FORMATS: dict = {
    "png": (".png", "PNG image"),
    "svg": (".svg", "SVG vector"),
}


# ── Colour handling ──────────────────────────────────────────────────────────

_NAMED_RGB = {
    "black": (0, 0, 0), "white": (255, 255, 255), "red": (255, 0, 0),
    "green": (0, 128, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0),
    "cyan": (0, 255, 255), "magenta": (255, 0, 255), "gray": (128, 128, 128),
    "grey": (128, 128, 128), "orange": (255, 165, 0), "purple": (128, 0, 128),
    "pink": (255, 105, 180), "brown": (165, 42, 42), "navy": (0, 0, 128),
}


def _xterm_to_rgb(n: int) -> "tuple[int, int, int]":
    """Convert an xterm-256 palette index to a 24-bit RGB tuple (standard
    16 system colours + 6×6×6 cube + 24-step grayscale ramp)."""
    n = max(0, min(255, int(n)))
    if n < 16:
        base = [
            (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
            (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192),
            (128, 128, 128), (255, 0, 0), (0, 255, 0), (255, 255, 0),
            (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
        ]
        return base[n]
    if n < 232:
        n -= 16
        r, g, b = n // 36, (n % 36) // 6, n % 6
        conv = lambda v: 0 if v == 0 else 40 * v + 55
        return conv(r), conv(g), conv(b)
    v = 8 + (n - 232) * 10
    return v, v, v


def _to_rgb(color: "str | None") -> "tuple[int, int, int]":
    """Best-effort conversion of any SpliceCraft colour token to RGB.

    Accepts ``#RGB`` / ``#RRGGBB`` / ``color(N)`` / bare xterm index /
    ``rgb(r,g,b)`` / a small set of CSS names. Falls back to a neutral
    grey so a malformed colour never aborts a render."""
    if not isinstance(color, str):
        return (150, 150, 150)
    c = color.strip().lower()
    if not c:
        return (150, 150, 150)
    if c in _NAMED_RGB:
        return _NAMED_RGB[c]
    if c.startswith("#"):
        h = c[1:]
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        if len(h) == 6:
            try:
                return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            except ValueError:
                return (150, 150, 150)
        return (150, 150, 150)
    if c.startswith("rgb(") and c.endswith(")"):
        try:
            parts = [int(p) for p in c[4:-1].split(",")]
            if len(parts) == 3:
                return tuple(max(0, min(255, p)) for p in parts)  # type: ignore[return-value]
        except ValueError:
            return (150, 150, 150)
    if c.startswith("color(") and c.endswith(")"):
        try:
            return _xterm_to_rgb(int(c[6:-1]))
        except ValueError:
            return (150, 150, 150)
    if c.isdigit():
        return _xterm_to_rgb(int(c))
    return (150, 150, 150)


def _hex(rgb: "tuple[int, int, int]") -> str:
    return "#%02X%02X%02X" % rgb


def _luminance(rgb: "tuple[int, int, int]") -> float:
    r, g, b = (v / 255.0 for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ensure_readable(rgb: "tuple[int, int, int]") -> "tuple[int, int, int]":
    """Darken a very-light colour so label text stays legible on white.
    Leaves already-readable colours untouched."""
    if _luminance(rgb) <= 0.62:
        return rgb
    return tuple(int(v * 0.55) for v in rgb)  # type: ignore[return-value]


# ── Geometry ─────────────────────────────────────────────────────────────────

def _angle(bp: float, total: int, origin_bp: int) -> float:
    """bp → radians, matching the on-screen map: origin at the top of the
    circle (−π/2), increasing bp sweeps clockwise."""
    if total <= 0:
        return -math.pi / 2
    return 2 * math.pi * ((bp - origin_bp) % total) / total - math.pi / 2


def _in_arc(x: float, start: float, length: float, total: int) -> bool:
    """Does bp ``x`` fall inside the circular arc ``[start, start+length)``
    on a plasmid of ``total`` bp? Wrap-safe."""
    if total <= 0:
        return False
    return ((x - start) % total) < length


def _assign_lanes(feats: list, total: int) -> "dict[int, int]":
    """Greedy circular-arc colouring: map each feature index to a lane so
    overlapping arcs stack onto separate concentric rings. Returns
    ``{feat_index: lane}`` (lane 0 = on the backbone, higher = inward).

    Longest arcs are placed first so the dominant features own the outer
    lanes — matches how the eye reads a plasmid map."""
    order = sorted(
        range(len(feats)),
        key=lambda i: _bio._feat_len(feats[i]["start"], feats[i]["end"], total),
        reverse=True,
    )
    lanes: list[list[tuple[int, int]]] = []   # per lane: list of (start, arclen)
    result: dict[int, int] = {}
    for i in order:
        f = feats[i]
        s = int(f["start"])
        ln = _bio._feat_len(s, f["end"], total)
        placed = False
        for lane_idx, occ in enumerate(lanes):
            if all(
                not (_in_arc(s, os_, ol, total)
                     or _in_arc(os_, s, ln, total))
                for os_, ol in occ
            ):
                occ.append((s, ln))
                result[i] = lane_idx
                placed = True
                break
        if not placed:
            if len(lanes) >= _MAP_MAX_LANES:
                # Cap the ring count: a plasmid with a huge overlapping-feature
                # pile-up would otherwise spawn unbounded (invisibly thin) lanes
                # and make lane assignment O(F²). Stack the overflow onto the
                # innermost lane (accepting some visual overlap) instead.
                lanes[-1].append((s, ln))
                result[i] = len(lanes) - 1
            else:
                lanes.append([(s, ln)])
                result[i] = len(lanes) - 1
    return result


def _lane_radius(lane: int, size: float) -> float:
    """Centre radius (px) of feature ``lane``, clamped so deep stacks don't
    collide with the centre title block."""
    r = (_R_BACKBONE - lane * _LANE_STEP) * size
    return max(r, _R_MIN_LANE * size)


def _arc_points(cx: float, cy: float, r: float,
                a0: float, a1: float, steps: int) -> list:
    """Sample a circular arc from angle ``a0`` to ``a1`` (a1 ≥ a0) into
    ``steps``+1 points at radius ``r``."""
    if steps < 1:
        steps = 1
    out = []
    for k in range(steps + 1):
        a = a0 + (a1 - a0) * (k / steps)
        out.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return out


def _band_polygon(cx: float, cy: float, r_mid: float, band: float,
                  a0: float, a1: float, strand: int) -> list:
    """Closed polygon (list of (x,y)) for a feature arc band from angle
    ``a0`` to ``a1`` at centre radius ``r_mid``, thickness ``band``. A
    stranded feature grows an arrowhead at its leading end."""
    r_out = r_mid + band / 2.0
    r_in = r_mid - band / 2.0
    span = a1 - a0
    steps = max(2, int(span / (2 * math.pi) * 240))
    head = min(_ARROW_HEAD, span * 0.5) if strand else 0.0

    if strand < 0:
        # Arrowhead at the low-bp (a0) end, pointing counter-clockwise.
        tip = (cx + r_mid * math.cos(a0), cy + r_mid * math.sin(a0))
        body_out = _arc_points(cx, cy, r_out, a0 + head, a1, steps)
        body_in = _arc_points(cx, cy, r_in, a0 + head, a1, steps)
        pts = [tip] + body_out + list(reversed(body_in))
        return pts
    if strand > 0:
        # Arrowhead at the high-bp (a1) end, pointing clockwise.
        tip = (cx + r_mid * math.cos(a1), cy + r_mid * math.sin(a1))
        body_out = _arc_points(cx, cy, r_out, a0, a1 - head, steps)
        body_in = _arc_points(cx, cy, r_in, a0, a1 - head, steps)
        pts = body_out + [tip] + list(reversed(body_in))
        return pts
    # Unstranded — a blunt band.
    body_out = _arc_points(cx, cy, r_out, a0, a1, steps)
    body_in = _arc_points(cx, cy, r_in, a0, a1, steps)
    return body_out + list(reversed(body_in))


def _spread_labels(items: list, min_gap: float, lo: float, hi: float) -> None:
    """In-place vertical de-collision. ``items`` is a list of dicts each
    carrying a ``y`` (desired) — nudged apart so neighbours are ≥
    ``min_gap`` apart, kept within [lo, hi]. Sorted by y first."""
    if not items:
        return
    items.sort(key=lambda d: d["y"])
    # Forward pass: push down.
    for i in range(1, len(items)):
        if items[i]["y"] - items[i - 1]["y"] < min_gap:
            items[i]["y"] = items[i - 1]["y"] + min_gap
    # If we overflowed the bottom, shift the whole stack up and clamp.
    overflow = items[-1]["y"] - hi
    if overflow > 0:
        for d in items:
            d["y"] -= overflow
    if items[0]["y"] < lo:
        shift = lo - items[0]["y"]
        for d in items:
            d["y"] += shift


# ── Primitive model (shared by both back-ends) ───────────────────────────────

def _fmt_bp(n: int) -> str:
    return f"{int(n):,} bp"


def _build_primitives(feats: list, total: int, *,
                      title: str = "", sites: "list | None" = None,
                      size: int = _MAP_IMAGE_DEFAULT_SIZE,
                      origin_bp: int = 0,
                      show_labels: bool = True,
                      show_sites: bool = True) -> dict:
    """Turn the plasmid data into back-end-agnostic drawing primitives.

    Returns a dict with keys ``size``, ``circles``, ``polys``, ``lines``,
    ``texts`` — each a list of tuples the SVG/PNG emitters iterate. Kept
    pure so it is trivially unit-testable without any raster/vector lib.
    """
    s = float(size)
    cx = cy = s / 2.0
    circles: list = []
    polys: list = []
    lines: list = []
    texts: list = []

    # Backbone.
    circles.append((cx, cy, _R_BACKBONE * s, _BACKBONE_COLOR,
                    max(1.5, s * 0.0022), None))

    if total > 0 and feats:
        lanes = _assign_lanes(feats, total)
        # Feature label candidates by side, gathered then de-collided.
        left: list = []
        right: list = []
        for i, f in enumerate(feats):
            start = int(f["start"])
            arclen = _bio._feat_len(start, f["end"], total)
            if arclen <= 0:
                continue
            a0 = _angle(start, total, origin_bp)
            a1 = a0 + 2 * math.pi * (arclen / total)
            lane = lanes.get(i, 0)
            r_mid = _lane_radius(lane, s)
            strand = int(f.get("strand") or 0)
            rgb = _to_rgb(f.get("color"))
            poly = _band_polygon(cx, cy, r_mid, _BAND * s, a0, a1, strand)
            polys.append((poly, _hex(rgb), "#00000022", max(0.5, s * 0.0009)))

            if not show_labels:
                continue
            label = _sanitize_label(str(f.get("label") or f.get("type") or ""),
                                    max_len=_LABEL_MAX_CHARS)
            if not label:
                continue
            a_mid = a0 + (a1 - a0) / 2.0
            anchor_r = r_mid + (_BAND / 2.0) * s
            ax = cx + anchor_r * math.cos(a_mid)
            ay = cy + anchor_r * math.sin(a_mid)
            side = right if math.cos(a_mid) >= 0 else left
            side.append({
                "y": cy + (_R_LEADER_OUT * s) * math.sin(a_mid),
                "ax": ax, "ay": ay,
                "label": label, "color": _hex(_ensure_readable(rgb)),
            })

        if show_labels:
            _place_side_labels(right, +1, cx, cy, s, lines, texts)
            _place_side_labels(left, -1, cx, cy, s, lines, texts)

    # Restriction ticks + enzyme names. Ticks are drawn for every (deduped)
    # cut; the NAME labels are angularly de-collided so a cluster of cutters
    # doesn't stack into an unreadable pile.
    if show_sites and sites and total > 0:
        seen: set = set()
        uniq: list = []
        for rf in sites:
            # Ticks mark CUT positions. A live map hands us its full
            # `_restr_feats` (recognition-span "resite" dicts + single-bp
            # "recut" dicts); keep only recuts. A pre-filtered list (no
            # `type` key) is taken as-is.
            if rf.get("type") not in (None, "recut"):
                continue
            enzyme = _sanitize_label(str(rf.get("label") or ""), max_len=18)
            pos = int(rf.get("start", 0))
            key = (enzyme, pos)
            if not enzyme or key in seen:
                continue
            seen.add(key)
            uniq.append((_angle(pos, total, origin_bp), enzyme))
            if len(uniq) >= _MAP_MAX_SITE_TICKS:
                break
        uniq.sort(key=lambda t: t[0])
        last_label_a = -10.0
        suppressed = 0
        for a, enzyme in uniq:
            x0 = cx + _R_BACKBONE * s * math.cos(a)
            y0 = cy + _R_BACKBONE * s * math.sin(a)
            x1 = cx + (_R_BACKBONE * s + _SITE_TICK * s) * math.cos(a)
            y1 = cy + (_R_BACKBONE * s + _SITE_TICK * s) * math.sin(a)
            lines.append((x0, y0, x1, y1, _SITE_COLOR, max(1.0, s * 0.0015)))
            if a - last_label_a < _SITE_LABEL_MIN_GAP:
                suppressed += 1
                continue
            last_label_a = a
            lx = cx + _R_SITE_LABEL * s * math.cos(a)
            ly = cy + _R_SITE_LABEL * s * math.sin(a)
            texts.append((lx, ly, enzyme, s * 0.016, _SITE_COLOR,
                          "middle", "normal"))
        if suppressed:
            _log.info("map image: %d clustered enzyme labels suppressed "
                      "(ticks still drawn)", suppressed)

    # Centre block: name + bp.
    if title:
        texts.append((cx, cy - s * 0.018,
                      _sanitize_label(title, max_len=40),
                      s * 0.032, _TITLE_COLOR, "middle", "bold"))
    if total > 0:
        texts.append((cx, cy + s * 0.028, _fmt_bp(total),
                      s * 0.022, _SUBTITLE_COLOR, "middle", "normal"))

    return {"size": size, "circles": circles, "polys": polys,
            "lines": lines, "texts": texts}


def _place_side_labels(side: list, direction: int, cx: float, cy: float,
                       s: float, lines: list, texts: list) -> None:
    """De-collide one side's labels vertically, then emit each as a leader
    line (feature edge → column) plus text. ``direction`` +1 = right."""
    if not side:
        return
    over = 0
    if len(side) > _MAP_MAX_FEATURE_LABELS:
        over = len(side) - _MAP_MAX_FEATURE_LABELS
        # Keep the outermost (longest-arc) features' labels; drop the rest.
        side = side[:_MAP_MAX_FEATURE_LABELS]
    lh = s * 0.026
    _spread_labels(side, lh, s * 0.06, s * 0.94)
    col_x = cx + direction * _LABEL_COL_X * s
    anchor = "start" if direction > 0 else "end"
    tick = direction * s * 0.012
    for d in side:
        ly = d["y"]
        # Leader: feature anchor → just before the text column.
        lines.append((d["ax"], d["ay"], col_x - tick, ly,
                      d["color"], max(0.6, s * 0.0011)))
        texts.append((col_x, ly, d["label"], s * 0.018, d["color"],
                      anchor, "normal"))
    if over > 0:
        _log.info("map image: %d feature labels dropped (cap %d)",
                  over, _MAP_MAX_FEATURE_LABELS)


# ── SVG back-end ─────────────────────────────────────────────────────────────

_SVG_FONT = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
             "'Helvetica Neue', Arial, sans-serif")


def render_plasmid_map_svg(feats: list, total: int, *, title: str = "",
                           sites: "list | None" = None,
                           size: int = _MAP_IMAGE_DEFAULT_SIZE,
                           origin_bp: int = 0, transparent: bool = False,
                           show_labels: bool = True,
                           show_sites: bool = True) -> str:
    """Render the plasmid map as an SVG document string (pure stdlib)."""
    size = _clamp_size(size)
    prims = _build_primitives(
        feats, total, title=title, sites=sites, size=size,
        origin_bp=origin_bp, show_labels=show_labels, show_sites=show_sites,
    )
    parts: list = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" '
        f'height="{size}" viewBox="0 0 {size} {size}" '
        f'font-family="{_xml_escape(_SVG_FONT)}">'
    ]
    if not transparent:
        parts.append(f'<rect width="{size}" height="{size}" fill="{_BG_COLOR}"/>')
    for cx, cy, r, stroke, w, fill in prims["circles"]:
        parts.append(
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" '
            f'fill="{fill or "none"}" stroke="{stroke}" '
            f'stroke-width="{w:.2f}"/>'
        )
    for poly, fill, stroke, w in prims["polys"]:
        pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in poly)
        parts.append(
            f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{w:.2f}" stroke-linejoin="round"/>'
        )
    for x0, y0, x1, y1, color, w in prims["lines"]:
        parts.append(
            f'<line x1="{x0:.2f}" y1="{y0:.2f}" x2="{x1:.2f}" y2="{y1:.2f}" '
            f'stroke="{color}" stroke-width="{w:.2f}"/>'
        )
    for x, y, text, fs, color, anchor, weight in prims["texts"]:
        parts.append(
            f'<text x="{x:.2f}" y="{y:.2f}" font-size="{fs:.2f}" '
            f'fill="{color}" text-anchor="{anchor}" '
            f'dominant-baseline="central" '
            f'font-weight="{weight}">{_xml_escape(text)}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


# ── PNG back-end (Pillow) ────────────────────────────────────────────────────

def _load_font(px: int):
    """A TrueType font at ``px`` pixels. Prefers DejaVu (ships with Pillow),
    then Pillow's scalable default; never raises."""
    from PIL import ImageFont
    px = max(6, int(px))
    for name in ("DejaVuSans.ttf", "Arial.ttf", "Helvetica.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=px)
    except TypeError:      # Pillow < 10 — non-scalable bitmap default
        return ImageFont.load_default()


_PIL_ANCHOR = {"start": "lm", "middle": "mm", "end": "rm"}


def render_plasmid_map_png(feats: list, total: int, *, title: str = "",
                           sites: "list | None" = None,
                           size: int = _MAP_IMAGE_DEFAULT_SIZE,
                           origin_bp: int = 0, transparent: bool = False,
                           show_labels: bool = True,
                           show_sites: bool = True,
                           supersample: int = _PNG_SUPERSAMPLE) -> bytes:
    """Render the plasmid map as PNG bytes via Pillow. Drawn at
    ``supersample``× then LANCZOS-downscaled so arcs/lines are anti-aliased."""
    from io import BytesIO
    from PIL import Image, ImageDraw
    size = _clamp_size(size)
    ss = max(1, min(4, int(supersample)))
    big = size * ss
    prims = _build_primitives(
        feats, total, title=title, sites=sites, size=big,
        origin_bp=origin_bp, show_labels=show_labels, show_sites=show_sites,
    )
    bg = (255, 255, 255, 0) if transparent else (255, 255, 255, 255)
    img = Image.new("RGBA", (big, big), bg)
    draw = ImageDraw.Draw(img)

    for cx, cy, r, stroke, w, _fill in prims["circles"]:
        draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                     outline=_to_rgb(stroke), width=max(1, int(round(w))))
    for poly, fill, stroke, w in prims["polys"]:
        draw.polygon(poly, fill=_to_rgb(fill),
                     outline=(0, 0, 0), width=max(1, int(round(w))))
    for x0, y0, x1, y1, color, w in prims["lines"]:
        draw.line([x0, y0, x1, y1], fill=_to_rgb(color),
                  width=max(1, int(round(w))))
    _font_cache: dict = {}
    for x, y, text, fs, color, anchor, weight in prims["texts"]:
        key = int(fs)
        font = _font_cache.get(key)
        if font is None:
            font = _load_font(key)
            _font_cache[key] = font
        rgb = _to_rgb(color)
        pa = _PIL_ANCHOR.get(anchor, "lm")
        try:
            draw.text((x, y), text, font=font, fill=rgb, anchor=pa)
            if weight == "bold":   # fake-bold: 1px over-stamp at supersample
                draw.text((x + ss, y), text, font=font, fill=rgb, anchor=pa)
        except (ValueError, OSError):
            # Anchored draw can reject certain default-font/anchor combos on
            # old Pillow — fall back to a top-left placement.
            draw.text((x, y), text, font=font, fill=rgb)

    if ss > 1:
        img = img.resize((size, size), Image.Resampling.LANCZOS)
    if not transparent:
        img = img.convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Record → data extractors (for off-screen / bulk export) ──────────────────

def _feat_bounds(feat, total: int) -> "tuple[int, int] | None":
    """Wrap-aware (start, end) for a Biopython SeqFeature, or None if it
    can't be resolved. ``end`` may be < ``start`` (origin-crossing)."""
    try:
        loc = feat.location
        if loc is None:
            return None
        parts = list(getattr(loc, "parts", []) or [loc])
        start = int(parts[0].start)
        end = int(parts[-1].end)
    except (TypeError, ValueError, AttributeError, IndexError):
        return None
    if total > 0:
        start %= total
        # Don't fold a full-length feature's end (== total) down to 0 — that
        # would zero its arc length and drop it. Only wrap ends past the origin.
        if end > total:
            end %= total
    return start, end


def _map_feats_from_record(record) -> "tuple[list[dict], int]":
    """Re-derive the map feature list (same shape as ``PlasmidMap._feats``)
    from a bare ``SeqRecord`` — for exporting entries that aren't the
    currently-loaded plasmid. Colours honour a feature's stored
    ``ApEinfo_fwdcolor`` / ``color`` qualifier, else cycle the palette."""
    if record is None:
        return [], 0
    total = len(getattr(record, "seq", "") or "")
    feats: list[dict] = []
    skip = {"source"}
    idx = 0
    for feat in getattr(record, "features", []) or []:
        if getattr(feat, "type", "") in skip:
            continue
        bounds = _feat_bounds(feat, total)
        if bounds is None:
            continue
        start, end = bounds
        strand = getattr(getattr(feat, "location", None), "strand", None)
        color = ""
        for q in ("ApEinfo_fwdcolor", "ApEinfo_revcolor", "color"):
            vals = feat.qualifiers.get(q, []) if hasattr(feat, "qualifiers") else []
            if vals:
                cand = str(vals[0]).strip()
                if cand.startswith("#") and len(cand) in (4, 7):
                    color = cand
                    break
        feats.append({
            "type": getattr(feat, "type", "misc"),
            "start": start, "end": end,
            "strand": 1 if strand is None else int(strand),
            "color": color or _FALLBACK_PALETTE[idx % len(_FALLBACK_PALETTE)],
            "label": _feat_label_full(feat),
        })
        idx += 1
    return feats, total


def _map_sites_from_record(record, *, allowed_enzymes=None,
                           unique_only: bool = True) -> "list[dict]":
    """Restriction cut sites (recut dicts: ``{start, label}``) for a record,
    for the outer tick ring. Defensive: an unbuilt scan catalog or a bad
    sequence yields ``[]`` rather than raising."""
    seq = str(getattr(record, "seq", "") or "")
    if not seq:
        return []
    try:
        hits = _bio._scan_restriction_sites(
            seq, unique_only=unique_only, circular=True,
            allowed_enzymes=allowed_enzymes,
        )
    except Exception:      # scan catalog may be unbuilt off-hub; never fail export
        _log.debug("map image: restriction scan unavailable", exc_info=True)
        return []
    return [h for h in hits if h.get("type") == "recut"]


# ── High-level file export ───────────────────────────────────────────────────

def _clamp_size(size: int) -> int:
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = _MAP_IMAGE_DEFAULT_SIZE
    return max(_MAP_IMAGE_MIN_SIZE, min(_MAP_IMAGE_MAX_SIZE, size))


def render_map_bytes(feats: list, total: int, *, fmt: str, **kw) -> bytes:
    """Render to encoded bytes in ``fmt`` ('png' or 'svg')."""
    fmt = (fmt or "png").lower()
    if fmt == "svg":
        return render_plasmid_map_svg(feats, total, **kw).encode("utf-8")
    if fmt == "png":
        return render_plasmid_map_png(feats, total, **kw)
    raise ValueError(f"unknown map image format {fmt!r}; "
                     f"choose one of {sorted(_MAP_EXPORT_FORMATS)}")


def export_plasmid_map(out_path, *, feats: "list | None" = None,
                       total: "int | None" = None, record=None,
                       fmt: str = "png", title: str = "",
                       sites: "list | None" = None,
                       size: int = _MAP_IMAGE_DEFAULT_SIZE,
                       origin_bp: int = 0, transparent: bool = False,
                       show_labels: bool = True,
                       show_sites: bool = True) -> dict:
    """Render a plasmid map and atomically write it to ``out_path``.

    Supply EITHER an already-parsed (``feats``, ``total``) pair (the live
    map hands us ``pm._feats`` / ``pm._total`` for on-screen fidelity) OR a
    ``record`` (re-derived via :func:`_map_feats_from_record`). Returns a
    summary ``{path, fmt, bp, features, bytes}``.

    Writes go to a user-chosen path OUTSIDE the sacred data dir (exactly
    like the GenBank/FASTA exporters), so no chokepoint applies — but the
    write is still atomic (temp file + fsync + replace) to avoid a
    half-written image on a crash/full-disk."""
    fmt = (fmt or "png").lower()
    if fmt not in _MAP_EXPORT_FORMATS:
        raise ValueError(f"unknown map image format {fmt!r}; "
                         f"choose one of {sorted(_MAP_EXPORT_FORMATS)}")
    if feats is None or total is None:
        if record is None:
            raise ValueError("export_plasmid_map: pass feats+total or record")
        feats, total = _map_feats_from_record(record)
        if show_sites and sites is None:
            sites = _map_sites_from_record(record)
    feats = feats or []
    total = int(total or 0)

    data = render_map_bytes(
        feats, total, fmt=fmt, title=title, sites=sites, size=size,
        origin_bp=origin_bp, transparent=transparent,
        show_labels=show_labels, show_sites=show_sites,
    )
    out = Path(out_path).expanduser()
    _atomic_write_bytes(out, data)
    _log.info("Exported plasmid map → %s (%s, %d bp, %d feats, %d bytes)",
              out, fmt, total, len(feats), len(data))
    return {"path": str(out), "fmt": fmt, "bp": total,
            "features": len(feats), "bytes": len(data)}


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Temp-file + fsync + os.replace write. Mirrors the persistence
    engine's atomicity for a user-chosen export path (which is outside the
    data dir, so it needs no chokepoint authorisation)."""
    import os
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".sc-map-", suffix=path.suffix,
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
