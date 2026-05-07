import { test, expect } from "@playwright/test";

test("KG 页回归：__self__ 显示为 我", async ({ page }) => {
  await page.route("**/api/kg/entities?*", async (route) => {
    await route.fulfill({
      status: 200,
      headers: { "content-type": "application/json; charset=utf-8" },
      body: JSON.stringify([
        { id: "e1", name: "__self__", entity_type: "person", attrs: {}, created_at: new Date().toISOString() },
      ]),
    });
  });
  await page.route("**/api/kg/relations?*", async (route) => {
    await route.fulfill({
      status: 200,
      headers: { "content-type": "application/json; charset=utf-8" },
      body: JSON.stringify([
        { id: "r1", subject_id: "e1", subject_name: "__self__", predicate: "同事", object_id: "e2", object_name: "李四", confidence: 1, created_at: new Date().toISOString() },
      ]),
    });
  });

  await page.goto("/kg?user_id=demo");
  await expect(page.getByText("知识图谱")).toBeVisible();
  // 列表中不应出现 __self__，应出现 我
  await expect(page.getByText("__self__")).toHaveCount(0);
  await expect(page.locator(".kg-entity-name").filter({ hasText: "我" }).first()).toBeVisible();
});

