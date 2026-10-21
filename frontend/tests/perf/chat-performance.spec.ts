import { test, expect } from '@playwright/test';
import { generateBenchmarkFixture } from './fixture-generator';

test.describe('Milestone M3: Virtualization & DOM SLA Verification', () => {
  test('500-message thread (100+ code blocks & 60 LaTeX formulas): <180 DOM nodes and 0 long tasks during concurrent typing & streaming', async ({
    page,
  }) => {
    // 1. Install PerformanceObserver before loading scripts
    await page.addInitScript(() => {
      (window as any).__longTasks = [];
      const observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (entry.duration > 50) {
            (window as any).__longTasks.push({
              name: entry.name,
              duration: entry.duration,
              startTime: entry.startTime,
            });
          }
        }
      });
      observer.observe({ entryTypes: ['longtask'] });
    });

    await page.goto('/');
    await page.waitForSelector('[data-testid="virtual-chat-scroll-area"]', { timeout: 15000 });

    // 2. Inject 500 realistic messages with 100+ code blocks and 60 LaTeX formulas from fixture-generator
    const fixtureMessages = generateBenchmarkFixture(500);
    await page.evaluate((messages) => {
      (window as any).__loadBenchmarkMessages(messages);
    }, fixtureMessages);

    // Give virtual list a frame to layout and measure
    await page.waitForTimeout(300);

    // 3. Verify DOM SLA 1: Total document nodes must be strictly < 180 (including full Sidebar & Header)
    const totalDocumentNodes = await page.evaluate(() => document.querySelectorAll('*').length);
    console.log(`[Playwright Benchmark] Total Document DOM Nodes: ${totalDocumentNodes}`);
    expect(totalDocumentNodes).toBeLessThan(180);

    // 4. Verify DOM SLA 2: Virtual list container nodes must be < 140
    const listContainerNodes = await page.evaluate(() => {
      const el = document.querySelector('[data-testid="virtual-chat-scroll-area"]');
      return el ? el.querySelectorAll('*').length : 0;
    });
    console.log(`[Playwright Benchmark] Virtual List Container Nodes: ${listContainerNodes}`);
    expect(listContainerNodes).toBeLessThan(140);

    // 5. Stress Test: Rapid scrolling across 500 messages
    await page.evaluate(async () => {
      const scrollArea = document.querySelector('[data-testid="virtual-chat-container"]')!;
      const totalScroll = scrollArea.scrollHeight - scrollArea.clientHeight;
      const step = 350;

      for (let pos = 0; pos < totalScroll; pos += step) {
        scrollArea.scrollTop = pos;
        await new Promise((resolve) => requestAnimationFrame(resolve));
      }
    });

    // 6. Reset Long Tasks buffer prior to live streaming typing test
    await page.evaluate(() => {
      (window as any).__longTasks = [];
    });

    // Start mock high-speed assistant token generation (60 tokens)
    const streamPromise = page.evaluate(() => {
      if ((window as any).__simulateHighSpeedStream) {
        return (window as any).__simulateHighSpeedStream(60);
      }
      return Promise.resolve();
    });

    // Concurrently type 50+ keystrokes in the prompt textarea
    const textarea = page.locator('[data-testid="prompt-input-textarea"]');
    await textarea.focus();
    await textarea.type('Simultaneous rapid typing during active 60Hz streaming response token arrival.', {
      delay: 15,
    });

    await streamPromise;

    // 7. Hard Performance SLA: Assert ZERO Long Tasks (>50ms) during typing while streaming
    const capturedLongTasks: Array<{ name: string; duration: number }> = await page.evaluate(
      () => (window as any).__longTasks
    );
    console.log(`[Playwright Benchmark] Long Tasks (>50ms) during typing: ${capturedLongTasks.length}`);
    if (capturedLongTasks.length > 0) {
      console.error('Long tasks detail:', JSON.stringify(capturedLongTasks, null, 2));
    }
    expect(capturedLongTasks.length).toBe(0);
  });
});
