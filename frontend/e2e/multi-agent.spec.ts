import { test, expect } from "@playwright/test";
import { sse } from "./utils";

test("Multi-Agent 冒烟：ma_plan + ma_worker_result + token + final", async ({ page }) => {
  await page.route("**/api/chat/multi_agent/stream", async (route) => {
    const body = sse([
      { event: "session_created", data: { session_id: "sid2" } },
      { event: "ma_plan", data: { request_id: "r1", plan: { goal: "g", steps: [] } } },
      { event: "ma_worker_result", data: { worker: "retriever", ok: true, text: "R", sources: [], steps_trace: [] } },
      { event: "ma_worker_result", data: { worker: "critic", ok: true, text: "{}", sources: [], steps_trace: [] } },
      { event: "token", data: { t: "模拟多智能体回答" } },
      { event: "final", data: { session_id: "sid2", request_id: "r1" } },
    ]);
    await route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream; charset=utf-8" },
      body,
    });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "🧩 多智能体" }).click();

  const textarea = page.locator("textarea");
  await textarea.fill("什么是 RAG？");
  await textarea.press("Enter");

  await expect(page.getByText("多智能体执行结果")).toBeVisible();
});

