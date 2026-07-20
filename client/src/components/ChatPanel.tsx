import { useCallback, useEffect, useRef, useState } from "react";
import { api, sendChatMessage } from "../api/client";
import type { ChatMessageOut, ChatScope } from "../api/types";

/** Scoped Q&A chat. One component for all three scopes — the server decides
 * what the model gets to know (this document / this project / all projects);
 * the panel only differs in the hint line telling the user what "here" means.
 *
 * screenContext (optional): called at send time; its text is prepended to the
 * outgoing message inside [[SCREEN]]…[[/SCREEN]] markers so the model knows
 * what the operator is looking at. The block is stripped from display — the
 * user sees only what they typed. (Server API untouched; the clean long-term
 * fix is a view_context field on the chat POST.)
 */
const SCREEN_RE = /\[\[(?:SCREEN|TOOLS)\]\][\s\S]*?\[\[\/(?:SCREEN|TOOLS)\]\]\s*/g;
const ACTION_RE = /\[\[ACTION\]\]\s*([\s\S]*?)\s*\[\[\/ACTION\]\]/g;

// What the model is told it may do. Rides along with every message when an
// onAction handler is wired; the client parses the emitted blocks and executes
// them against the normal REST API — the server chat stays a plain text pipe.
const TOOLS_BLOCK = `[[TOOLS]]
You may CHANGE data when the user explicitly asks. To act, end your answer with one block per action:
[[ACTION]]{"type":"set_price","material_key":"L60X60X6","price":12,"pricing_unit":"per_kg"}[[/ACTION]]
Types: set_price{material_key,price,pricing_unit:per_kg|per_m|per_unit} · approve_table{table_id} · reject_table{table_id} · reopen_table{table_id} · create_order{material_key,stock:[{length_mm,price}],kerf_mm?} · start_scan{document_id} · delete_document{document_id} · switch_tab{tab:documents|tables|summary|bid|orders|inventory} · set_inventory_mode{on:true|false}
Strict JSON inside blocks. Never act unasked.
[[/TOOLS]]`;

