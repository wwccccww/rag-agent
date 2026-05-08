import { NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi-fetch";

type Ctx = { params: Promise<{ sessionId: string }> };

export async function PATCH(req: Request, ctx: Ctx) {
  const { sessionId } = await ctx.params;
  const body = await req.text();
  const r = await fastapiFetch(`/v1/sessions/${sessionId}`, {
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
  const r = await fastapiFetch(`/v1/sessions/${sessionId}`, {
    method: "DELETE",
  });
  return new NextResponse(null, { status: r.status });
}
