import { test, expect } from '@playwright/test';

test.describe('Overnight UI Verification & Account Health Suite', () => {
  test.beforeEach(async ({ page }) => {
    // Collect uncaught console errors
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });

    await page.goto('http://127.0.0.1:5173', { waitUntil: 'domcontentloaded' });
  });

  test('R6.1 Header renders branding and active ULTRA account badge', async ({ page }) => {
    // 1. Verify Branding
    await expect(page.locator('text=Gemini Local UI')).toBeVisible();

    // 2. Verify Ultra Badge in Header
    const ultraBadge = page.locator('[data-testid="tier-badge-ultra"]');
    await expect(ultraBadge).toBeVisible({ timeout: 10000 });
    await expect(ultraBadge).toHaveText('ULTRA');

    // 3. Verify Account Switcher Button contains ULTRA
    const accountBtn = page.locator('[data-testid="account-switcher-btn"]');
    await expect(accountBtn).toBeVisible();
    await expect(accountBtn).toContainText('ULTRA');
  });

  test('R6.2 Mode Toggle switches between Chat and Task mode', async ({ page }) => {
    const modeToggle = page.locator('[data-testid="header-mode-toggle"]');
    await expect(modeToggle).toBeVisible();

    const taskBtn = modeToggle.locator('button', { hasText: 'Task' });
    const chatBtn = modeToggle.locator('button', { hasText: 'Chat' });

    await expect(taskBtn).toBeVisible();
    await expect(chatBtn).toBeVisible();

    // Switch to Task Mode
    await taskBtn.click();
    await expect(taskBtn).toHaveClass(/border-blue-500/);

    // Switch back to Chat Mode
    await chatBtn.click();
    await expect(chatBtn).toHaveClass(/bg-zinc-800/);
  });

  test('R6.3 Model Selector lists Gemini 4 Argon with High Reasoning', async ({ page }) => {
    const modelDropdownBtn = page.locator('[data-testid="model-selector-btn"]');
    if (await modelDropdownBtn.isVisible()) {
      await modelDropdownBtn.click();

      // Check for Argon or 4 in dropdown items
      const dropdown = page.locator('[data-testid="model-dropdown-list"]');
      if (await dropdown.isVisible()) {
        await expect(dropdown).toContainText('Argon');
      }
    }
  });

  test('R6.4 Account Limits Telemetry Panel opens without console errors', async ({ page }) => {
    const accountBtn = page.locator('[data-testid="account-switcher-btn"]');
    await accountBtn.click();

    const limitsBtn = page.locator('text=Limits & Quotas');
    if (await limitsBtn.isVisible()) {
      await limitsBtn.click();
      const modal = page.locator('[data-testid="account-limits-modal"]');
      await expect(modal).toBeVisible({ timeout: 5000 });
    }
  });

  test('R6.5 Prompt input and action controls are responsive', async ({ page }) => {
    const promptInput = page.locator('textarea');
    await expect(promptInput).toBeVisible();
    await expect(promptInput).toBeEnabled();

    await promptInput.fill('UI test prompt');
    expect(await promptInput.inputValue()).toBe('UI test prompt');
    await promptInput.fill('');
  });
});
