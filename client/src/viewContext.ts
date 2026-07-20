// What's on screen right now, as text the assistant can read.
//
// Components publish a compact description of what they are rendering
// (setViewSection in an effect, cleared on unmount); the assistant dock reads
// the joined result at send time and prepends it to the outgoing message. This
// keeps the server chat API untouched — the honest fix later is a dedicated
// view_context field on POST /api/chat, so context stops being stored inside
// the user's message text.

const sections = new Map<string, string>();

export function setViewSection(key: string, text: string | null): void {
  if (text === null) sections.delete(key);
  else sections.set(key, text);
}

export function readViewContext(): string {
  return [...sections.values()].join("\n\n");
}
