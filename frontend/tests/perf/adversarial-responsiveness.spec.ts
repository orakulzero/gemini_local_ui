import { test, expect } from '@playwright/test';

// Multiline realistic code snippets for Python, TypeScript, and Rust
const PYTHON_SNIPPET = `import asyncio
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field

@dataclass
class NodeRecord:
    node_id: str
    weight: float
    neighbors: List[str] = field(default_factory=list)
    tags: Dict[str, Any] = field(default_factory=dict)
    active: bool = True

class DistributedConsensusEngine:
    """Core distributed orchestration engine with quorum consistency."""
    def __init__(self, cluster_size: int = 128) -> None:
        self.cluster_size = cluster_size
        self.state_table: Dict[str, NodeRecord] = {}
        self._lock = asyncio.Lock()
        self.logger = logging.getLogger("consensus")

    async def register_node(self, node_id: str, neighbors: List[str]) -> bool:
        async with self._lock:
            if node_id in self.state_table:
                return False
            self.state_table[node_id] = NodeRecord(node_id=node_id, weight=1.0, neighbors=neighbors)
            self.logger.info("Node registered: %s", node_id)
            return True

    async def verify_quorum(self, votes: List[str]) -> Tuple[bool, int]:
        async with self._lock:
            valid_votes = sum(1 for v in votes if v in self.state_table and self.state_table[v].active)
            quorum_threshold = self.cluster_size // 2 + 1
            has_quorum = valid_votes >= quorum_threshold
            return has_quorum, valid_votes

    async def rebalance(self) -> None:
        async with self._lock:
            for node in self.state_table.values():
                node.weight = max(0.1, node.weight * 0.95)
`;

const TYPESCRIPT_SNIPPET = `export interface GraphNode<T = unknown> {
  id: string;
  payload: T;
  edges: Map<string, number>;
  visited: boolean;
  depth: number;
}

export type VisitorFn<T, R> = (node: GraphNode<T>, depth: number) => Promise<R>;

export class DirectedAcyclicGraph<T extends { id: string; priority: number }> {
  private nodes: Map<string, GraphNode<T>> = new Map();

  public addNode(item: T): void {
    if (!this.nodes.has(item.id)) {
      this.nodes.set(item.id, {
        id: item.id,
        payload: item,
        edges: new Map(),
        visited: false,
        depth: 0,
      });
    }
  }

  public addEdge(from: string, to: string, weight = 1.0): void {
    const source = this.nodes.get(from);
    if (source && this.nodes.has(to)) {
      source.edges.set(to, weight);
    }
  }

  public async topologicalSort(): Promise<string[]> {
    const inDegree = new Map<string, number>();
    for (const [id] of this.nodes) inDegree.set(id, 0);
    for (const [, node] of this.nodes) {
      for (const [target] of node.edges) {
        inDegree.set(target, (inDegree.get(target) || 0) + 1);
      }
    }
    const queue: string[] = [];
    for (const [id, deg] of inDegree) {
      if (deg === 0) queue.push(id);
    }
    const order: string[] = [];
    while (queue.length > 0) {
      const curr = queue.shift()!;
      order.push(curr);
      const node = this.nodes.get(curr);
      if (node) {
        for (const [target] of node.edges) {
          const newDeg = (inDegree.get(target) || 0) - 1;
          inDegree.set(target, newDeg);
          if (newDeg === 0) queue.push(target);
        }
      }
    }
    return order;
  }
}
`;

const RUST_SNIPPET = `use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use tokio::sync::mpsc::{channel, Receiver, Sender};

#[derive(Debug, Clone)]
pub struct MessageFrame {
    pub stream_id: u64,
    pub sequence: u32,
    pub payload: Vec<u8>,
    pub checksum: u32,
}

pub struct AsyncPipelineManager {
    capacity: usize,
    active_count: Arc<AtomicUsize>,
    tx: Sender<MessageFrame>,
}

impl AsyncPipelineManager {
    pub fn new(capacity: usize) -> (Self, Receiver<MessageFrame>) {
        let (tx, rx) = channel(capacity);
        let manager = Self {
            capacity,
            active_count: Arc::new(AtomicUsize::new(0)),
            tx,
        };
        (manager, rx)
    }

    pub async fn dispatch(&self, frame: MessageFrame) -> Result<(), &'static str> {
        self.active_count.fetch_add(1, Ordering::SeqCst);
        self.tx.send(frame).await.map_err(|_| "Channel buffer closed")?;
        Ok(())
    }

    pub fn active_tasks(&self) -> usize {
        self.active_count.load(Ordering::Relaxed)
    }
}
`;

