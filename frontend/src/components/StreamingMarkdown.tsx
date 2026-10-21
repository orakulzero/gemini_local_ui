import React, { useMemo, useEffect, useState, useRef } from 'react';
import { CodeBlock } from './CodeBlock';
import { MathBlock } from './MathBlock';

export interface StreamingMarkdownProps {
  content: string;
  isStreaming?: boolean;
}

export interface MarkdownBlockItem {
  id: string;
  type: 'paragraph' | 'heading' | 'code' | 'display_math';
  content: string;
  language?: string;
  level?: number;
}

const BLOCK_SPLIT_REGEX = /\n\n+/;
const INLINE_MATH_REGEX = /(?<!\$)\$(?!\s)([^$\n]+?)(?<![\s\-/])\$(?!\$|\d)/g;

/**
 * Splits raw markdown buffer into immutable completed blocks and mutable active tail block.
 */
export function segmentStreamingMarkdown(rawBuffer: string): {
  immutableBlocks: MarkdownBlockItem[];
  tailBlock: string;
  isInCodeBlock: boolean;
  codeLanguage: string;
} {
  if (!rawBuffer) {
    return { immutableBlocks: [], tailBlock: '', isInCodeBlock: false, codeLanguage: '' };
  }

  // Count backtick fence occurrences
  const fenceMatches = rawBuffer.match(/```/g);
  const fenceCount = fenceMatches ? fenceMatches.length : 0;
  const isInCodeBlock = fenceCount % 2 !== 0;

  let immutableText = '';
  let tailText = '';
  let codeLanguage = '';

  if (isInCodeBlock) {
    const lastFenceIdx = rawBuffer.lastIndexOf('```');
    immutableText = rawBuffer.slice(0, lastFenceIdx).trim();
    tailText = rawBuffer.slice(lastFenceIdx);

    const langMatch = tailText.match(/^```([a-zA-Z0-9_-]*)/);
    codeLanguage = langMatch ? langMatch[1] : '';
  } else {
    const stripped = rawBuffer.trimEnd();
    if (stripped.endsWith('```') || !rawBuffer.includes('\n\n')) {
      if (stripped.endsWith('```')) {
        immutableText = rawBuffer.trim();
        tailText = '';
      } else {
        immutableText = '';
        tailText = rawBuffer;
      }
    } else {
      const lastFence = rawBuffer.lastIndexOf('```');
      const lastBreak = rawBuffer.lastIndexOf('\n\n');

      if (lastFence !== -1 && lastFence > lastBreak) {
        immutableText = rawBuffer.slice(0, lastFence + 3).trim();
        tailText = rawBuffer.slice(lastFence + 3).trim();
      } else if (lastBreak !== -1) {
        immutableText = rawBuffer.slice(0, lastBreak).trim();
        tailText = rawBuffer.slice(lastBreak + 2);
      } else {
        immutableText = '';
        tailText = rawBuffer;
      }
    }
  }

  const immutableBlocks: MarkdownBlockItem[] = [];

  if (immutableText) {
    const codePattern = /```([a-zA-Z0-9_-]*)\r?\n([\s\S]*?)```/g;
    let lastIdx = 0;
    let match: RegExpExecArray | null;

    while ((match = codePattern.exec(immutableText)) !== null) {
      const beforeText = immutableText.slice(lastIdx, match.index).trim();
      if (beforeText) {
        parseTextBlocks(beforeText, immutableBlocks);
      }

      const lang = match[1].trim() || 'text';
      const codeContent = match[2].replace(/\r?\n$/, '');
      immutableBlocks.push({
        id: `code_${match.index}`,
        type: 'code',
        language: lang,
        content: codeContent,
      });

      lastIdx = codePattern.lastIndex;
    }

    const remainingText = immutableText.slice(lastIdx).trim();
    if (remainingText) {
      parseTextBlocks(remainingText, immutableBlocks);
    }
  }

  return { immutableBlocks, tailBlock: tailText, isInCodeBlock, codeLanguage };
}

