import { test, expect } from "@playwright/test";
import { sse } from "./utils";

test("RAG 冒烟：sources + token + final", async ({ page }) => {
  await page.route("**/api/chat/stream", async (route) => {
    const body = sse([
      {
        event: "sources",
        data: {
          session_id: "sid1",
          sources: [{ chunk_id: "1", source: "doc.md", snippet: "片段A" }],
        },
      },
      { event: "token", data: { delta: "模拟回答" } },
      { event: "final", data: { session_id: "sid1" } },
    ]);
    await route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream; charset=utf-8" },
      body,
    });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "📚 RAG" }).click();

  const textarea = page.locator("textarea");
  await textarea.fill("什么是 RAG？");
  await textarea.press("Enter");

  await expect(page.getByText("模拟回答")).toBeVisible();
  await expect(page.getByRole("button", { name: /📎/ })).toBeVisible();
});