function buildStreamingChunks(): string[] {
  const chunks: string[] = [
    '### Adversarial 100Hz Multi-Language Stream Benchmark\n\n',
    'Executing high-frequency token streaming and offloading syntax compilation to Web Worker.\n\n',
    '```python\n',
  ];

  for (const line of PYTHON_SNIPPET.split('\n')) {
    chunks.push(line + '\n');
  }
  chunks.push('```\n\n');
  chunks.push('Intermediate explanation paragraph analyzing memory layouts and DAG ordering:\n\n');
  chunks.push('```typescript\n');

  for (const line of TYPESCRIPT_SNIPPET.split('\n')) {
    chunks.push(line + '\n');
  }
  chunks.push('```\n\n');
  chunks.push('Final architectural section detailing Rust atomic synchronization primitives:\n\n');
  chunks.push('```rust\n');

  for (const line of RUST_SNIPPET.split('\n')) {
    chunks.push(line + '\n');
  }
  chunks.push('```\n\n');
  chunks.push('All 3 multi-language code snippets streamed successfully at 100Hz.\n');

  return chunks;
}

test.describe('Milestone M3 Adversarial Challenge: Main-Thread Responsiveness & Zero Long Tasks', () => {
  test.beforeEach(async ({ page }) => {
    // Install PerformanceObserver for Long Tasks (>50ms) before scripts execute
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
  });

  test('Primary Stress Run: 100Hz streaming with Python, TS, Rust snippets + 100 rapid concurrent keystrokes', async ({
    page,
  }) => {
    await page.goto('/?benchmark=true');
    await page.waitForSelector('[data-testid="virtual-chat-scroll-area"]', { timeout: 15000 });

    // Load 500 dynamic-height messages to establish realistic virtualized chat memory pressure
    await page.evaluate(() => {
      const sampleMessages = Array.from({ length: 500 }, (_, i) => ({
        id: i + 1,
        conversation_id: 'conv_bench',
        parent_id: i > 0 ? i : null,
        role: (i % 2 === 0 ? 'user' : 'assistant') as 'user' | 'assistant',
        content: `Message ${i}: ${
          i % 5 === 0
            ? 'Long architectural response with multiline explanations and code examples.\nLine 2\nLine 3\nLine 4'
            : 'Short conversational turn.'
        }`,
        created_at: Date.now() - (500 - i) * 1000,
        model: 'gemini-2.5-pro',
        thought: i % 7 === 0 ? 'Evaluating constraint tree and calculating DOM budget.' : undefined,
        codeBlocks:
          i % 4 === 0
            ? [{ language: 'typescript', code: 'export const run = () => {\n  return 42;\n};' }]
            : undefined,
      }));
      (window as any).__loadBenchmarkMessages(sampleMessages);
    });

    await page.waitForTimeout(400);

    // Verify initial DOM constraint (< 180)
    const initialDomNodes = await page.evaluate(() => document.querySelectorAll('*').length);
    console.log(`[Adversarial Test 1] Initial Document DOM Nodes: ${initialDomNodes}`);
    expect(initialDomNodes).toBeLessThan(180);

    // Setup Keystroke Instrumentation
    await page.evaluate(() => {
      const textarea = document.querySelector('[data-testid="prompt-input-textarea"]') as HTMLTextAreaElement;
      if (!textarea) throw new Error('Textarea not found');

      (window as any).__keystrokeEvents = [];

      let lastKeydownTime = 0;
      let lastKey = '';

      textarea.addEventListener('keydown', (e) => {
        lastKeydownTime = performance.now();
        lastKey = e.key;
      });

      textarea.addEventListener('input', (e) => {
        const handledTime = performance.now();
        const latency = lastKeydownTime > 0 ? handledTime - lastKeydownTime : 0;
        (window as any).__keystrokeEvents.push({
          key: lastKey,
          valueLength: textarea.value.length,
          latencyMs: latency,
          timestamp: handledTime,
        });
      });
    });

    // Reset Long Tasks buffer right before launching adversarial workload
    await page.evaluate(() => {
      (window as any).__longTasks = [];
    });

    const chunks = buildStreamingChunks();
    console.log(`[Adversarial Test 1] Total streaming tokens prepared: ${chunks.length}`);

    // Launch 100Hz Streaming in the browser page (10ms interval)
    const streamPromise = page.evaluate((tokens) => {
      return new Promise<void>((resolve) => {
        const store = (window as any).__chatStore.getState();
        const assistantId = 888888;
        const currentThread = [...store.activeThread];

        currentThread.push({
          id: assistantId,
          conversation_id: store.activeConversationId || 'conv_perf_bench',
          parent_id: currentThread.length > 0 ? currentThread[currentThread.length - 1].id : null,
          role: 'assistant',
          content: '',
          thoughts: 'Adversarial 100Hz streaming with Python, TypeScript, and Rust multi-language code snippets.',
          model: 'gemini-2.5-pro',
          created_at: Date.now(),
        });

        (window as any).__chatStore.setState({
          activeThread: currentThread,
          isGenerating: true,
          streamingText: '',
        });

        let index = 0;
        let accumulated = '';

        const timer = setInterval(() => {
          if (index >= tokens.length) {
            clearInterval(timer);
            (window as any).__chatStore.setState({ isGenerating: false });
            resolve();
            return;
          }

          accumulated += tokens[index];
          index++;

          (window as any).__chatStore.setState((s: any) => ({
            streamingText: accumulated,
            activeThread: s.activeThread.map((m: any) =>
              m.id === assistantId ? { ...m, content: accumulated } : m
            ),
          }));
        }, 10); // 100Hz = 10ms
      });
    }, chunks);

    // Concurrently simulate 100 rapid keystrokes into prompt textarea during active streaming
    const textarea = page.locator('[data-testid="prompt-input-textarea"]');
    await textarea.focus();

    const testString100 = 'TheQuickBrownFoxJumpsOverTheLazyDog_1234567890_ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqrstuvwxyz';
    expect(testString100.length).toBe(100);

    // Type with 12ms delay per keystroke so 100 keystrokes overlap continuously with 100Hz stream
    await textarea.pressSequentially(testString100, { delay: 12 });

    // Wait for the stream to conclude
    await streamPromise;

    // 1. Assert Keystroke Fidelity (Zero Dropped Keystrokes)
    const finalInputValue = await textarea.inputValue();
    console.log(`[Adversarial Test 1] Textarea input value length: ${finalInputValue.length} (Expected: 100)`);
    expect(finalInputValue).toBe(testString100);
    expect(finalInputValue.length).toBe(100);

    // 2. Evaluate Keystroke Event Latencies
    const keystrokeMetrics: Array<{
      key: string;
      valueLength: number;
      latencyMs: number;
      timestamp: number;
    }> = await page.evaluate(() => (window as any).__keystrokeEvents);

    console.log(`[Adversarial Test 1] Recorded Keystroke Events Count: ${keystrokeMetrics.length}`);
    expect(keystrokeMetrics.length).toBe(100);

    const maxKeystrokeLatency = Math.max(...keystrokeMetrics.map((k) => k.latencyMs));
    const avgKeystrokeLatency =
      keystrokeMetrics.reduce((sum, k) => sum + k.latencyMs, 0) / keystrokeMetrics.length;

    console.log(`[Adversarial Test 1] Max Keystroke Latency: ${maxKeystrokeLatency.toFixed(2)}ms`);
    console.log(`[Adversarial Test 1] Avg Keystroke Latency: ${avgKeystrokeLatency.toFixed(2)}ms`);

    // Verify that keystrokes are never delayed by more than 16ms
    expect(maxKeystrokeLatency).toBeLessThanOrEqual(16);

    // 3. Hard SLA: Assert ZERO Long Tasks (>50ms) on Main Thread throughout the entire run
    const longTasks: Array<{ name: string; duration: number; startTime: number }> =
      await page.evaluate(() => (window as any).__longTasks);

    console.log(`[Adversarial Test 1] Long Tasks (>50ms) Count: ${longTasks.length}`);
    if (longTasks.length > 0) {
      console.error(
        '[Adversarial Test 1] Long tasks detected:',
        JSON.stringify(longTasks, null, 2)
      );
    }
    expect(longTasks.length).toBe(0);

    // 4. Verify Code Blocks hydrated with syntax highlighting
    const codeBlockCount = await page.evaluate(() => {
      return document.querySelectorAll('.hljs, pre code').length;
    });
    console.log(`[Adversarial Test 1] Rendered Code Blocks: ${codeBlockCount}`);
    expect(codeBlockCount).toBeGreaterThanOrEqual(3);
  });

  test('High-Velocity Burst Typing (100 rapid keystrokes with 5ms delay) during 100Hz token arrival', async ({
    page,
  }) => {
    await page.goto('/?benchmark=true');
    await page.waitForSelector('[data-testid="virtual-chat-scroll-area"]', { timeout: 15000 });

    await page.evaluate(() => {
      const sampleMessages = Array.from({ length: 200 }, (_, i) => ({
        id: i + 1,
        conversation_id: 'conv_burst',
        parent_id: i > 0 ? i : null,
        role: (i % 2 === 0 ? 'user' : 'assistant') as 'user' | 'assistant',
        content: `Message ${i}`,
        created_at: Date.now() - (200 - i) * 1000,
        model: 'gemini-2.5-pro',
      }));
      (window as any).__loadBenchmarkMessages(sampleMessages);
    });

    await page.waitForTimeout(300);

    // Setup Keystroke Instrumentation
    await page.evaluate(() => {
      const textarea = document.querySelector('[data-testid="prompt-input-textarea"]') as HTMLTextAreaElement;
      (window as any).__burstKeyEvents = [];
      let lastTime = 0;

      textarea.addEventListener('keydown', () => {
        lastTime = performance.now();
      });

      textarea.addEventListener('input', () => {
        const now = performance.now();
        (window as any).__burstKeyEvents.push({
          len: textarea.value.length,
          latency: lastTime > 0 ? now - lastTime : 0,
        });
      });

      (window as any).__longTasks = [];
    });

    const chunks = buildStreamingChunks();

    // Launch streaming at 100Hz (10ms)
    const streamPromise = page.evaluate((tokens) => {
      return new Promise<void>((resolve) => {
        const store = (window as any).__chatStore.getState();
        const assistantId = 777777;
        const currentThread = [...store.activeThread];

        currentThread.push({
          id: assistantId,
          conversation_id: store.activeConversationId || 'conv_burst',
          parent_id: currentThread.length > 0 ? currentThread[currentThread.length - 1].id : null,
          role: 'assistant',
          content: '',
          thoughts: 'Burst typing test.',
          model: 'gemini-2.5-pro',
          created_at: Date.now(),
        });

        (window as any).__chatStore.setState({
          activeThread: currentThread,
          isGenerating: true,
          streamingText: '',
        });

        let idx = 0;
        let acc = '';

        const timer = setInterval(() => {
          if (idx >= tokens.length) {
            clearInterval(timer);
            (window as any).__chatStore.setState({ isGenerating: false });
            resolve();
            return;
          }
          acc += tokens[idx];
          idx++;
          (window as any).__chatStore.setState((s: any) => ({
            streamingText: acc,
            activeThread: s.activeThread.map((m: any) =>
              m.id === assistantId ? { ...m, content: acc } : m
            ),
          }));
        }, 10);
      });
    }, chunks);

    const textarea = page.locator('[data-testid="prompt-input-textarea"]');
    await textarea.focus();

    // 100 rapid characters typed with ultra-fast 5ms delay
    const burstString = '0123456789'.repeat(10);
    expect(burstString.length).toBe(100);

    await textarea.pressSequentially(burstString, { delay: 5 });

    await streamPromise;

    // Check textarea value
    const finalVal = await textarea.inputValue();
    console.log(`[Adversarial Test 2 - Burst] Textarea length: ${finalVal.length} (Expected: 100)`);
    expect(finalVal).toBe(burstString);

    const burstEvents: Array<{ len: number; latency: number }> =
      await page.evaluate(() => (window as any).__burstKeyEvents);
    expect(burstEvents.length).toBe(100);

    const maxLatency = Math.max(...burstEvents.map((e) => e.latency));
    console.log(`[Adversarial Test 2 - Burst] Max Keystroke Latency: ${maxLatency.toFixed(2)}ms`);
    expect(maxLatency).toBeLessThanOrEqual(16);

    const longTasks: Array<{ name: string; duration: number }> =
      await page.evaluate(() => (window as any).__longTasks);
    console.log(`[Adversarial Test 2 - Burst] Long Tasks (>50ms): ${longTasks.length}`);
    expect(longTasks.length).toBe(0);
  });

  test('Concurrent Rapid Scrolling + 100Hz Streaming + 100 Keystrokes Stress Test', async ({
    page,
  }) => {
    await page.goto('/?benchmark=true');
    await page.waitForSelector('[data-testid="virtual-chat-scroll-area"]', { timeout: 15000 });

    // Load 500 messages
    await page.evaluate(() => {
      const sampleMessages = Array.from({ length: 500 }, (_, i) => ({
        id: i + 1,
        conversation_id: 'conv_scroll_bench',
        parent_id: i > 0 ? i : null,
        role: (i % 2 === 0 ? 'user' : 'assistant') as 'user' | 'assistant',
        content: `Message ${i}: Architectural specification and continuous analysis.\nLine 2\nLine 3`,
        created_at: Date.now() - (500 - i) * 1000,
        model: 'gemini-2.5-pro',
      }));
      (window as any).__loadBenchmarkMessages(sampleMessages);
    });

    await page.waitForTimeout(400);

    // Reset Long Tasks buffer right before launching adversarial workload
    await page.evaluate(() => {
      (window as any).__longTasks = [];
    });

    const chunks = buildStreamingChunks();

    // 1. Launch 100Hz streaming
    const streamPromise = page.evaluate((tokens) => {
      return new Promise<void>((resolve) => {
        const store = (window as any).__chatStore.getState();
        const assistantId = 666666;
        const currentThread = [...store.activeThread];

        currentThread.push({
          id: assistantId,
          conversation_id: store.activeConversationId || 'conv_scroll_bench',
          parent_id: currentThread.length > 0 ? currentThread[currentThread.length - 1].id : null,
          role: 'assistant',
          content: '',
          thoughts: 'Scroll + streaming + typing stress.',
          model: 'gemini-2.5-pro',
          created_at: Date.now(),
        });

        (window as any).__chatStore.setState({
          activeThread: currentThread,
          isGenerating: true,
          streamingText: '',
        });

        let idx = 0;
        let acc = '';

        const timer = setInterval(() => {
          if (idx >= tokens.length) {
            clearInterval(timer);
            (window as any).__chatStore.setState({ isGenerating: false });
            resolve();
            return;
          }
          acc += tokens[idx];
          idx++;
          (window as any).__chatStore.setState((s: any) => ({
            streamingText: acc,
            activeThread: s.activeThread.map((m: any) =>
              m.id === assistantId ? { ...m, content: acc } : m
            ),
          }));
        }, 10);
      });
    }, chunks);

    // 2. Launch concurrent rapid scrolling across virtual list in background
    const scrollPromise = page.evaluate(async () => {
      const scrollArea = document.querySelector('[data-testid="virtual-chat-container"]');
      if (!scrollArea) return;
      const total = scrollArea.scrollHeight - scrollArea.clientHeight;
      const step = 300;
      // Scroll down
      for (let p = 0; p < total; p += step) {
        scrollArea.scrollTop = p;
        await new Promise((r) => requestAnimationFrame(r));
      }
      // Scroll back up
      for (let p = total; p >= 0; p -= step) {
        scrollArea.scrollTop = p;
        await new Promise((r) => requestAnimationFrame(r));
      }
    });

    // 3. Concurrently type 100 keystrokes
    const textarea = page.locator('[data-testid="prompt-input-textarea"]');
    await textarea.focus();

    const scrollTypeString = 'RapidScrollAndStreamAndType_1234567890_ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqrstuvwxyz_01234567'.slice(0, 100);
    expect(scrollTypeString.length).toBe(100);

    await textarea.pressSequentially(scrollTypeString, { delay: 10 });

    await Promise.all([streamPromise, scrollPromise]);

    // Check textarea value
    const finalVal = await textarea.inputValue();
    console.log(`[Adversarial Test 3 - Scroll+Type] Input length: ${finalVal.length} (Expected: 100)`);
    expect(finalVal).toBe(scrollTypeString);

    // Check Long Tasks
    const longTasks: Array<{ name: string; duration: number }> =
      await page.evaluate(() => (window as any).__longTasks);
    console.log(`[Adversarial Test 3 - Scroll+Type] Long Tasks (>50ms): ${longTasks.length}`);
    if (longTasks.length > 0) {
      console.log('[Adversarial Test 3 - Scroll+Type] Long Tasks Details:', JSON.stringify(longTasks, null, 2));
    }
    expect(longTasks.length).toBe(0);
  });
});
