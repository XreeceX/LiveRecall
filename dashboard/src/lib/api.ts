export const BACKEND =
  process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

export const STREAM_URL =
  process.env.NEXT_PUBLIC_BACKEND_WS || BACKEND.replace(/^http/, "ws") + "/stream";

export async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(`${BACKEND}${path}`);
  if (!r.ok) throw new Error(`${path} ${r.status}`);
  return r.json();
}

export async function ask(text: string, sessionId = "demo"): Promise<{ question_id: string }> {
  const r = await fetch(`${BACKEND}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, session_id: sessionId }),
  });
  if (!r.ok) throw new Error(`/ask ${r.status}`);
  return r.json();
}
