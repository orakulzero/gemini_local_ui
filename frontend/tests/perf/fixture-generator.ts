import { Message } from '../../src/types';

export function generateBenchmarkFixture(totalMessages = 500): Message[] {
  const messages: Message[] = [];
  const languages = ['typescript', 'python', 'rust', 'go', 'sql', 'bash', 'json'];
  const formulas = [
    '\\int_{-\\infty}^{\\infty} e^{-x^2} dx = \\sqrt{\\pi}',
    'i\\hbar \\frac{\\partial}{\\partial t}\\Psi(\\mathbf{r},t) = \\hat{H}\\Psi(\\mathbf{r},t)',
    '\\nabla \\times \\mathbf{B} = \\mu_0\\mathbf{J} + \\mu_0\\epsilon_0\\frac{\\partial \\mathbf{E}}{\\partial t}',
    'f(x) = \\sum_{n=0}^{\\infty} \\frac{f^{(n)}(a)}{n!} (x-a)^n',
  ];

  let codeBlockCount = 0;
  let formulaCount = 0;
  let parentId: number | null = null;

  for (let i = 1; i <= totalMessages; i++) {
    const isAssistant = i % 2 === 0;
    const msgId = i;
    let content = '';
    let thoughts: string | null = null;

    if (!isAssistant) {
      content = `User Prompt ${i}: Please provide the technical architecture, mathematical derivation, and performance characteristics for component module ${i}.`;
    } else {
      if (i % 8 === 0) {
        thoughts = `Reasoning trace for turn ${i}:\n- Analyzing requirements for component ${i}\n- Verifying memory boundaries and algorithmic bounds\n- Synthesizing mathematical proofs.`;
      }

      content = `### Architectural Response ${i}\n\nHere is the detailed specification for component ${i}.\n\n`;

      if (codeBlockCount < 120 && (i % 4 === 0 || i % 6 === 0)) {
        codeBlockCount++;
        const lang = languages[codeBlockCount % languages.length];
        content += `\`\`\`${lang}\n// Implementation snippet for module ${i}\nfunction execute${i}(data: Uint8Array): Promise<number> {\n  let sum = 0;\n  for (let idx = 0; idx < data.length; idx++) {\n    sum = (sum + data[idx]) ^ 0x5f;\n  }\n  return Promise.resolve(sum);\n}\n\`\`\`\n\n`;
      }

      if (formulaCount < 60 && i % 5 === 0) {
        formulaCount++;
        const formula = formulas[formulaCount % formulas.length];
        content += `The underlying continuous transform satisfies:\n\n$$${formula}$$\n\nNotice the strict bounds.\n\n`;
      }

      content += `1. **Memory Isolation:** Guaranteed $O(1)$ memory growth.\n2. **Zero-Latency:** Worker-offloaded computation.\n`;
    }

    messages.push({
      id: msgId,
      conversation_id: 'conv_perf_bench',
      parent_id: parentId,
      role: isAssistant ? 'assistant' : 'user',
      content,
      thoughts,
      model: 'gemini-2.5-pro',
      created_at: Date.now() - (totalMessages - i) * 60000,
    });

    parentId = msgId;
  }

  return messages;
}
