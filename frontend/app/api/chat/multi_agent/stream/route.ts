import { NextRequest } from "next/server";
import { fastapiFetch } from "@/lib/fastapi-fetch";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const body = await req.text();
  const res = await fastapiFetch("/v1/chat/multi_agent/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });

  return new Response(res.body, {
    status: res.status,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}
