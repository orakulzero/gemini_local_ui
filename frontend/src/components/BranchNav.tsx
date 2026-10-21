import React from 'react';

export interface BranchNavProps {
  messageId: number;
  siblingIndex: number;
  siblingCount: number;
  siblingIds?: number[];
  onSwitchBranch: (targetMessageId: number) => void;
  disabled?: boolean;
}

export const BranchNav: React.FC<BranchNavProps> = ({
  siblingIndex,
  siblingCount,
  siblingIds = [],
  onSwitchBranch,
  disabled = false,
}) => {
  if (siblingCount <= 1) {
    return null;
  }

  const hasPrev = siblingIndex > 1;
  const hasNext = siblingIndex < siblingCount;

  const handlePrev = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!hasPrev || disabled) return;
    const targetId = siblingIds[siblingIndex - 2];
    if (targetId !== undefined) {
      onSwitchBranch(targetId);
    }
  };

  const handleNext = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!hasNext || disabled) return;
    const targetId = siblingIds[siblingIndex];
    if (targetId !== undefined) {
      onSwitchBranch(targetId);
    }
  };

  return (
    <div
      data-testid="branch-nav"
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-zinc-800/80 border border-zinc-700/60 text-xs font-medium text-zinc-300 select-none transition-colors"
    >
      <button
        type="button"
        data-testid="branch-nav-prev"
        onClick={handlePrev}
        disabled={!hasPrev || disabled}
        aria-label="Previous branch"
        className={`p-0.5 rounded hover:bg-zinc-700 active:bg-zinc-600 transition-colors ${
          !hasPrev || disabled ? 'opacity-30 cursor-not-allowed' : 'cursor-pointer'
        }`}
      >
        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M15 19l-7-7 7-7" />
        </svg>
      </button>

      <span data-testid="branch-counter" className="font-mono text-[11px] text-zinc-400">
        {siblingIndex} / {siblingCount}
      </span>

      <button
        type="button"
        data-testid="branch-nav-next"
        onClick={handleNext}
        disabled={!hasNext || disabled}
        aria-label="Next branch"
        className={`p-0.5 rounded hover:bg-zinc-700 active:bg-zinc-600 transition-colors ${
          !hasNext || disabled ? 'opacity-30 cursor-not-allowed' : 'cursor-pointer'
        }`}
      >
        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M9 5l7 7-7 7" />
        </svg>
      </button>
    </div>
  );
};
