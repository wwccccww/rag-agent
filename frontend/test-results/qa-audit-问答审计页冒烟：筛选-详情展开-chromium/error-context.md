# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: qa-audit.spec.ts >> 问答审计页冒烟：筛选 + 详情展开
- Location: e2e\qa-audit.spec.ts:3:5

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: locator('table.audit-table').getByText('这条是测试问题内容')
Expected: visible
Timeout: 15000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 15000ms
  - waiting for locator('table.audit-table').getByText('这条是测试问题内容')

```

# Page snapshot

```yaml
- generic [ref=e2]:
  - generic [ref=e3]:
    - heading "问答审计" [level=2] [ref=e4]
    - generic [ref=e5]:
      - link "← 返回对话" [ref=e6] [cursor=pointer]:
        - /url: /
      - link "🧾 工具审计" [ref=e7] [cursor=pointer]:
        - /url: /audit
      - link "📊 系统统计" [ref=e8] [cursor=pointer]:
        - /url: /stats
  - paragraph [ref=e9]:
    - text: 查看
    - code [ref=e10]: qa_audit_logs
    - text: ：每轮对话的用户问题、分区、模式、检索引用片段（
    - code [ref=e11]: chunk_id
    - text: ）等。
  - generic [ref=e12]:
    - textbox "user_id" [ref=e13]: demo
    - combobox "按 mode 过滤（可选）" [ref=e14]:
      - option "mode（全部）"
      - option "rag" [selected]
      - option "agent"
      - option "plan"
      - option "multi"
    - textbox "kb_collection（可选）" [ref=e15]
    - textbox "session_id UUID（可选）" [ref=e16]
    - textbox "request_id（可选）" [ref=e17]
    - textbox "limit" [ref=e18]: "100"
    - button "刷新" [active] [ref=e19]
  - table [ref=e21]:
    - rowgroup [ref=e22]:
      - row "时间 mode 分区 用户问题 引用数 request_id 展开" [ref=e23]:
        - columnheader "时间" [ref=e24]
        - columnheader "mode" [ref=e25]
        - columnheader "分区" [ref=e26]
        - columnheader "用户问题" [ref=e27]
        - columnheader "引用数" [ref=e28]
        - columnheader "request_id" [ref=e29]
        - columnheader "展开" [ref=e30]
    - rowgroup [ref=e31]:
      - row "暂无数据。先在任意对话模式完成一轮问答后再刷新（需开启后端 QA_AUDIT_ENABLED）。" [ref=e32]:
        - cell "暂无数据。先在任意对话模式完成一轮问答后再刷新（需开启后端 QA_AUDIT_ENABLED）。" [ref=e33]:
          - text: 暂无数据。先在任意对话模式完成一轮问答后再刷新（需开启后端
          - code [ref=e34]: QA_AUDIT_ENABLED
          - text: ）。
```

# Test source

```ts
  1  | import { test, expect } from "@playwright/test";
  2  | 
  3  | test("问答审计页冒烟：筛选 + 详情展开", async ({ page }) => {
  4  |   await page.route("**/api/audit/qa?*", async (route) => {
  5  |     await route.fulfill({
  6  |       status: 200,
  7  |       headers: { "content-type": "application/json; charset=utf-8" },
  8  |       body: JSON.stringify([
  9  |         {
  10 |           id: "550e8400-e29b-41d4-a716-446655440000",
  11 |           created_at: new Date().toISOString(),
  12 |           user_id: "demo",
  13 |           session_id: null,
  14 |           kb_collection: "default",
  15 |           mode: "rag",
  16 |           request_id: "req-qa-1",
  17 |           user_message: "这条是测试问题内容",
  18 |           assistant_preview: "回复预览片段",
  19 |           cited_chunk_ids: ["chunk-a", "chunk-b"],
  20 |           sources_count: 2,
  21 |         },
  22 |       ]),
  23 |     });
  24 |   });
  25 | 
  26 |   await page.goto("/audit/qa");
  27 | 
  28 |   await page.locator('select[title="按 mode 过滤（可选）"]').selectOption("rag");
  29 |   await page.getByRole("button", { name: "刷新" }).click();
  30 | 
  31 |   const table = page.locator("table.audit-table");
> 32 |   await expect(table.getByText("这条是测试问题内容")).toBeVisible();
     |                                              ^ Error: expect(locator).toBeVisible() failed
  33 | 
  34 |   await page.getByRole("button", { name: "详情" }).first().click();
  35 |   await expect(page.locator("pre.qa-pre-wrap").filter({ hasText: "chunk-a" })).toBeVisible();
  36 | });
  37 | 
```