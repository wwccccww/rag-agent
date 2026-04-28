import { NextResponse } from "next/server";

const upstream = process.env.FASTAPI_BASE_URL ?? "http://127.0.0.1:8000";

export async function POST(req: Request) {
  const form = await req.formData();
  const r = await fetch(`${upstream}/v1/ingest`, { method: "POST", body: form });
  const text = await r.text();
  return new NextResponse(text, {
    status: r.status,
    headers: { "Content-Type": r.headers.get("content-type") ?? "application/json" },
  });
}
