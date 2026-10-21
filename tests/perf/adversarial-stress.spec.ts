import { test, expect } from '@playwright/test';

test.describe('Adversarial Stress Test: Virtualization, DOM Budget & Layout Stability', () => {
  test('1,000+ extreme variable height messages under rapid scroll thrashing', async ({ page }) => {
    // 1. Capture browser console errors and page crashes
    const pageErrors: string[] = [];
    const jsErrors: string[] = [];
    page.on('pageerror', (err) => jsErrors.push(`[PageError] ${err.message}`));
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        const text = msg.text();
        if (!text.includes('Failed to load resource') && !text.includes('ECONNREFUSED')) {
          jsErrors.push(`[ConsoleError] ${text}`);
        }
      }
    });

    // Install PerformanceObserver to track long tasks (>50ms)
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

    await page.goto('/?benchmark=true');
    await page.waitForSelector('[data-testid="virtual-chat-scroll-area"]', { timeout: 15000 });

    // 2. Generate 1,050 adversarial messages with extreme variable heights (20px to 2,000px)
    await page.evaluate(() => {
      const messages: any[] = [];
      const totalMessages = 1050;

      // 100-line code block to force ~2,000px height
      const massiveCode = Array.from({ length: 95 }, (_, lineIdx) =>
        `  const step_${lineIdx} = async (input: number) => computeMatrixTransform(input * ${lineIdx}, { debug: true });`
      ).join('\n');

      const complexMathFormula = `\\begin{pmatrix} \\frac{\\partial^2 \\psi}{\\partial x^2} & \\nabla \\cdot \\mathbf{E} \\\\ \\oint_{\\mathcal{C}} \\mathbf{B} \\cdot d\\boldsymbol{\\ell} & \\sum_{k=1}^{\\infty} \\frac{(-1)^k}{k^2 + 1} \\end{pmatrix} = \\mathbf{\\Omega}_{\\mu\\nu}`;

      for (let i = 1; i <= totalMessages; i++) {
        const isAssistant = i % 2 === 0;
        let content = '';
        let thought: string | undefined = undefined;
        let codeBlocks: any[] | undefined = undefined;

        if (i >= 200 && i <= 230) {
          // Cluster of 31 consecutive ultra-short messages (~20-30px each)
          content = `Short msg ${i}`;
        } else if (i % 50 === 0) {
          // Extreme 2,000px height message: massive code + multi-paragraph markdown + math
          content = `### Extreme Variable Block #${i}\n\nAnalyzing dense theoretical construct with extreme viewport consumption.\n\n$$\n${complexMathFormula}\n$$\n\nDetailed breakdown of the 100-step algorithm:`;
          thought = 'Extensive theoretical verification tree spanning 500 tokens of architectural justification and state proofs.';
          codeBlocks = [
            {
              language: 'typescript',
              code: `// Extreme scale benchmark harness item #${i}\nexport async function executeExtremeTransform_${i}() {\n${massiveCode}\n  return step_94(42);\n}`,
            },
          ];
        } else if (i % 15 === 0) {
          // Heavy KaTeX math formula display block
          content = `Display Math #${i}:\n\n$$\n${complexMathFormula}\n$$\n\n$$\n\\int_{-\\infty}^{\\infty} e^{-x^2} dx = \\sqrt{\\pi}\n$$`;
        } else if (i % 7 === 0) {
          // Medium code block (20-30 lines)
          content = `Standard implementation block #${i}:\n\`\`\`python\ndef process_batch(items):\n    for item in items:\n        yield item * 2\n\`\`\``;
          thought = 'Thinking about generator performance.';
        } else if (i % 3 === 0) {
          // Short answer
          content = `Direct concise answer #${i}: Confirmed and verified.`;
        } else {
          // Normal conversational text
          content = `Conversation turn #${i}: Exploring conversational DAG topology and branch node transitions across multiple context iterations.`;
        }

        messages.push({
          id: i,
          conversation_id: 'conv_adversarial',
          parent_id: i > 1 ? i - 1 : null,
          role: isAssistant ? 'assistant' : 'user',
          content,
          created_at: Date.now() - (totalMessages - i) * 500,
          model: isAssistant ? 'gemini-2.5-pro' : undefined,
          thought,
          codeBlocks,
        });
      }

      (window as any).__loadBenchmarkMessages(messages);
    });

    // Allow browser 500ms to mount and observe initial layout
    await page.waitForTimeout(500);

    // 3. Check for any initial NaN values in DOM or state
    const initialNaNCheck = await page.evaluate(() => {
      const container = document.querySelector('[data-testid="virtual-chat-container"]') as HTMLElement;
      const phantom = document.querySelector('[data-testid="virtual-chat-phantom"]') as HTMLElement;
      const items = Array.from(document.querySelectorAll('[data-id]')) as HTMLElement[];

      const nanItems = items.filter((el) => {
        const transform = el.style.transform || '';
        return transform.includes('NaN') || transform.includes('undefined');
      });

      return {
        phantomHeight: phantom?.style?.height,
        isPhantomNaN: phantom?.style?.height?.includes('NaN') || false,
        nanItemCount: nanItems.length,
        containerScrollTop: container?.scrollTop,
        isScrollTopNaN: Number.isNaN(container?.scrollTop),
      };
    });

    console.log('[Adversarial] Initial NaN check:', JSON.stringify(initialNaNCheck));
    expect(initialNaNCheck.isPhantomNaN).toBe(false);
    expect(initialNaNCheck.nanItemCount).toBe(0);
    expect(initialNaNCheck.isScrollTopNaN).toBe(false);

    // 4. Perform Rapid Scroll Thrashing (20 cycles across extreme positions)
    console.log('[Adversarial] Beginning 20 rapid scroll thrashing cycles...');
    const domMeasurements: Array<{
      cycle: number;
      step: string;
      scrollTop: number;
      totalNodes: number;
      containerNodes: number;
      renderedItemCount: number;
      hasNaN: boolean;
    }> = [];

    for (let cycle = 1; cycle <= 20; cycle++) {
      // Sequence of rapid scroll targets in one cycle
      const targets = [
        { name: 'bottom', fraction: 1.0 },
        { name: 'top', fraction: 0.0 },
        { name: 'middle', fraction: 0.5 },
        { name: 'short-cluster (approx 20%)', fraction: 0.2 },
        { name: 'extreme-cluster (approx 80%)', fraction: 0.8 },
      ];

      for (const target of targets) {
        const measurement = await page.evaluate((fraction) => {
          const container = document.querySelector('[data-testid="virtual-chat-container"]') as HTMLElement;
          const scrollArea = document.querySelector('[data-testid="virtual-chat-scroll-area"]') as HTMLElement;
          if (!container) return null;

          const maxScroll = container.scrollHeight - container.clientHeight;
          const targetScrollTop = Math.floor(maxScroll * fraction);
          container.scrollTop = targetScrollTop;

          // Query active DOM nodes immediately after scroll event dispatch
          const totalNodes = document.querySelectorAll('*').length;
          const containerNodes = scrollArea ? scrollArea.querySelectorAll('*').length : 0;
          const renderedItems = document.querySelectorAll('[data-id]');

          let hasNaN = false;
          renderedItems.forEach((el) => {
            const tf = (el as HTMLElement).style.transform || '';
            if (tf.includes('NaN') || tf.includes('undefined')) hasNaN = true;
          });

          return {
            scrollTop: container.scrollTop,
            totalNodes,
            containerNodes,
            renderedItemCount: renderedItems.length,
            hasNaN,
          };
        }, target.fraction);

        if (measurement) {
          domMeasurements.push({
            cycle,
            step: target.name,
            ...measurement,
          });
        }
      }

      // Small pause every 5 cycles to allow ResizeObserver / RAF batching
      if (cycle % 5 === 0) {
        await page.waitForTimeout(50);
      }
    }

    // 5. Evaluate DOM node budget across all 100 measurements
    const maxTotalNodes = Math.max(...domMeasurements.map((m) => m.totalNodes));
    const minTotalNodes = Math.min(...domMeasurements.map((m) => m.totalNodes));
    const maxContainerNodes = Math.max(...domMeasurements.map((m) => m.containerNodes));
    const maxRenderedItems = Math.max(...domMeasurements.map((m) => m.renderedItemCount));
    const anyNaN = domMeasurements.some((m) => m.hasNaN);

    console.log(`[Adversarial] DOM Node Budget Results over 100 thrashing measurements:`);
    console.log(`  - Peak Total Document Nodes: ${maxTotalNodes} (Budget SLA: <= 180)`);
    console.log(`  - Min Total Document Nodes: ${minTotalNodes}`);
    console.log(`  - Peak Container Nodes: ${maxContainerNodes} (Budget SLA: < 140)`);
    console.log(`  - Peak Rendered Items: ${maxRenderedItems}`);
    console.log(`  - Any NaN in Transforms: ${anyNaN}`);

    // Find any measurement that violated the 180 DOM node budget
    const budgetViolations = domMeasurements.filter((m) => m.totalNodes > 180);
    if (budgetViolations.length > 0) {
      console.error(
        `[Adversarial ALERT] ${budgetViolations.length} measurements exceeded 180 DOM nodes! First violation:`,
        JSON.stringify(budgetViolations[0], null, 2)
      );

      // Inspect tag breakdown at the peak node position
      const tagBreakdown = await page.evaluate(() => {
        const counts: Record<string, number> = {};
        document.querySelectorAll('*').forEach((el) => {
          const tag = el.tagName.toLowerCase();
          counts[tag] = (counts[tag] || 0) + 1;
        });
        return counts;
      });
      console.log('[Adversarial Node Breakdown by Tag]:', JSON.stringify(tagBreakdown, null, 2));
    }

    // 6. Test Specific High-Risk Cluster: The 31 consecutive short messages (id 200..230)
    console.log('[Adversarial] Testing Short Message Cluster (items 200-230)...');
    const clusterMeasurement = await page.evaluate(() => {
      const container = document.querySelector('[data-testid="virtual-chat-container"]') as HTMLElement;
      const scrollArea = document.querySelector('[data-testid="virtual-chat-scroll-area"]') as HTMLElement;

      // Scroll specifically to around item 200 (estimated ~24,000px)
      container.scrollTop = 24000;

      const totalNodes = document.querySelectorAll('*').length;
      const containerNodes = scrollArea ? scrollArea.querySelectorAll('*').length : 0;
      const renderedItems = Array.from(document.querySelectorAll('[data-id]')).map((el) => {
        const id = el.getAttribute('data-id');
        const index = el.getAttribute('data-index');
        const rect = el.getBoundingClientRect();
        return { id, index, top: rect.top, height: rect.height };
      });

      return {
        totalNodes,
        containerNodes,
        renderedItems,
      };
    });

    console.log(
      `[Adversarial] Short Cluster Active Nodes: Total=${clusterMeasurement.totalNodes}, Container=${clusterMeasurement.containerNodes}, Rendered Count=${clusterMeasurement.renderedItems.length}`
    );

    // 7. Test Layout Overlap & Collision Oracle around Extreme 2,000px Message (item 250)
    console.log('[Adversarial] Testing Layout Collision Oracle around item 250 (2,000px code block)...');
    const collisionCheck = await page.evaluate(async () => {
      const container = document.querySelector('[data-testid="virtual-chat-container"]') as HTMLElement;
      // Scroll to item 250: 250 * 120 = 30000
      container.scrollTop = 30000;

      // Wait 100ms for ResizeObserver
      await new Promise((r) => setTimeout(r, 100));

      const items = Array.from(document.querySelectorAll('[data-id]')) as HTMLElement[];
      const rects = items.map((el) => {
        const rect = el.getBoundingClientRect();
        return {
          id: el.getAttribute('data-id'),
          index: Number(el.getAttribute('data-index')),
          top: rect.top,
          bottom: rect.bottom,
          height: rect.height,
          transform: el.style.transform,
        };
      });

      // Check for vertical collisions: if item i's bottom > item i+1's top by > 2px
      const collisions: any[] = [];
      for (let i = 0; i < rects.length - 1; i++) {
        const current = rects[i];
        const next = rects[i + 1];
        if (current.bottom - next.top > 2) {
          collisions.push({
            currentId: current.id,
            nextId: next.id,
            overlapPx: current.bottom - next.top,
            currentHeight: current.height,
            currentTransform: current.transform,
            nextTransform: next.transform,
          });
        }
      }

      return {
        itemCount: items.length,
        rects,
        collisions,
      };
    });

    console.log(
      `[Adversarial] Layout Collision Check: Rendered items=${collisionCheck.itemCount}, Overlap Collisions=${collisionCheck.collisions.length}`
    );
    if (collisionCheck.collisions.length > 0) {
      console.warn('[Adversarial WARNING] Layout collisions detected:', JSON.stringify(collisionCheck.collisions, null, 2));
    }

    // 8. Test DOM Detachment Leaks: Scroll to end and back to 0, verify DOM node count returns to baseline
    console.log('[Adversarial] Testing DOM Detachment & Leakage...');
    await page.evaluate(() => {
      const container = document.querySelector('[data-testid="virtual-chat-container"]') as HTMLElement;
      container.scrollTop = 0;
    });
    await page.waitForTimeout(200);

    const finalNodeCheck = await page.evaluate(() => {
      const totalNodes = document.querySelectorAll('*').length;
      const containerNodes = document.querySelector('[data-testid="virtual-chat-scroll-area"]')?.querySelectorAll('*').length ?? 0;
      const renderedCount = document.querySelectorAll('[data-id]').length;
      return { totalNodes, containerNodes, renderedCount };
    });

    console.log(`[Adversarial] Final Node Count at scrollTop=0: Total=${finalNodeCheck.totalNodes}, Container=${finalNodeCheck.containerNodes}, Rendered=${finalNodeCheck.renderedCount}`);

    // Assert zero uncaught page errors
    expect(jsErrors).toEqual([]);

    // Assert zero NaN occurrences
    expect(anyNaN).toBe(false);

    // Assert zero layout collisions
    expect(collisionCheck.collisions).toEqual([]);

    // Assert Hard DOM SLA: active DOM nodes NEVER exceed 180 elements under any viewport offset or scroll velocity
    expect(maxTotalNodes).toBeLessThanOrEqual(180);
    expect(maxContainerNodes).toBeLessThan(140);
  });

  test('Thinking accordion expansion layout shift & overlap check', async ({ page }) => {
    await page.goto('/?benchmark=true');
    await page.waitForSelector('[data-testid="virtual-chat-scroll-area"]', { timeout: 15000 });

    // Load 10 messages where message 1 has a thought accordion
    await page.evaluate(() => {
      const messages = [
        {
          id: 1,
          conversation_id: 'conv_accordion',
          parent_id: null,
          role: 'assistant',
          content: 'Here is the preliminary architecture.',
          thought: 'Detailed thinking trace step 1\nStep 2\nStep 3\nStep 4\nStep 5\nStep 6\nStep 7\nStep 8\nStep 9\nStep 10',
          created_at: Date.now() - 5000,
        },
        {
          id: 2,
          conversation_id: 'conv_accordion',
          parent_id: 1,
          role: 'user',
          content: 'Does this meet the specification?',
          created_at: Date.now() - 4000,
        },
        {
          id: 3,
          conversation_id: 'conv_accordion',
          parent_id: 2,
          role: 'assistant',
          content: 'Yes, it complies with all constraints.',
          created_at: Date.now() - 3000,
        },
      ];
      (window as any).__loadBenchmarkMessages(messages);
    });

    await page.waitForTimeout(300);

    // Measure positions before opening details
    const beforeOpen = await page.evaluate(() => {
      const msg1 = document.querySelector('[data-id="1"]') as HTMLElement;
      const msg2 = document.querySelector('[data-id="2"]') as HTMLElement;
      return {
        msg1Height: msg1.getBoundingClientRect().height,
        msg1Transform: msg1.style.transform,
        msg2Transform: msg2.style.transform,
        msg2Top: msg2.getBoundingClientRect().top,
      };
    });

    console.log('[Accordion Test] Before opening details:', JSON.stringify(beforeOpen));

    // Open details on message 1
    const summary = page.locator('[data-id="1"] details summary');
    await summary.click();
    await page.waitForTimeout(200);

    // Measure positions after opening details
    const afterOpen = await page.evaluate(() => {
      const msg1 = document.querySelector('[data-id="1"]') as HTMLElement;
      const msg2 = document.querySelector('[data-id="2"]') as HTMLElement;
      const r1 = msg1.getBoundingClientRect();
      const r2 = msg2.getBoundingClientRect();
      return {
        msg1Height: r1.height,
        msg1Bottom: r1.bottom,
        msg1Transform: msg1.style.transform,
        msg2Top: r2.top,
        msg2Transform: msg2.style.transform,
        overlapPx: r1.bottom - r2.top,
      };
    });

    console.log('[Accordion Test] After opening details:', JSON.stringify(afterOpen));
    expect(afterOpen.overlapPx).toBeLessThanOrEqual(2);
  });
});