function parseTextBlocks(text: string, output: MarkdownBlockItem[]) {
  const mathSegments = text.split(/\$\$([\s\S]+?)\$\$/g);
  if (mathSegments.length > 1) {
    for (let i = 0; i < mathSegments.length; i++) {
      const seg = mathSegments[i].trim();
      if (!seg) continue;
      if (i % 2 === 1) {
        output.push({
          id: `math_${output.length}`,
          type: 'display_math',
          content: seg,
        });
      } else {
        parseParagraphsAndHeadings(seg, output);
      }
    }
    return;
  }

  parseParagraphsAndHeadings(text, output);
}

function parseParagraphsAndHeadings(text: string, output: MarkdownBlockItem[]) {
  const parts = text.split(BLOCK_SPLIT_REGEX);
  for (const part of parts) {
    const p = part.trim();
    if (!p) continue;

    if (p.startsWith('#')) {
      const levelMatch = p.match(/^(#{1,6})\s+(.*)$/);
      if (levelMatch) {
        output.push({
          id: `h_${output.length}`,
          type: 'heading',
          level: levelMatch[1].length,
          content: levelMatch[2],
        });
        continue;
      }
    }

    output.push({
      id: `p_${output.length}`,
      type: 'paragraph',
      content: p,
    });
  }
}

function renderFormattedSpan(text: string, keyPrefix: string): React.ReactNode {
  const tokenRegex = /(\*\*[^*]+\*\*|`[^`\n]+`|\[[^\]]+\]\([^)]+\))/g;
  const parts: React.ReactNode[] = [];
  let lastIdx = 0;
  let match: RegExpExecArray | null;

  while ((match = tokenRegex.exec(text)) !== null) {
    if (match.index > lastIdx) {
      parts.push(text.slice(lastIdx, match.index));
    }
    const token = match[0];
    if (token.startsWith('**') && token.endsWith('**')) {
      parts.push(
        <strong key={`${keyPrefix}_b_${match.index}`} className="font-semibold text-zinc-100">
          {token.slice(2, -2)}
        </strong>
      );
    } else if (token.startsWith('`') && token.endsWith('`')) {
      parts.push(
        <code
          key={`${keyPrefix}_c_${match.index}`}
          className="px-1.5 py-0.5 rounded bg-zinc-800/90 text-blue-300 font-mono text-xs"
        >
          {token.slice(1, -1)}
        </code>
      );
    } else if (token.startsWith('[')) {
      const linkMatch = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      if (linkMatch) {
        parts.push(
          <a
            key={`${keyPrefix}_a_${match.index}`}
            href={linkMatch[2]}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-400 hover:text-blue-300 underline underline-offset-2"
          >
            {linkMatch[1]}
          </a>
        );
      } else {
        parts.push(token);
      }
    }
    lastIdx = tokenRegex.lastIndex;
  }

  if (lastIdx < text.length) {
    parts.push(text.slice(lastIdx));
  }

  return <>{parts}</>;
}

function renderInlineContent(text: string): React.ReactNode {
  const parts: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  INLINE_MATH_REGEX.lastIndex = 0;
  while ((match = INLINE_MATH_REGEX.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(renderFormattedSpan(text.slice(lastIndex, match.index), `pre_${match.index}`));
    }
    parts.push(
      <MathBlock
        key={`inline_math_${match.index}`}
        formula={match[1]}
        displayMode={false}
      />
    );
    lastIndex = INLINE_MATH_REGEX.lastIndex;
  }

  if (lastIndex < text.length) {
    parts.push(renderFormattedSpan(text.slice(lastIndex), `tail_${lastIndex}`));
  }

  return <>{parts}</>;
}

function renderRichParagraphBlock(content: string, key: string, extraTailNode?: React.ReactNode): React.ReactNode {
  const trimmed = content.trim();
  if (trimmed === '---' || trimmed === '***') {
    return <hr key={key} className="my-3 border-zinc-800/80" />;
  }

  const lines = trimmed.split(/\r?\n/);

  // Markdown Table detection
  if (
    lines.length >= 2 &&
    lines[0].trim().startsWith('|') &&
    lines[1].includes('---')
  ) {
    const parseRow = (line: string) =>
      line
        .trim()
        .replace(/^\|/, '')
        .replace(/\|$/, '')
        .split('|')
        .map((c) => c.trim());

    const headers = parseRow(lines[0]);
    const bodyRows = lines.slice(2).filter((l) => l.trim().startsWith('|')).map(parseRow);

    return (
      <div key={key} className="my-2.5 overflow-x-auto rounded-lg border border-zinc-800 bg-zinc-950/40">
        <table className="w-full text-left border-collapse text-xs">
          <thead>
            <tr className="border-b border-zinc-800 bg-zinc-900/80 text-zinc-200">
              {headers.map((h, i) => (
                <th key={i} className="px-3 py-2 font-semibold">
                  {renderInlineContent(h)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/60">
            {bodyRows.map((row, rIdx) => (
              <tr key={rIdx} className="hover:bg-zinc-900/30">
                {row.map((cell, cIdx) => (
                  <td key={cIdx} className="px-3 py-2 align-top text-zinc-300">
                    {renderInlineContent(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {extraTailNode}
      </div>
    );
  }

  // Multi-line list / structured paragraph rendering
  if (lines.length > 1) {
    return (
      <div key={key} className="my-1.5 space-y-1">
        {lines.map((line, idx) => {
          const l = line.trim();
          if (!l) return null;
          const bulletMatch = l.match(/^([*-]|\d+\.)\s+(.*)$/);
          if (bulletMatch) {
            return (
              <div key={idx} className="flex items-start gap-2 pl-2">
                <span className="text-blue-400 select-none shrink-0">
                  {bulletMatch[1].endsWith('.') ? bulletMatch[1] : '•'}
                </span>
                <span className="flex-1">
                  {renderInlineContent(bulletMatch[2])}
                  {idx === lines.length - 1 ? extraTailNode : null}
                </span>
              </div>
            );
          }
          return (
            <p key={idx}>
              {renderInlineContent(l)}
              {idx === lines.length - 1 ? extraTailNode : null}
            </p>
          );
        })}
      </div>
    );
  }

  return (
    <p key={key} className="my-1.5">
      {renderInlineContent(content)}
      {extraTailNode}
    </p>
  );
}

export const StreamingMarkdown: React.FC<StreamingMarkdownProps> = React.memo(({
  content,
  isStreaming = false,
}) => {
  const [segmented, setSegmented] = useState(() => segmentStreamingMarkdown(content));
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (!isStreaming) {
      setSegmented(segmentStreamingMarkdown(content));
      return;
    }

    if (rafRef.current === null) {
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;
        setSegmented(segmentStreamingMarkdown(content));
      });
    }

    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [content, isStreaming]);

  const { immutableBlocks, tailBlock, isInCodeBlock, codeLanguage } = segmented;

  const tailCode = useMemo(() => {
    if (!isInCodeBlock) return '';
    return tailBlock.replace(/^```[a-zA-Z0-9_-]*\r?\n?/, '');
  }, [isInCodeBlock, tailBlock]);

  return (
    <div className="markdown-content text-zinc-200 text-sm leading-relaxed space-y-2 select-text">
      {/* Immutable Completed Blocks */}
      {immutableBlocks.map((block) => {
        if (block.type === 'code') {
          return (
            <CodeBlock
              key={block.id}
              language={block.language}
              code={block.content}
              isStreaming={false}
            />
          );
        }

        if (block.type === 'display_math') {
          return (
            <MathBlock
              key={block.id}
              formula={block.content}
              displayMode={true}
            />
          );
        }

        if (block.type === 'heading') {
          const Tag = `h${Math.min(block.level || 2, 6)}` as 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6';
          return (
            <Tag key={block.id} className="font-semibold text-zinc-100 mt-3 mb-1.5">
              {renderInlineContent(block.content)}
            </Tag>
          );
        }

        return renderRichParagraphBlock(block.content, block.id);
      })}

      {/* Mutable Active Tail Block */}
      {tailBlock && (
        <div className="streaming-tail">
          {isInCodeBlock ? (
            <CodeBlock
              language={codeLanguage}
              code={tailCode}
              isStreaming={true}
            />
          ) : (
            renderRichParagraphBlock(
              tailBlock,
              'tail_block',
              isStreaming ? (
                <span className="inline-block w-1.5 h-4 ml-1 bg-blue-400 animate-pulse align-middle" />
              ) : undefined
            )
          )}
        </div>
      )}
    </div>
  );
});

StreamingMarkdown.displayName = 'StreamingMarkdown';
