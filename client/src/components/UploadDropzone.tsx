import { useRef, useState } from "react";

export default function UploadDropzone({
  onFile,
  multiple = false,
}: {
  onFile: (file: File) => void;
  multiple?: boolean;
}) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const accept = (files: FileList | null) => {
    if (!files) return;
    for (const file of multiple ? Array.from(files) : Array.from(files).slice(0, 1)) {
      onFile(file);
    }
  };

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
        accept(e.dataTransfer.files);
      }}
      onClick={() => input.current?.click()}
      className={`cursor-pointer rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors ${
        over
          ? "border-emerald-500 bg-emerald-950/30"
          : "border-zinc-700 hover:border-zinc-500"
      }`}
    >
      <p className="text-sm text-zinc-300">
        {multiple
          ? "Drop blueprint PDFs / JPEGs / PNGs here, or click to browse"
          : "Drop a blueprint PDF / JPEG / PNG here, or click to browse"}
      </p>
      <input
        ref={input}
        type="file"
        accept="application/pdf,image/jpeg,image/png"
        multiple={multiple}
        className="hidden"
        onChange={(e) => {
          accept(e.target.files);
          e.target.value = "";
        }}
      />
    </div>
  );
}
