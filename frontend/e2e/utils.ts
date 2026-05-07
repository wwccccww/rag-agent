export function sse(payloads: { event: string; data: unknown }[]): string {
  return payloads
    .map((p) => `event: ${p.event}\n` + `data: ${JSON.stringify(p.data)}\n\n`)
    .join("");
}

