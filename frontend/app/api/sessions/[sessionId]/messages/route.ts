import { NextResponse } from "next/server";

const upstream = process.env.FASTAPI_BASE_URL ?? "http://127.0.0.1:8000";

export async function GET(_req: Request, { params }: { params: { sessionId: string } }) {
  const r = await fetch(`${upstream}/v1/sessions/${params.sessionId}/messages`, { cache: "no-store" });
  const text = await r.text();
  return new NextResponse(text, {
    status: r.status,
    headers: { "Content-Type": r.headers.get("content-type") ?? "application/json" },
  });
}
