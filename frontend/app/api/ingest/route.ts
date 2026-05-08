import { NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi-fetch";

export async function POST(req: Request) {
  const form = await req.formData();
  const r = await fastapiFetch("/v1/ingest", { method: "POST", body: form });
  const text = await r.text();
  return new NextResponse(text, {
    status: r.status,
    headers: { "Content-Type": r.headers.get("content-type") ?? "application/json" },
  });
}
