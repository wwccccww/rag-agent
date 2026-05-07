import { test, expect } from "@playwright/test";

test("审计页冒烟：mode 下拉 + 列表渲染", async ({ page }) => {
  await page.route("**/api/audit/tools?*", async (route) => {
    await route.fulfill({
      status: 200,
      headers: { "content-type": "application/json; charset=utf-8" },
      body: JSON.stringify([
        {
          id: "1",
          created_at: new Date().toISOString(),
          user_id: "demo",
          session_id: null,
          mode: "multi",
          request_id: "req1",
          worker: "retriever",
          tool: "search_knowledge_base",
          status: "ok",
          elapsed_ms: 12,
          sources_count: 1,
          tool_args: { query: "x" },
          error: null,
          result_preview: "p",
        },
      ]),
    });
  });

  await page.goto("/audit");

  // mode 选择 multi
  await page.locator('select[title="按 mode 过滤（可选）"]').selectOption("multi");
  await page.getByRole("button", { name: "刷新" }).click();

  const table = page.locator("table.audit-table");
  await expect(table.locator("code").filter({ hasText: "search_knowledge_base" })).toBeVisible();
  await expect(table.getByText("retriever")).toBeVisible();
});

