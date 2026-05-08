import { NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi-fetch";

export async function DELETE(req: Request, { params }: { params: { memId: string } }) {
  const url = new URL(req.url);
  const r = await fastapiFetch(`/v1/memory/${params.memId}${url.search}`, { method: "DELETE" });
  const text = await r.text();
  return new NextResponse(text, { status: r.status });
}
