import { useEffect, useRef, useState } from "react";

/** A minimal sketch pad for when there is no drawing file at all: freehand pen,
 * rectangles, and TYPED text labels. The sketch goes to the local vision model,
 * which reads print far better than handwriting — so the text tool (click,
 * type "8× L60x60x6 L=2000") is the reliable channel; the shapes are context.
 * White background on purpose: the model reads dark-on-light. */
export default function WhiteboardModal({
  onClose,
  onDone,
}: {
  onClose: () => void;
  onDone: (png: Blob) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [tool, setTool] = useState<"pen" | "rect" | "text">("pen");
  const [textInput, setTextInput] = useState<{ x: number; y: number; value: string } | null>(
    null,
  );
  const drawing = useRef<{ x: number; y: number } | null>(null);
  const rectStart = useRef<{ x: number; y: number } | null>(null);
  const snapshot = useRef<ImageData | null>(null);

  const W = 1000;
  const H = 640;

  useEffect(() => {
    const ctx = canvasRef.current!.getContext("2d")!;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = "#111111";
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.font = "22px sans-serif";
    ctx.fillStyle = "#111111";
  }, []);

  function pos(e: React.PointerEvent): { x: number; y: number } {
    const r = canvasRef.current!.getBoundingClientRect();
    return {
      x: ((e.clientX - r.left) / r.width) * W,
      y: ((e.clientY - r.top) / r.height) * H,
    };
  }

  function down(e: React.PointerEvent) {
    const p = pos(e);
    const ctx = canvasRef.current!.getContext("2d")!;
    if (tool === "text") {
      setTextInput({ x: p.x, y: p.y, value: "" });
      return;
    }
    if (tool === "rect") {
      rectStart.current = p;
      snapshot.current = ctx.getImageData(0, 0, W, H);
      return;
    }
    drawing.current = p;
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
  }

  function move(e: React.PointerEvent) {
    const ctx = canvasRef.current!.getContext("2d")!;
    const p = pos(e);
    if (tool === "pen" && drawing.current) {
      ctx.lineTo(p.x, p.y);
      ctx.stroke();
      drawing.current = p;
    }
    if (tool === "rect" && rectStart.current && snapshot.current) {
      ctx.putImageData(snapshot.current, 0, 0);
      const s = rectStart.current;
      ctx.strokeRect(s.x, s.y, p.x - s.x, p.y - s.y);
    }
  }

  function up() {
    drawing.current = null;
    rectStart.current = null;
    snapshot.current = null;
  }

  function commitText() {
    if (textInput && textInput.value.trim()) {
      const ctx = canvasRef.current!.getContext("2d")!;
      ctx.fillText(textInput.value, textInput.x, textInput.y);
    }
    setTextInput(null);
  }

  function clearBoard() {
    const ctx = canvasRef.current!.getContext("2d")!;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = "#111111";
  }

  function done() {
    canvasRef.current!.toBlob((blob) => {
      if (blob) onDone(blob);
    }, "image/png");
  }

  const toolBtn = (t: typeof tool, label: string, title: string) => (
    <button
      onClick={() => setTool(t)}
      title={title}
      className={`rounded px-2.5 py-1 text-xs ${
        tool === t ? "bg-zinc-200 font-medium text-zinc-900" : "bg-zinc-800 hover:bg-zinc-700"
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6">
      <div className="flex max-h-full w-full max-w-4xl flex-col gap-2 rounded border border-zinc-700 bg-zinc-950 p-4">
        <div className="flex items-center gap-2">
          <span className="mr-2 text-sm font-medium">🖌 Whiteboard</span>
          {toolBtn("pen", "✏ Pen", "Freehand sketch")}
          {toolBtn("rect", "▭ Rect", "Draw a rectangle (a plate, a frame)")}
          {toolBtn(
            "text",
            "T Text",
            'Click the board, then TYPE the part line — e.g. "8x L60x60x6 L=2000". Typed text is what the model reads reliably.',
          )}
          <button
            onClick={clearBoard}
            className="rounded bg-zinc-800 px-2.5 py-1 text-xs hover:bg-zinc-700"
          >
            Clear
          </button>
          <span className="flex-1" />
          <button
            onClick={done}
            className="rounded bg-emerald-700 px-3 py-1 text-sm font-medium hover:bg-emerald-600"
          >
            Read this sketch →
          </button>
          <button
            onClick={onClose}
            className="rounded bg-zinc-800 px-3 py-1 text-sm hover:bg-zinc-700"
          >
            Cancel
          </button>
        </div>
        <p className="text-xs text-zinc-500">
          Best results: use the Text tool for the part lines ("8× L60x60x6
          L=2000", "4× PLATE 12mm 300×200") — sketching is for context, typed
          labels are what gets read.
        </p>
        <div className="relative">
          <canvas
            ref={canvasRef}
            width={W}
            height={H}
            onPointerDown={down}
            onPointerMove={move}
            onPointerUp={up}
            onPointerLeave={up}
            className="w-full cursor-crosshair rounded border border-zinc-700 bg-white"
            style={{ aspectRatio: `${W} / ${H}`, touchAction: "none" }}
          />
          {textInput && (
            <input
              autoFocus
              value={textInput.value}
              onChange={(e) => setTextInput({ ...textInput, value: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === "Enter") commitText();
                if (e.key === "Escape") setTextInput(null);
              }}
              onBlur={commitText}
              placeholder="type, Enter to place"
              className="absolute rounded border border-sky-500 bg-white px-1 text-sm text-zinc-900 outline-none"
              style={{
                left: `${(textInput.x / W) * 100}%`,
                top: `${(textInput.y / H) * 100}%`,
              }}
            />
          )}
        </div>
      </div>
    </div>
  );
}
