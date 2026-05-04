import { NextResponse } from "next/server";

const upstream = process.env.FASTAPI_BASE_URL ?? "http://127.0.0.1:8000";

export async function GET(req: Request, { params }: { params: { docId: string } }) {
  const url = new URL(req.url);
  const view = url.searchParams.get("view") ?? "parent";
  const limit = url.searchParams.get("limit") ?? "100";
  const r = await fetch(
    `${upstream}/v1/documents/${params.docId}/chunks?view=${view}&limit=${limit}`,
    { cache: "no-store" },
  );
  const text = await r.text();
  return new NextResponse(text, {
    status: r.status,
    headers: { "Content-Type": r.headers.get("content-type") ?? "application/json" },
  });
}