export default function ChatPanel({
  scope,
  scopeId,
  hint,
  screenContext,
  onAction,
}: {
  scope: ChatScope;
  scopeId: number;
  hint: string;
  screenContext?: () => string;
  /** Execute one agent-emitted action; return a short human summary. */
  onAction?: (action: Record<string, unknown>) => Promise<string>;
}) {
  type PendingAction = {
    key: number;
    action: Record<string, unknown> | null;
    label: string;
    error?: string;
  };
  const [messages, setMessages] = useState<ChatMessageOut[]>([]);
  const [pending, setPending] = useState<PendingAction[]>([]);
  const [draft, setDraft] = useState("");
  // the assistant turn currently streaming in, not yet in `messages`
  const [streaming, setStreaming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    api
      .getChatMessages(scope, scopeId)
      .then(setMessages)
      .catch((e) => setError(e.message));
    return () => abortRef.current?.abort();
  }, [scope, scopeId]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, streaming, pending]);

  const send = useCallback(async () => {
    const content = draft.trim();
    if (!content || busy) return;
    setDraft("");
    setError(null);
    setBusy(true);
    setMessages((prev) => [
      ...prev,
      { id: -Date.now(), role: "user", content, created_at: "" },
    ]);
    setStreaming("");
    abortRef.current = new AbortController();
    const ctx = screenContext?.();
    const outgoing =
      (ctx ? `[[SCREEN]]\nOn screen now:\n${ctx}\n[[/SCREEN]]\n` : "") +
      (onAction ? `${TOOLS_BLOCK}\n` : "") +
      content;
    try {
      const full = await sendChatMessage(
        scope,
        scopeId,
        outgoing,
        (delta) => setStreaming((prev) => (prev ?? "") + delta),
        abortRef.current.signal,
      );
      setMessages((prev) => [
        ...prev,
        { id: -Date.now() - 1, role: "assistant", content: full, created_at: "" },
      ]);
      // actions the model emitted become PENDING cards — nothing runs without
      // an explicit click, so a hallucinated action is just a card you dismiss
      if (onAction) {
        const found: PendingAction[] = [];
        let i = 0;
        for (const m of full.matchAll(ACTION_RE)) {
          i += 1;
          try {
            found.push({
              key: Date.now() + i,
              action: JSON.parse(m[1]) as Record<string, unknown>,
              label: m[1].trim(),
            });
          } catch {
            found.push({
              key: Date.now() + i,
              action: null,
              label: m[1].trim().slice(0, 120),
              error: "model emitted invalid JSON",
            });
          }
        }
        if (found.length) setPending((prev) => [...prev, ...found]);
      }
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) {
        setError((e as Error).message);
      }
    } finally {
      setStreaming(null);
      setBusy(false);
    }
  }, [draft, busy, scope, scopeId, screenContext, onAction]);

  const clear = useCallback(() => {
    api
      .clearChat(scope, scopeId)
      .then(() => {
        setMessages([]);
        setPending([]);
      })
      .catch((e) => setError(e.message));
  }, [scope, scopeId]);

  async function runPending(p: PendingAction) {
    setPending((prev) => prev.filter((x) => x.key !== p.key));
    if (!p.action || !onAction) return;
    let note: string;
    try {
      note = `⚡ ${await onAction(p.action)}`;
    } catch (e) {
      note = `⚡ action failed: ${(e as Error).message}`;
    }
    setMessages((prev) => [
      ...prev,
      { id: -Date.now(), role: "assistant", content: note, created_at: "" },
    ]);
  }

  const bubble = (role: "user" | "assistant", content: string, key: React.Key) => (
    <div
      key={key}
      className={`max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm ${
        role === "user"
          ? "self-end bg-emerald-900/50 text-emerald-100"
          : "self-start bg-zinc-800 text-zinc-200"
      }`}
    >
      {content.replace(SCREEN_RE, "").replace(ACTION_RE, "").trim() || "⚡ …"}
    </div>
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between border-b border-zinc-800 px-3 py-1.5">
        <span className="text-xs text-zinc-500">{hint}</span>
        <button
          onClick={clear}
          disabled={busy || messages.length === 0}
          className="text-xs text-zinc-500 hover:text-zinc-300 disabled:opacity-40"
          title="Clear this conversation"
        >
          ✕ clear
        </button>
      </div>

      <div ref={scrollRef} className="flex flex-1 flex-col gap-2 overflow-y-auto p-3">
        {messages.length === 0 && streaming === null && (
          <p className="mt-4 text-center text-xs text-zinc-600">
            Ask about quantities, sizes, materials, prices or order plans.
            <br />
            Answers come only from the data on screen — עברית או English.
          </p>
        )}
        {messages.map((m) => bubble(m.role, m.content, m.id))}
        {streaming !== null &&
          (streaming === ""
            ? bubble("assistant", "…", "streaming")
            : bubble("assistant", streaming, "streaming"))}
        {pending.map((p) => (
          <div
            key={p.key}
            className="max-w-[85%] self-start rounded-lg border border-amber-800 bg-amber-950/40 px-3 py-2 text-xs"
          >
            <div className="mb-1 font-medium text-amber-300">
              ⚡ Proposed action{p.error ? ` — ${p.error}` : ""}
            </div>
            <code className="block whitespace-pre-wrap break-all text-amber-100/80">
              {p.label}
            </code>
            <div className="mt-2 flex gap-2">
              {p.action && (
                <button
                  onClick={() => void runPending(p)}
                  className="rounded bg-emerald-700 px-2.5 py-0.5 font-medium hover:bg-emerald-600"
                >
                  Run
                </button>
              )}
              <button
                onClick={() =>
                  setPending((prev) => prev.filter((x) => x.key !== p.key))
                }
                className="rounded bg-zinc-800 px-2.5 py-0.5 hover:bg-zinc-700"
              >
                Dismiss
              </button>
            </div>
          </div>
        ))}
      </div>

      {error && (
        <div className="border-t border-red-900 bg-red-950/60 px-3 py-1.5 text-xs text-red-300">
          {error}
        </div>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
        className="flex gap-2 border-t border-zinc-800 p-2"
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={busy ? "Answering…" : "Ask a question…"}
          disabled={busy}
          dir="auto"
          className="min-w-0 flex-1 rounded border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-sm outline-none placeholder:text-zinc-600 focus:border-zinc-500 disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={busy || !draft.trim()}
          className="rounded bg-emerald-800 px-3 py-1.5 text-sm font-medium hover:bg-emerald-700 disabled:opacity-40"
        >
          Send
        </button>
      </form>
    </div>
  );
}
