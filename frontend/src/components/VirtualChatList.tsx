import React, {
  useRef,
  useState,
  useEffect,
  useLayoutEffect,
  useCallback,
  useMemo,
} from 'react';
import { Message } from '../types';
import { MessageItem } from './MessageItem';

export interface VirtualChatListProps {
  messages: Message[];
  isStreaming?: boolean;
  estimatedItemHeight?: number;
  overscan?: number;
  onEditMessage?: (id: number, newContent: string) => void;
  onCopyMessage?: (id: number) => void;
  onSelectBranch?: (id: number, branchIndex: number) => void;
}

export const VirtualChatList: React.FC<VirtualChatListProps> = ({
  messages,
  isStreaming = false,
  estimatedItemHeight = 120,
  overscan = 1,
  onEditMessage,
  onCopyMessage,
  onSelectBranch,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(640);
  const [isPinned, setIsPinned] = useState(true);

  // Height measurement state revision: incrementing triggers prefixSums & totalHeight recomputation
  const [heightRevision, setHeightRevision] = useState(0);

  // Height cache: maps message.id -> measured height in px
  const heightCache = useRef<Map<number, number>>(new Map());
  const mountedNodes = useRef<Map<number, HTMLDivElement>>(new Map());
  const pendingMeasurements = useRef<Map<number, number>>(new Map());
  const rafId = useRef<number | null>(null);
  const observerRef = useRef<ResizeObserver | null>(null);

  const isPinnedRef = useRef(isPinned);
  isPinnedRef.current = isPinned;

  const messagesRef = useRef(messages);
  messagesRef.current = messages;

  // Measure container viewport height on mount & window resize
  useLayoutEffect(() => {
    const updateSize = () => {
      if (containerRef.current) {
        setViewportHeight(containerRef.current.clientHeight || 640);
      }
    };
    updateSize();
    window.addEventListener('resize', updateSize);
    return () => window.removeEventListener('resize', updateSize);
  }, []);

  // Structure-aware initial estimate for unmeasured items
  const getInitialEstimate = useCallback(
    (message: Message): number => {
      if (!message) return estimatedItemHeight;
      if (message.codeBlocks && message.codeBlocks.length > 0) {
        let lines = 0;
        for (const cb of message.codeBlocks) {
          lines += (cb.code || '').split('\n').length;
        }
        return Math.max(estimatedItemHeight, 120 + lines * 21);
      }
      if (message.thoughts || (message as any).thought) {
        return Math.max(estimatedItemHeight, 160);
      }
      const len = message.content ? message.content.length : 0;
      if (len < 80 && !message.content.includes('\n') && !message.content.includes('$$')) {
        return 76;
      }
      return estimatedItemHeight;
    },
    [estimatedItemHeight]
  );

  // Compute prefix sums: prefixSums[i] = top offset of message i
  const { prefixSums, totalHeight } = useMemo(() => {
    const count = messages.length;
    const sums = new Float64Array(count + 1);
    sums[0] = 0;

    for (let i = 0; i < count; i++) {
      const msg = messages[i];
      const cached = heightCache.current.get(msg.id);
      const h = cached ?? getInitialEstimate(msg);
      sums[i + 1] = sums[i] + h;
    }

    return { prefixSums: sums, totalHeight: sums[count] };
  }, [messages, estimatedItemHeight, heightRevision, getInitialEstimate]);

  const prefixSumsRef = useRef(prefixSums);
  prefixSumsRef.current = prefixSums;

  // Binary search for visible start index: O(log N)
  const startIndex = useMemo(() => {
    const count = messages.length;
    if (count === 0) return 0;

    let low = 0;
    let high = count - 1;
    let result = 0;

    while (low <= high) {
      const mid = (low + high) >> 1;
      if (prefixSums[mid] <= scrollTop) {
        result = mid;
        low = mid + 1;
      } else {
        high = mid - 1;
      }
    }
    return Math.max(0, result - overscan);
  }, [scrollTop, prefixSums, messages.length, overscan]);

  // Determine end index with overscan covering target bottom (capped to max 11 mounted items for strict DOM bound)
  const endIndex = useMemo(() => {
    const count = messages.length;
    if (count === 0) return 0;

    let index = startIndex;
    const targetBottom = scrollTop + viewportHeight;

    while (index < count && prefixSums[index] < targetBottom) {
      index++;
    }
    const desiredEnd = Math.min(count - 1, index + overscan);
    return Math.min(desiredEnd, startIndex + 10);
  }, [startIndex, scrollTop, viewportHeight, prefixSums, messages.length, overscan]);

  // Scroll handler: update scrollTop and pin state
  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;

    const currentScrollTop = el.scrollTop;
    setScrollTop(currentScrollTop);

    const distFromBottom = el.scrollHeight - currentScrollTop - el.clientHeight;
    setIsPinned(distFromBottom <= 30);
  }, []);

  // Stable singleton ResizeObserver: instantiated once, never recreated on scroll/pinning
  if (!observerRef.current && typeof ResizeObserver !== 'undefined') {
    observerRef.current = new ResizeObserver((entries) => {
      let hasChanges = false;

      for (const entry of entries) {
        const idStr = entry.target.getAttribute('data-id');
        const idxStr = entry.target.getAttribute('data-index');
        if (!idStr) continue;
        const id = Number(idStr);
        const idx = idxStr ? Number(idxStr) : -1;

        const rawHeight = entry.borderBoxSize?.[0]?.blockSize ?? entry.contentRect.height;
        if (!rawHeight || rawHeight <= 0) continue;
        const newHeight = Math.ceil(rawHeight);

        const sums = prefixSumsRef.current;
        const allocatedHeight =
          idx >= 0 && idx + 1 < sums.length
            ? sums[idx + 1] - sums[idx]
            : heightCache.current.get(id);

        if (allocatedHeight === undefined || Math.abs(newHeight - allocatedHeight) > 1) {
          pendingMeasurements.current.set(id, newHeight);
          hasChanges = true;
        } else {
          heightCache.current.set(id, newHeight);
        }
      }

      if (hasChanges && rafId.current === null) {
        rafId.current = requestAnimationFrame(() => {
          rafId.current = null;
          let anyUpdated = false;
          pendingMeasurements.current.forEach((h, id) => {
            const prev = heightCache.current.get(id);
            if (prev === undefined || Math.abs(prev - h) > 1) {
              heightCache.current.set(id, h);
              anyUpdated = true;
            }
          });
          pendingMeasurements.current.clear();

          if (anyUpdated) {
            setHeightRevision((v) => v + 1);
          }
        });
      }
    });
  }

  // Cleanup ResizeObserver strictly on unmount
  useEffect(() => {
    return () => {
      if (rafId.current !== null) {
        cancelAnimationFrame(rafId.current);
        rafId.current = null;
      }
      observerRef.current?.disconnect();
      observerRef.current = null;
    };
  }, []);

  // Compute slice of rendered items
  const visibleItems = useMemo(() => {
    const items = [];
    for (let i = startIndex; i <= endIndex; i++) {
      if (messages[i]) {
        items.push({
          message: messages[i],
          index: i,
          offsetY: prefixSums[i],
        });
      }
    }
    return items;
  }, [startIndex, endIndex, messages, prefixSums]);

  // Synchronous layout measurement pass after visibleItems mount/update to prevent initial overlap collisions
  useLayoutEffect(() => {
    let changed = false;
    mountedNodes.current.forEach((node, id) => {
      const idxAttr = node.getAttribute('data-index');
      const idx = idxAttr ? Number(idxAttr) : -1;
      const rectHeight = Math.ceil(node.getBoundingClientRect().height);
      if (rectHeight > 0) {
        const allocated =
          idx >= 0 && idx + 1 < prefixSums.length
            ? prefixSums[idx + 1] - prefixSums[idx]
            : heightCache.current.get(id);
        if (allocated === undefined || Math.abs(rectHeight - allocated) > 1) {
          heightCache.current.set(id, rectHeight);
          changed = true;
        }
      }
    });
    if (changed) {
      setHeightRevision((v) => v + 1);
    }
  }, [visibleItems, prefixSums]);

  // Auto-scroll to bottom if pinned
  useLayoutEffect(() => {
    if (isPinned && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [totalHeight, isPinned]);

  const scrollToBottom = useCallback(() => {
    if (containerRef.current) {
      containerRef.current.scrollTo({
        top: containerRef.current.scrollHeight,
        behavior: 'smooth',
      });
      setIsPinned(true);
    }
  }, []);

  return (
    <div
      ref={containerRef}
      data-testid="virtual-chat-container"
      onScroll={handleScroll}
      className="relative w-full h-full overflow-y-auto overflow-x-hidden"
      style={{
        contain: 'strict',
      }}
    >
      <div
        data-testid="virtual-chat-scroll-area"
        style={{ width: '100%', height: '100%', position: 'relative' }}
      >
        <div
          data-testid="virtual-chat-phantom"
          style={{
            height: `${Math.max(totalHeight, viewportHeight)}px`,
            width: '100%',
            position: 'relative',
          }}
        >
          {visibleItems.map(({ message, index, offsetY }) => (
            <div
              key={message.id}
              data-id={message.id}
              data-index={index}
              ref={(node) => {
                const prev = mountedNodes.current.get(message.id);
                if (prev && prev !== node) {
                  observerRef.current?.unobserve(prev);
                  mountedNodes.current.delete(message.id);
                }
                if (node) {
                  mountedNodes.current.set(message.id, node);
                  observerRef.current?.observe(node);
                }
              }}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                transform: `translateY(${offsetY}px)`,
                willChange: 'transform',
                contain: 'layout paint',
              }}
            >
              <MessageItem
                message={message}
                isStreaming={isStreaming && index === messages.length - 1}
                onEdit={onEditMessage}
                onCopy={onCopyMessage}
                onSelectBranch={onSelectBranch}
              />
            </div>
          ))}
        </div>
      </div>

      {!isPinned && (
        <button
          data-testid="scroll-to-bottom"
          onClick={scrollToBottom}
          className="fixed bottom-24 right-6 z-40 bg-zinc-800 hover:bg-zinc-700 text-zinc-100 border border-zinc-700 rounded-full px-3.5 py-1.5 text-xs shadow-lg transition-transform hover:scale-105"
        >
          ↓ Scroll to bottom
        </button>
      )}
    </div>
  );
};
