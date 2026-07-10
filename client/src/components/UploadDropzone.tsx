import { useRef, useState } from "react";

export default function UploadDropzone({
  onFile,
}: {
  onFile: (file: File) => void;
}) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const file = e.dataTransfer.files[0];
        if (file) onFile(file);
      }}
      onClick={() => input.current?.click()}
      className={`cursor-pointer rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors ${
        over
          ? "border-emerald-500 bg-emerald-950/30"
          : "border-zinc-700 hover:border-zinc-500"
      }`}
    >
      <p className="text-sm text-zinc-300">
        Drop a blueprint PDF / JPEG / PNG here, or click to browse
      </p>
      <input
        ref={input}
        type="file"
        accept="application/pdf,image/jpeg,image/png"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onFile(file);
          e.target.value = "";
        }}
      />
    </div>
  );
}
