import { NextResponse } from "next/server";

const upstream = process.env.FASTAPI_BASE_URL ?? "http://127.0.0.1:8000";

export async function GET(req: Request) {
  const url = new URL(req.url);
  const q = url.searchParams.toString();
  const path = q
    ? `${upstream}/v1/documents/catalog/doc-types?${q}`
    : `${upstream}/v1/documents/catalog/doc-types`;
  const r = await fetch(path, { cache: "no-store" });
  const text = await r.text();
  return new NextResponse(text, {
    status: r.status,
    headers: { "Content-Type": r.headers.get("content-type") ?? "application/json" },
  });
}
