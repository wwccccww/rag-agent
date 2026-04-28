import { NextResponse } from "next/server";

const upstream = process.env.FASTAPI_BASE_URL ?? "http://127.0.0.1:8000";

export async function POST(req: Request) {
  const body = await req.text();
  const r = await fetch(`${upstream}/v1/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });

  const ct = r.headers.get("content-type") ?? "text/event-stream; charset=utf-8";
  return new NextResponse(r.body, {
    status: r.status,
    headers: {
      "Content-Type": ct,
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
