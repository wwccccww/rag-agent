import { NextResponse } from "next/server";

const upstream = process.env.FASTAPI_BASE_URL ?? "http://127.0.0.1:8000";

export async function DELETE(req: Request, { params }: { params: { memId: string } }) {
  const url = new URL(req.url);
  const r = await fetch(`${upstream}/v1/memory/${params.memId}${url.search}`, { method: "DELETE" });
  const text = await r.text();
  return new NextResponse(text, { status: r.status });
}
