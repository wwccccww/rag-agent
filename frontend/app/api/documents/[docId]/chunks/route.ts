import { NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi-fetch";

export async function GET(req: Request, { params }: { params: { docId: string } }) {
  const url = new URL(req.url);
  const r = await fastapiFetch(`/v1/documents/${params.docId}/chunks${url.search}`, {
    cache: "no-store",
  });
  const text = await r.text();
  return new NextResponse(text, {
    status: r.status,
    headers: { "Content-Type": r.headers.get("content-type") ?? "application/json" },
  });
}
