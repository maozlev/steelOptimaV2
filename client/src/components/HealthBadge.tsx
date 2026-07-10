import type { HealthOut } from "../api/types";

export default function HealthBadge({ health }: { health: HealthOut | null }) {
  const server = health?.status === "ok";
  const ollama = health?.ollama.available ?? false;
  const dot = (ok: boolean) => (ok ? "bg-emerald-500" : "bg-red-500");
  return (
    <div className="flex items-center gap-3 rounded border border-zinc-800 px-3 py-1.5 text-xs text-zinc-400">
      <span className="flex items-center gap-1.5">
        <span className={`h-2 w-2 rounded-full ${dot(server)}`} />
        server
      </span>
      <span className="flex items-center gap-1.5">
        <span className={`h-2 w-2 rounded-full ${dot(ollama)}`} />
        ollama
      </span>
    </div>
  );
}
