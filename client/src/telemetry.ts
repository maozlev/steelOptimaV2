import { api } from "./api/client";

export const sessionId = crypto.randomUUID();

type QueuedEvent = { type: string; entity_id?: number; payload?: object };

let queue: QueuedEvent[] = [];

export function track(type: string, entity_id?: number, payload?: object) {
  queue.push({ type, entity_id, payload });
}

async function flush() {
  if (!queue.length) return;
  const events = queue;
  queue = [];
  try {
    await api.postTelemetry({ session_id: sessionId, events });
  } catch {
    queue = events.concat(queue); // retry on next flush
  }
}

setInterval(flush, 5000);
window.addEventListener("beforeunload", () => {
  if (!queue.length) return;
  navigator.sendBeacon(
    "/api/telemetry/events",
    new Blob(
      [JSON.stringify({ session_id: sessionId, events: queue })],
      { type: "application/json" },
    ),
  );
});
