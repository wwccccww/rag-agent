export type SseHandler = (event: string, data: unknown) => void;

export async function consumeSse(response: Response, onEvent: SseHandler): Promise<void> {
  if (!response.body) throw new Error("no response body");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";

    for (const block of parts) {
      const lines = block.split("\n").filter((l) => l.length > 0);
      let eventName = "message";
      const dataLines: string[] = [];
      for (const line of lines) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      const dataStr = dataLines.join("\n");
      if (!dataStr) continue;
      try {
        onEvent(eventName, JSON.parse(dataStr));
      } catch {
        onEvent(eventName, dataStr);
      }
    }
  }
}
