/**
 * 调用 FastAPI 上游：可选附加 FASTAPI_API_KEY（与后端 API_KEY 对齐）。
 */
const upstream = process.env.FASTAPI_BASE_URL ?? "http://127.0.0.1:8000";

export function fastapiUrl(path: string): string {
  const base = upstream.replace(/\/$/, "");
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${base}${p}`;
}

export function withFastapiAuth(headers?: HeadersInit): Headers {
  const h = new Headers(headers);
  const k = process.env.FASTAPI_API_KEY;
  if (k && !h.has("X-API-Key")) {
    h.set("X-API-Key", k);
  }
  return h;
}

export async function fastapiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(fastapiUrl(path), {
    ...init,
    headers: withFastapiAuth(init?.headers),
  });
}
