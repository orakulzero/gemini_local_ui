import { test, expect } from '@playwright/test';

test.describe('Milestone M8 (R6): E2E UI Overnight Verification', () => {
  test.beforeEach(async ({ page }) => {
    // Intercept favicon to prevent 404 console error
    await page.route('**/favicon.ico', (route) =>
      route.fulfill({ status: 204, contentType: 'image/x-icon', body: '' })
    );
  });

  test('R6.1 Header renders branding and active ULTRA account badge', async ({ page }) => {
    await page.goto('http://127.0.0.1:5173', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('text=Gemini Local UI')).toBeVisible();

    const ultraBadge = page.locator('[data-testid="tier-badge-ultra"]').first();
    await expect(ultraBadge).toBeVisible({ timeout: 15000 });
    await expect(ultraBadge).toHaveText('ULTRA');

    const accountBtn = page.locator('[data-testid="account-switcher-btn"]');
    await expect(accountBtn).toBeVisible();
    await expect(accountBtn).toContainText('ULTRA');
  });

  test('R6.2 Mode Toggle switches between Chat and Task mode', async ({ page }) => {
    await page.goto('http://127.0.0.1:5173', { waitUntil: 'domcontentloaded' });
    const modeToggle = page.locator('[data-testid="header-mode-toggle"]');
    await expect(modeToggle).toBeVisible();

    const taskBtn = modeToggle.locator('button', { hasText: 'Task' });
    const chatBtn = modeToggle.locator('button', { hasText: 'Chat' });

    await expect(taskBtn).toBeVisible();
    await expect(chatBtn).toBeVisible();

    await taskBtn.click();
    await expect(taskBtn).toHaveClass(/border-blue-500/);

    await chatBtn.click();
    await expect(chatBtn).toHaveClass(/bg-zinc-800/);
  });

  test('R6.3 Full lifecycle: ULTRA account, 4 Argon High Reasoning, thought accordion, canvas and limits panels with 0 console errors', async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error' && !msg.text().includes('favicon.ico')) {
        consoleErrors.push(msg.text());
      }
    });
    page.on('pageerror', (err) => {
      consoleErrors.push(err.message);
    });

    // 1. Navigate to frontend daemon
    await page.goto('http://127.0.0.1:5173');
    await page.waitForSelector('[data-testid="virtual-chat-scroll-area"]', { timeout: 15000 });

    // 2. Verify active ULTRA profile (acc_21aac20f)
    await page.waitForSelector('[data-testid="tier-badge-ultra"]', { timeout: 15000 });
    const ultraBadge = page.locator('[data-testid="tier-badge-ultra"]').first();
    await expect(ultraBadge).toBeVisible();
    await expect(ultraBadge).toHaveText('ULTRA');

    // Verify account switcher dropdown indicates active account acc_21aac20f
    const accountSwitcherBtn = page.locator('[data-testid="account-switcher-btn"]');
    await accountSwitcherBtn.click();
    await page.waitForSelector('[data-testid="account-switcher-dropdown"]');
    const activeAccountRow = page.locator('[data-testid="account-item-acc_21aac20f"]');
    await expect(activeAccountRow).toBeVisible();
    await expect(activeAccountRow.locator('text=✓')).toBeVisible();
    // Close dropdown
    await accountSwitcherBtn.click();
    await page.waitForTimeout(200);

    // 3. Verify ModelSelector displays 4 Argon with High Reasoning
    const modelSelectorBtn = page.locator('[data-testid="model-selector-btn"]');
    await modelSelectorBtn.click();
    await page.waitForSelector('[data-testid="model-selector-dropdown"]');

    // Select 4 Argon (ID 95a4c0abcba24364)
    const argonOption = page.locator('[data-testid="model-option-95a4c0abcba24364"]');
    await expect(argonOption).toBeVisible();
    await argonOption.click();

    // Re-open to configure High Reasoning (level 2)
    await modelSelectorBtn.click();
    await page.waitForSelector('[data-testid="model-selector-dropdown"]');
    const highReasoningBtn = page.locator('[data-testid="reasoning-level-2"]');
    await highReasoningBtn.click();

    // Close dropdown by clicking backdrop
    const backdrop = page.locator('div.fixed.inset-0.z-40');
    if (await backdrop.isVisible()) {
      await backdrop.click();
    }
    await page.waitForTimeout(200);

    // Assert model selector button text
    await expect(modelSelectorBtn).toContainText('4 Argon');
    await expect(modelSelectorBtn).toContainText('High');

    // 4. Verify Reasoning accordion expands on response
    await page.evaluate(() => {
      const b3 = String.fromCharCode(96).repeat(3);
      const codeSnippet = 'def calculate_stress_metrics(rss_mb, duration_s):\n    return {"rss": rss_mb, "throughput": 1000 / duration_s}';
      const formattedContent =
        '### Deep Reasoning Output\n\nHere is the validated implementation:\n\n' +
        b3 + 'python\n' + codeSnippet + '\n' + b3 + '\n\nExecution verified.';

      (window as any).__loadBenchmarkMessages([
        {
          id: 501,
          conversation_id: 'conv_overnight_e2e_verify',
          parent_id: null,
          role: 'assistant',
          content: formattedContent,
          thoughts:
            'Analyzing computational complexity and memory bounds for overnight stress testing pipeline.\n' +
            'Ensuring zero leaks and strict O(1) buffer bounds.',
          model: '95a4c0abcba24364',
          created_at: Date.now(),
        },
      ]);
    });

    await page.waitForSelector('details', { timeout: 10000 });
    const details = page.locator('details').first();
    const summary = details.locator('summary');
    await expect(summary).toBeVisible();
    await expect(summary).toContainText('Thinking Process');

    // Click to expand accordion
    await summary.click();
    await expect(details).toHaveAttribute('open', '');
    await expect(details).toContainText('Analyzing computational complexity');

    // 5. Verify Canvas panel opens and closes
    const openCanvasBtn = page.locator('[data-testid="open-canvas-button"]').first();
    await expect(openCanvasBtn).toBeVisible();
    await openCanvasBtn.click();

    const canvasPanel = page.locator('[data-testid="canvas-panel"]');
    await expect(canvasPanel).toBeVisible();

    const canvasEditor = page.locator('[data-testid="canvas-editor-textarea"]');
    await expect(canvasEditor).toBeVisible();
    const editorValue = await canvasEditor.inputValue();
    expect(editorValue).toContain('calculate_stress_metrics');

    // Close canvas
    const closeCanvasBtn = page.locator('[data-testid="canvas-close-btn"]');
    await closeCanvasBtn.click();
    await expect(canvasPanel).toHaveCount(0);

    // 6. Verify Limits modal opens and closes
    const openLimitsBtn = page.locator('[data-testid="open-limits-btn"]');
    await expect(openLimitsBtn).toBeVisible();
    await openLimitsBtn.click();

    const limitsModal = page.locator('[data-testid="account-limits-modal"]');
    await expect(limitsModal).toBeVisible();
    await expect(limitsModal).toContainText('Account Limits & Quota Telemetry');

    // Close limits modal
    const closeLimitsBtn = page.locator('[data-testid="limits-close-btn"]');
    await closeLimitsBtn.click();
    await expect(limitsModal).toHaveCount(0);

    // 7. Verify zero JavaScript console errors occurred
    expect(consoleErrors).toEqual([]);
  });
});
