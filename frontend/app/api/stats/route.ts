import { NextResponse } from "next/server";
import { fastapiFetch } from "@/lib/fastapi-fetch";

export async function GET(req: Request) {
  const url = new URL(req.url);
  const r = await fastapiFetch(`/v1/stats${url.search}`, { cache: "no-store" });
  const text = await r.text();
  return new NextResponse(text, {
    status: r.status,
    headers: { "Content-Type": "application/json" },
  });
}
