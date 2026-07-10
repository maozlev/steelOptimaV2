import { useCallback, useRef, useState } from "react";
import { renderUrl } from "../api/client";
import type { CropIn, DocumentDetailOut } from "../api/types";

type HandleId = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w" | "move";

const MIN_SIZE = 0.02;

const HANDLES: { id: HandleId; className: string }[] = [
  { id: "nw", className: "left-0 top-0 -translate-x-1/2 -translate-y-1/2 cursor-nwse-resize" },
  { id: "n", className: "left-1/2 top-0 -translate-x-1/2 -translate-y-1/2 cursor-ns-resize" },
  { id: "ne", className: "right-0 top-0 translate-x-1/2 -translate-y-1/2 cursor-nesw-resize" },
  { id: "e", className: "right-0 top-1/2 translate-x-1/2 -translate-y-1/2 cursor-ew-resize" },
  { id: "se", className: "right-0 bottom-0 translate-x-1/2 translate-y-1/2 cursor-nwse-resize" },
  { id: "s", className: "left-1/2 bottom-0 -translate-x-1/2 translate-y-1/2 cursor-ns-resize" },
  { id: "sw", className: "left-0 bottom-0 -translate-x-1/2 translate-y-1/2 cursor-nesw-resize" },
  { id: "w", className: "left-0 top-1/2 -translate-x-1/2 -translate-y-1/2 cursor-ew-resize" },
];

const clamp = (v: number) => Math.min(1, Math.max(0, v));

export default function IngestionPreview({
  doc,
  busy,
  onConfirm,
  onCancel,
}: {
  doc: DocumentDetailOut;
  busy: boolean;
  onConfirm: (crop: CropIn | null) => void;
  onCancel: () => void;
}) {
  const [crop, setCrop] = useState<CropIn>({
    x_min: 0,
    y_min: 0,
    x_max: 1,
    y_max: 1,
  });
  const imgRef = useRef<HTMLImageElement>(null);
  const dragRef = useRef<{ handle: HandleId; start: CropIn; x: number; y: number } | null>(null);

  const onPointerDown = (handle: HandleId) => (e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    (e.target as Element).setPointerCapture(e.pointerId);
    dragRef.current = { handle, start: crop, x: e.clientX, y: e.clientY };
  };

  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      const drag = dragRef.current;
      const img = imgRef.current;
      if (!drag || !img) return;
      const box = img.getBoundingClientRect();
      const dx = (e.clientX - drag.x) / box.width;
      const dy = (e.clientY - drag.y) / box.height;
      const s = drag.start;
      let { x_min, y_min, x_max, y_max } = s;

      if (drag.handle === "move") {
        const mx = Math.min(Math.max(dx, -s.x_min), 1 - s.x_max);
        const my = Math.min(Math.max(dy, -s.y_min), 1 - s.y_max);
        x_min = s.x_min + mx;
        x_max = s.x_max + mx;
        y_min = s.y_min + my;
        y_max = s.y_max + my;
      } else {
        if (drag.handle.includes("w")) x_min = Math.min(clamp(s.x_min + dx), s.x_max - MIN_SIZE);
        if (drag.handle.includes("e")) x_max = Math.max(clamp(s.x_max + dx), s.x_min + MIN_SIZE);
        if (drag.handle.includes("n")) y_min = Math.min(clamp(s.y_min + dy), s.y_max - MIN_SIZE);
        if (drag.handle.includes("s")) y_max = Math.max(clamp(s.y_max + dy), s.y_min + MIN_SIZE);
      }
      setCrop({ x_min, y_min, x_max, y_max });
    },
    [],
  );

  const onPointerUp = () => {
    dragRef.current = null;
  };

  const area = (crop.x_max - crop.x_min) * (crop.y_max - crop.y_min);
  const pct = (v: number) => `${v * 100}%`;

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-zinc-950/95 p-6">
      <header className="mb-3 flex items-center justify-between">
        <div className="text-sm font-medium text-zinc-200">
          Ingestion preview — {doc.filename}
          {doc.page_count > 1 && (
            <span className="ml-2 text-xs text-zinc-500">
              (page 1 shown — crop applies to all {doc.page_count} pages)
            </span>
          )}
        </div>
        <button
          onClick={onCancel}
          disabled={busy}
          className="rounded bg-zinc-800 px-3 py-1.5 text-sm hover:bg-zinc-700 disabled:opacity-50"
        >
          Cancel
        </button>
      </header>

      <div
        className="relative flex min-h-0 flex-1 items-center justify-center"
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
      >
        <div className="relative max-h-full max-w-full">
          <img
            ref={imgRef}
            src={renderUrl(doc.pages[0].id, false, `${doc.pages[0].width_pt}_${doc.pages[0].height_pt}`)}
            draggable={false}
            className="max-h-[calc(100vh-160px)] max-w-full select-none object-contain"
            alt="document preview"
          />
          {/* dark mask outside the crop box */}
          <div className="pointer-events-none absolute inset-x-0 top-0 bg-zinc-950/70" style={{ height: pct(crop.y_min) }} />
          <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-zinc-950/70" style={{ height: pct(1 - crop.y_max) }} />
          <div
            className="pointer-events-none absolute left-0 bg-zinc-950/70"
            style={{ top: pct(crop.y_min), height: pct(crop.y_max - crop.y_min), width: pct(crop.x_min) }}
          />
          <div
            className="pointer-events-none absolute right-0 bg-zinc-950/70"
            style={{ top: pct(crop.y_min), height: pct(crop.y_max - crop.y_min), width: pct(1 - crop.x_max) }}
          />
          {/* crop box */}
          <div
            className="absolute cursor-move border-2 border-emerald-400"
            style={{
              left: pct(crop.x_min),
              top: pct(crop.y_min),
              width: pct(crop.x_max - crop.x_min),
              height: pct(crop.y_max - crop.y_min),
            }}
            onPointerDown={onPointerDown("move")}
          >
            {HANDLES.map((h) => (
              <div
                key={h.id}
                onPointerDown={onPointerDown(h.id)}
                className={`absolute h-3 w-3 rounded-sm border border-zinc-900 bg-emerald-400 ${h.className}`}
              />
            ))}
          </div>
        </div>
      </div>

      <footer className="mt-3 flex items-center justify-between">
        <p className="text-xs text-zinc-500">
          Drag the anchors to crop out title blocks, margins, or legends.
        </p>
        <button
          onClick={() => onConfirm(area >= 0.995 ? null : crop)}
          disabled={busy}
          className="rounded bg-emerald-700 px-4 py-2 text-sm font-medium hover:bg-emerald-600 disabled:opacity-50"
        >
          {busy ? "Sending…" : "Confirm & Send to Extract"}
        </button>
      </footer>
    </div>
  );
}
