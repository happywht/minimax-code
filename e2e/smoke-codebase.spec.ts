/**
 * v0.11.0 codebase smoke — Build Index → Search → SourcesPanel.
 *
 * Verifies the Codebase inspector panel end-to-end:
 *   1. The Codebase tab opens and loads the indexer status.
 *   2. Build Index triggers a real `codebase.build_index` call and the
 *      panel reaches the "done" state with non-empty stats.
 *   3. Search submits `codebase.search` and renders result rows.
 *   4. The SourcesPanel appears on assistant messages that carry source
 *      annotations (injected via the chat store so the test does not
 *      depend on a live LLM producing tool results).
 *
 * The globalSetup boots a real Python agent (forced mock LLM), so the
 * `codebase.*` IPC handlers exercised here are the production handlers.
 */
import { test, expect, type Page } from "@playwright/test";

test("codebase: build index, search, and render SourcesPanel", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/");
  await expect(page.getByTestId("app-root")).toBeVisible({ timeout: 15_000 });

  // Open the Codebase inspector tab.
  const codebaseTab = page.getByTestId("right-panel-tab-codebase");
  await expect(codebaseTab).toBeVisible({ timeout: 10_000 });
  await codebaseTab.click();

  const codebaseBody = page.getByTestId("right-panel-codebase-body");
  await expect(codebaseBody).toBeVisible();

  // The CodebasePanel is rendered with testId="right-panel-codebase-panel".
  const status = page.getByTestId("right-panel-codebase-panel-status");
  const buildBtn = page.getByTestId("right-panel-codebase-panel-build");
  const filesStat = page.getByTestId("right-panel-codebase-panel-files");
  const chunksStat = page.getByTestId("right-panel-codebase-panel-chunks");

  // Wait for the initial status poll to populate the panel.
  await expect(status).toBeVisible({ timeout: 10_000 });

  // The agent starts a delayed incremental build on boot, so the panel
  // may already be indexing when we open it. Wait for any in-flight
  // build to finish before triggering a forced rebuild.
  await expect(status).not.toHaveText("indexing", { timeout: 45_000 });

  // Build (or refresh) the index. The forced rebuild clears stale chunks
  // and re-indexes the current workspace.
  await buildBtn.click();

  // Wait until the indexer reports completion.
  await expect(status).toHaveText("done", { timeout: 45_000 });

  // Stats should reflect a real workspace (the agent/ directory).
  await expect(filesStat).toHaveText(/文件：\d+/);
  await expect(chunksStat).toHaveText(/分块：\d+/);
  const filesText = await filesStat.textContent();
  const chunksText = await chunksStat.textContent();
  expect(filesText).toMatch(/文件：\d+/);
  expect(chunksText).toMatch(/分块：\d+/);

  // Run a search against the indexed workspace.
  const searchInput = page.getByTestId("right-panel-codebase-panel-search");
  await searchInput.fill("codebase");
  await page.getByTestId("right-panel-codebase-panel-search-btn").click();

  // Result rows should appear.
  await expect(page.getByTestId("right-panel-codebase-panel-results")).toBeVisible({ timeout: 10_000 });
  const resultCount = await page.locator("[data-testid='right-panel-codebase-panel-result']").count();
  expect(resultCount).toBeGreaterThan(0);

  // Result rows should contain file paths and snippets.
  const firstResult = page.locator("[data-testid='right-panel-codebase-panel-result']").first();
  await expect(firstResult).toContainText(".py", { timeout: 5_000 });

  // SourcesPanel: inject an assistant message with source annotations
  // and confirm the per-turn sources chip renders in the chat timeline.
  await waitForChatStoreReady(page);
  await page.evaluate(async () => {
    // @ts-expect-error Vite serves this source module directly in the browser.
    const { useChat } = await import("/src/stores/chat.ts");
    useChat.setState({
      messages: [
        {
          id: "codebase-user",
          role: "user",
          text: "How is the indexer implemented?",
          streaming: false,
          status: "completed",
          created_at: 1,
        },
        {
          id: "codebase-assistant",
          role: "assistant",
          text: "The indexer walks the workspace and persists chunks.",
          streaming: false,
          status: "completed",
          created_at: 2,
          metadata: {
            thinking_count: 1,
            tokens_in: 10,
            tokens_out: 20,
            sources: [
              { file_path: "minimax_code/codebase/indexer.py", line_range: "L1-50" },
              { file_path: "minimax_code/ipc/handlers_codebase.py", line_range: null },
            ],
          },
        },
      ],
      status: "idle",
      error: null,
    });
  });

  const sourcesPanel = page.getByTestId("sources-panel");
  await expect(sourcesPanel).toBeVisible({ timeout: 5_000 });
  await expect(sourcesPanel).toContainText("2 来源");

  await sourcesPanel.getByRole("button", { name: "来源" }).click();
  await expect(page.getByText("minimax_code/codebase/indexer.py")).toBeVisible();
  await expect(page.getByText("L1-50")).toBeVisible();
  await expect(page.getByText("minimax_code/ipc/handlers_codebase.py")).toBeVisible();
});

async function waitForChatStoreReady(page: Page): Promise<void> {
  await expect
    .poll(
      () => page.evaluate(async () => {
        // @ts-expect-error Vite serves this source module directly in the browser.
        const { useChat } = await import("/src/stores/chat.ts");
        return useChat.getState().agentReady;
      }),
      { timeout: 10_000 },
    )
    .toBe(true);
}
