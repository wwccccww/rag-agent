import { NextResponse } from "next/server";

const upstream = process.env.FASTAPI_BASE_URL ?? "http://127.0.0.1:8000";

export async function PATCH(req: Request, { params }: { params: { docId: string } }) {
  const url = new URL(req.url);
  const body = await req.text();
  const r = await fetch(`${upstream}/v1/documents/${params.docId}${url.search}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body,
  });
  const text = await r.text();
  return new NextResponse(text, {
    status: r.status,
    headers: { "Content-Type": r.headers.get("content-type") ?? "application/json" },
  });
}

export async function DELETE(req: Request, { params }: { params: { docId: string } }) {
  const url = new URL(req.url);
  const r = await fetch(`${upstream}/v1/documents/${params.docId}${url.search}`, {
    method: "DELETE",
  });
  const text = await r.text();
  return new NextResponse(text, {
    status: r.status,
    headers: { "Content-Type": r.headers.get("content-type") ?? "application/json" },
  });
}
