import { NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi-fetch";

export async function DELETE(req: Request, { params }: { params: { entityId: string } }) {
  const url = new URL(req.url);
  const r = await fastapiFetch(`/v1/kg/entities/${params.entityId}${url.search}`, {
    method: "DELETE",
  });
  const text = await r.text();
  return new NextResponse(text, {
    status: r.status,
    headers: { "Content-Type": r.headers.get("content-type") ?? "application/json" },
  });
}
