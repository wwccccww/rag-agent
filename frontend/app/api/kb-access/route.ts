import { NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi-fetch";

export async function GET(req: Request) {
  const url = new URL(req.url);
  const q = url.searchParams.toString();
  const path = q ? `/v1/kb-access?${q}` : `/v1/kb-access`;
  const r = await fastapiFetch(path, { cache: "no-store" });
  const text = await r.text();
  return new NextResponse(text, {
    status: r.status,
    headers: { "Content-Type": r.headers.get("content-type") ?? "application/json" },
  });
}
