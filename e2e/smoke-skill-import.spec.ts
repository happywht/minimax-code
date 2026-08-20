import { expect, test } from "@playwright/test";

test("skills: imports a personal SKILL.md through the UI", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "技能", exact: true }).click();

  const content = [
    "---",
    "name: e2e-personal-helper",
    "version: 1.0.0",
    "description: Imported by the browser smoke test",
    "tools: []",
    "---",
    "Answer briefly and clearly.",
    "",
  ].join("\n");
  await page.getByTestId("skills-file-input").setInputFiles({
    name: "SKILL.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(content, "utf-8"),
  });

  const row = page.getByTestId(
    "skills-row-e2e-personal-helper:e2e-personal-helper",
  );
  await expect(row).toBeVisible({ timeout: 10_000 });
  await expect(row).toContainText("e2e-personal-helper");
  await expect(row).not.toContainText("内置");

  await page
    .getByRole("button", { name: "移除技能 e2e-personal-helper" })
    .click();
  await expect(page.getByTestId("skills-remove-modal")).toBeVisible();
  await page.getByRole("button", { name: "移除" }).click();
  await expect(row).toHaveCount(0);
});
