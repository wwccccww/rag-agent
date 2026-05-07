import { test, expect } from "@playwright/test";
import { sse } from "./utils";

test("Agent 冒烟：agent_step + sources + token + final", async ({ page }) => {
  await page.route("**/api/chat/agent/stream", async (route) => {
    const body = sse([
      { event: "agent_step", data: { step: 1, tool: "search_knowledge_base", icon: "📚", label: "检索知识库", status: "calling", args: { query: "x" }, reasoning: "Thought" } },
      { event: "agent_step", data: { step: 1, tool: "search_knowledge_base", icon: "📚", label: "检索知识库", status: "done", result_summary: "ok", source_count: 1, elapsed_ms: 12, reasoning: "Thought" } },
      { event: "sources", data: { session_id: "sidA", sources: [{ chunk_id: "1", source: "doc.md", snippet: "片段A" }] } },
      { event: "token", data: { delta: "模拟 Agent 回答" } },
      { event: "final", data: { session_id: "sidA" } },
    ]);
    await route.fulfill({ status: 200, headers: { "content-type": "text/event-stream; charset=utf-8" }, body });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "⚡ Agent" }).click();

  const textarea = page.locator("textarea");
  await textarea.fill("测试 Agent");
  await textarea.press("Enter");

  await expect(page.getByText("模拟 Agent 回答")).toBeVisible();
  await expect(page.getByText("检索知识库")).toBeVisible();
});

