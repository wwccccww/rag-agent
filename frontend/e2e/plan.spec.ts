import { test, expect } from "@playwright/test";
import { sse } from "./utils";

test("规划 冒烟：plan + plan_step_* + token + final", async ({ page }) => {
  await page.route("**/api/chat/plan_execute/stream", async (route) => {
    const body = sse([
      { event: "plan", data: { goal: "g", steps: [{ id: 1, description: "d", tool: "search_knowledge_base" }], plan_ms: 1 } },
      { event: "plan_step_start", data: { step_id: 1, description: "d", tool: "search_knowledge_base" } },
      { event: "plan_step_done", data: { step_id: 1, description: "d", success: true, result_summary: "ok", elapsed_ms: 1 } },
      { event: "sources", data: { session_id: "sidP", sources: [] } },
      { event: "token", data: { delta: "模拟 规划 回答" } },
      { event: "final", data: { session_id: "sidP", plan_goal: "g", plan_steps: [{ id: 1, description: "d", tool: "search_knowledge_base" }] } },
    ]);
    await route.fulfill({ status: 200, headers: { "content-type": "text/event-stream; charset=utf-8" }, body });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "🗂 规划" }).click();

  const textarea = page.locator("textarea");
  await textarea.fill("测试规划");
  await textarea.press("Enter");

  await expect(page.getByText("模拟 规划 回答")).toBeVisible();
  await expect(page.locator(".plan-panel-icon")).toBeVisible();
});

