import { NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi-fetch";

export async function PATCH(req: Request) {
  const url = new URL(req.url);
  const body = await req.text();
  const r = await fastapiFetch(`/v1/documents/batch${url.search}`, {
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
