import React from 'react';

export interface CitationItem {
  index?: number;
  url: string;
  title?: string;
  domain?: string;
  snippet?: string;
}

export interface CitationPillsProps {
  citations: CitationItem[];
}

export const CitationPills: React.FC<CitationPillsProps> = ({ citations }) => {
  if (!citations || citations.length === 0) {
    return null;
  }

  return (
    <div data-testid="citation-pills-tray" className="mt-2.5 flex flex-wrap gap-1.5 items-center">
      <span className="text-[11px] font-medium text-zinc-400 select-none mr-1 flex items-center gap-1">
        <span>🔗</span> Sources:
      </span>
      {citations.map((c, idx) => {
        const itemIndex = c.index ?? idx + 1;
        let domain = c.domain;
        if (!domain && c.url) {
          try {
            domain = new URL(c.url).hostname;
          } catch {
            domain = 'source';
          }
        }
        const titleText = c.title || domain || 'Source';
        const tooltip = c.snippet ? `${titleText}\n\n"${c.snippet}"` : titleText;

        return (
          <a
            key={idx}
            href={c.url}
            target="_blank"
            rel="noopener noreferrer"
            title={tooltip}
            data-testid={`citation-pill-${itemIndex}`}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] bg-zinc-800/80 hover:bg-zinc-700/80 text-zinc-300 hover:text-white border border-zinc-700/50 transition-all shadow-sm hover:border-blue-500/50 hover:shadow-blue-500/10"
          >
            <span className="font-mono text-[10px] text-zinc-400 font-semibold">[{itemIndex}]</span>
            {domain && (
              <img
                src={`https://www.google.com/s2/favicons?domain=${domain}&sz=32`}
                alt=""
                className="w-3.5 h-3.5 rounded-sm shrink-0"
                onError={(e) => {
                  (e.target as HTMLElement).style.display = 'none';
                }}
              />
            )}
            <span className="truncate max-w-[150px] font-medium">{domain || titleText}</span>
          </a>
        );
      })}
    </div>
  );
};
