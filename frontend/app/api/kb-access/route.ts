import { NextResponse } from "next/server";

const upstream = process.env.FASTAPI_BASE_URL ?? "http://127.0.0.1:8000";

export async function GET(req: Request) {
  const url = new URL(req.url);
  const q = url.searchParams.toString();
  const target = q ? `${upstream}/v1/kb-access?${q}` : `${upstream}/v1/kb-access`;
  const r = await fetch(target, { cache: "no-store" });
  const text = await r.text();
  return new NextResponse(text, {
    status: r.status,
    headers: { "Content-Type": r.headers.get("content-type") ?? "application/json" },
  });
}
