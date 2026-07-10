import { useEffect, useRef, useState } from "react";
import type { JobEvent } from "./types";

const TERMINAL = new Set(["job_done", "job_failed"]);

export function useJobEvents(jobId: number | null, onTerminal?: () => void) {
  const [events, setEvents] = useState<JobEvent[]>([]);
  const onTerminalRef = useRef(onTerminal);
  onTerminalRef.current = onTerminal;

  useEffect(() => {
    if (jobId == null) return;
    setEvents([]);
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/jobs/${jobId}`);
    ws.onmessage = (msg) => {
      const event: JobEvent = JSON.parse(msg.data);
      setEvents((prev) => [...prev, event]);
      if (TERMINAL.has(event.type)) onTerminalRef.current?.();
    };
    return () => ws.close();
  }, [jobId]);

  return events;
}
