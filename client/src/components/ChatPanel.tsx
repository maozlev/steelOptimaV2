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
const SCREEN_RE = /\[\[SCREEN\]\][\s\S]*?\[\[\/SCREEN\]\]\s*/g;

export default function ChatPanel({
  scope,
  scopeId,
  hint,
  screenContext,
}: {
  scope: ChatScope;
  scopeId: number;
  hint: string;
  screenContext?: () => string;
}) {
  const [messages, setMessages] = useState<ChatMessageOut[]>([]);
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
  }, [messages, streaming]);

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
    const outgoing = ctx
      ? `[[SCREEN]]\nWhat the operator currently sees on screen (context, not part of the question):\n${ctx}\n[[/SCREEN]]\n${content}`
      : content;
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
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) {
        setError((e as Error).message);
      }
    } finally {
      setStreaming(null);
      setBusy(false);
    }
  }, [draft, busy, scope, scopeId, screenContext]);

  const clear = useCallback(() => {
    api
      .clearChat(scope, scopeId)
      .then(() => setMessages([]))
      .catch((e) => setError(e.message));
  }, [scope, scopeId]);

  const bubble = (role: "user" | "assistant", content: string, key: React.Key) => (
    <div
      key={key}
      className={`max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm ${
        role === "user"
          ? "self-end bg-emerald-900/50 text-emerald-100"
          : "self-start bg-zinc-800 text-zinc-200"
      }`}
    >
      {content.replace(SCREEN_RE, "")}
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
