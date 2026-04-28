import { NextResponse } from "next/server";

const upstream = process.env.FASTAPI_BASE_URL ?? "http://127.0.0.1:8000";

type Ctx = { params: Promise<{ sessionId: string }> };

export async function PATCH(req: Request, ctx: Ctx) {
  const { sessionId } = await ctx.params;
  const body = await req.text();
  const r = await fetch(`${upstream}/v1/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body,
  });
  const text = await r.text();
  return new NextResponse(text, {
    status: r.status,
    headers: { "Content-Type": "application/json" },
  });
}

export async function DELETE(_: Request, ctx: Ctx) {
  const { sessionId } = await ctx.params;
  const r = await fetch(`${upstream}/v1/sessions/${sessionId}`, {
    method: "DELETE",
  });
  return new NextResponse(null, { status: r.status });
}
