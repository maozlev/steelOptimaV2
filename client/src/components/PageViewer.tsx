import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { CutoutOut } from "../api/types";
import type { PageOut } from "../api/types";
import { parsePolygonWkt, ringToPoints } from "../api/wkt";
import { renderUrl } from "../api/client";

const STATUS_STYLE: Record<string, { stroke: string; fill: string }> = {
  approved: { stroke: "#10b981", fill: "rgba(16,185,129,0.12)" },
  rejected: { stroke: "#ef4444", fill: "rgba(239,68,68,0.06)" },
  edited: { stroke: "#3b82f6", fill: "rgba(59,130,246,0.12)" },
};

export type DrawMode = "add" | "add-poly" | "edit" | null;

interface Props {
  page: PageOut;
  cutouts: CutoutOut[];
  selectedId: number | null;
  onSelect: (id: number | null) => void;
  drawMode: DrawMode;
  onRect: (x0: number, y0: number, x1: number, y1: number) => void;
  onPolygon: (points: [number, number][]) => void;
  finalizeThreshold: number;
  highlightIds: number[] | null;
}

export default function PageViewer({
  page,
  cutouts,
  selectedId,
  onSelect,
  drawMode,
  onRect,
  onPolygon,
  finalizeThreshold,
  highlightIds,
}: Props) {
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [drag, setDrag] = useState<{ x: number; y: number } | null>(null);
  const [rect, setRect] = useState<{
    x0: number;
    y0: number;
    x1: number;
    y1: number;
  } | null>(null);
  const [polyPts, setPolyPts] = useState<[number, number][]>([]);
  const [cursor, setCursor] = useState<[number, number] | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const W = page.width_pt;
  const H = page.height_pt;

  const fitToScreen = useCallback(() => {
    const box = containerRef.current?.getBoundingClientRect();
    if (!box || !box.width || !box.height) return;
    const s = Math.min(box.width / W, box.height / H) * 0.95;
    setScale(s);
    setOffset({ x: (box.width - W * s) / 2, y: (box.height - H * s) / 2 });
  }, [W, H]);

  useLayoutEffect(() => {
    fitToScreen();
  }, [page.id, fitToScreen]);

  useEffect(() => {
    setPolyPts([]);
    setCursor(null);
    if (drawMode !== "add-poly") return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setPolyPts([]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawMode, page.id]);

  function toPagePt(e: React.MouseEvent): [number, number] {
    const box = svgRef.current!.getBoundingClientRect();
    return [
      ((e.clientX - box.left) / box.width) * W,
      ((e.clientY - box.top) / box.height) * H,
    ];
  }

  function onWheel(e: React.WheelEvent) {
    const box = containerRef.current!.getBoundingClientRect();
    const factor = e.deltaY < 0 ? 1.2 : 1 / 1.2;
    const next = Math.min(12, Math.max(0.05, scale * factor));
    const px = e.clientX - box.left;
    const py = e.clientY - box.top;
    setOffset({
      x: px - ((px - offset.x) * next) / scale,
      y: py - ((py - offset.y) * next) / scale,
    });
    setScale(next);
  }

  function closePolygon() {
    if (polyPts.length >= 3) onPolygon(polyPts);
    setPolyPts([]);
  }

  function onMouseDown(e: React.MouseEvent) {
    if (drawMode === "add-poly") return;
    if (drawMode) {
      const [x, y] = toPagePt(e);
      setRect({ x0: x, y0: y, x1: x, y1: y });
    } else {
      setDrag({ x: e.clientX - offset.x, y: e.clientY - offset.y });
    }
  }

  function onClick(e: React.MouseEvent) {
    if (drawMode !== "add-poly") return;
    const [x, y] = toPagePt(e);
    if (polyPts.length >= 3) {
      const [fx, fy] = polyPts[0];
      if (Math.hypot(x - fx, y - fy) < 8 / scale) {
        closePolygon();
        return;
      }
    }
    setPolyPts((prev) => [...prev, [x, y]]);
  }

  function onDoubleClick() {
    if (drawMode === "add-poly") closePolygon();
  }

  function onMouseMove(e: React.MouseEvent) {
    if (drawMode === "add-poly") {
      if (polyPts.length) setCursor(toPagePt(e));
      return;
    }
    if (rect) {
      const [x, y] = toPagePt(e);
      setRect({ ...rect, x1: x, y1: y });
    } else if (drag) {
      setOffset({ x: e.clientX - drag.x, y: e.clientY - drag.y });
    }
  }

  function onMouseUp() {
    if (rect) {
      const w = Math.abs(rect.x1 - rect.x0);
      const h = Math.abs(rect.y1 - rect.y0);
      if (w > 1 && h > 1) onRect(rect.x0, rect.y0, rect.x1, rect.y1);
      setRect(null);
    }
    setDrag(null);
  }

  function cutoutStyle(c: CutoutOut): {
    stroke: string;
    fill: string;
    width: number;
    pulse: boolean;
  } {
    if (c.source === "manual" && c.status !== "rejected")
      return { stroke: "#a78bfa", fill: "rgba(167,139,250,0.12)", width: 1.2, pulse: false };
    if (c.status === "pending") {
      return c.confidence >= finalizeThreshold
        ? { stroke: "#eab308", fill: "rgba(234,179,8,0.12)", width: 1.5, pulse: false }
        : { stroke: "#ef4444", fill: "rgba(239,68,68,0.10)", width: 3, pulse: true };
    }
    const s = STATUS_STYLE[c.status] ?? STATUS_STYLE.approved;
    return { stroke: s.stroke, fill: s.fill, width: 1.2, pulse: false };
  }

  return (
    <div
      ref={containerRef}
      onWheel={onWheel}
      onMouseDown={onMouseDown}
      onMouseMove={onMouseMove}
      onMouseUp={onMouseUp}
      onMouseLeave={onMouseUp}
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      className={`relative h-full w-full select-none overflow-hidden bg-zinc-900 ${
        drawMode ? "cursor-crosshair" : drag ? "cursor-grabbing" : "cursor-grab"
      }`}
    >
      <div
        className="absolute origin-top-left"
        style={{
          transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`,
          width: W,
          height: H,
        }}
      >
        <img
          src={renderUrl(page.id, false, `${page.width_pt}_${page.height_pt}`)}
          draggable={false}
          className="absolute inset-0 h-full w-full"
          alt={`page ${page.index + 1}`}
        />
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          className="absolute inset-0 h-full w-full"
        >
          {cutouts.map((c) => {
            const ring = parsePolygonWkt(c.edited_geometry_wkt ?? c.geometry_wkt);
            if (!ring.length) return null;
            const style = cutoutStyle(c);
            const selected = c.id === selectedId;
            const dimmed =
              highlightIds != null && !highlightIds.includes(c.id);
            const highlighted =
              highlightIds != null && highlightIds.includes(c.id);
            return (
              <polygon
                key={c.id}
                points={ringToPoints(ring)}
                stroke={selected || highlighted ? "#ffffff" : style.stroke}
                fill={style.fill}
                strokeWidth={(selected ? 2.5 : style.width) / scale}
                strokeDasharray={selected ? `${6 / scale} ${3 / scale}` : undefined}
                opacity={dimmed ? 0.15 : 1}
                className={`cursor-pointer ${style.pulse && !dimmed ? "pulse-stroke" : ""}`}
                onClick={(e) => {
                  e.stopPropagation();
                  if (drawMode === "add-poly") return;
                  onSelect(selected ? null : c.id);
                }}
              />
            );
          })}
          {rect && (
            <rect
              x={Math.min(rect.x0, rect.x1)}
              y={Math.min(rect.y0, rect.y1)}
              width={Math.abs(rect.x1 - rect.x0)}
              height={Math.abs(rect.y1 - rect.y0)}
              stroke="#22d3ee"
              fill="rgba(34,211,238,0.15)"
              strokeWidth={1.5 / scale}
            />
          )}
          {drawMode === "add-poly" && polyPts.length > 0 && (
            <>
              <polyline
                points={ringToPoints(
                  cursor ? [...polyPts, cursor] : polyPts,
                )}
                stroke="#22d3ee"
                fill="rgba(34,211,238,0.10)"
                strokeWidth={1.5 / scale}
              />
              {polyPts.map(([x, y], i) => (
                <circle
                  key={i}
                  cx={x}
                  cy={y}
                  r={(i === 0 ? 5 : 3) / scale}
                  fill={i === 0 ? "#22d3ee" : "#0e7490"}
                  stroke="#ffffff"
                  strokeWidth={1 / scale}
                />
              ))}
            </>
          )}
        </svg>
      </div>
      <div className="absolute bottom-2 left-2 flex items-center gap-2 rounded bg-zinc-950/80 px-2 py-1 text-xs text-zinc-400">
        <span>
          {Math.round(scale * 100)}% · wheel = zoom · drag = pan
          {drawMode === "add" && " · drawing rectangle"}
          {drawMode === "edit" && " · drawing rectangle"}
          {drawMode === "add-poly" &&
            " · click = add point · double-click / first point = close · Esc = cancel"}
        </span>
        <button
          onClick={(e) => {
            e.stopPropagation();
            fitToScreen();
          }}
          className="rounded bg-zinc-800 px-1.5 py-0.5 hover:bg-zinc-700"
        >
          ↺ Reset view
        </button>
      </div>
    </div>
  );
}
