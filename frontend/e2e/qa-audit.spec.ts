import { test, expect } from "@playwright/test";

test("问答审计页冒烟：筛选 + 详情展开", async ({ page }) => {
  await page.route("**/api/audit/qa?*", async (route) => {
    await route.fulfill({
      status: 200,
      headers: { "content-type": "application/json; charset=utf-8" },
      body: JSON.stringify([
        {
          id: "550e8400-e29b-41d4-a716-446655440000",
          created_at: new Date().toISOString(),
          user_id: "demo",
          session_id: null,
          kb_collection: "default",
          mode: "rag",
          request_id: "req-qa-1",
          user_message: "这条是测试问题内容",
          assistant_preview: "回复预览片段",
          cited_chunk_ids: ["chunk-a", "chunk-b"],
          sources_count: 2,
        },
      ]),
    });
  });

  await page.goto("/audit/qa");

  await page.locator('select[title="按 mode 过滤（可选）"]').selectOption("rag");
  await page.getByRole("button", { name: "刷新" }).click();

  const table = page.locator("table.audit-table");
  await expect(table.getByText("这条是测试问题内容")).toBeVisible();

  await page.getByRole("button", { name: "详情" }).first().click();
  await expect(page.locator("pre.audit-pre").filter({ hasText: "chunk-a" })).toBeVisible();
});
