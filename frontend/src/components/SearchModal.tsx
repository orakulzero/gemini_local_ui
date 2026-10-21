import React, { useState, useEffect, useRef } from 'react';
import { useSessionStore } from '../store/sessionStore';
import { useChatStore } from '../store/chatStore';
import { searchMessages } from '../services/api';
import { SearchResultItem } from '../types';

export const SearchModal: React.FC = () => {
  const isSearchOpen = useSessionStore((s) => s.isSearchOpen);
  const toggleSearch = useSessionStore((s) => s.toggleSearch);
  const selectConversation = useChatStore((s) => s.selectConversation);

  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResultItem[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isSearchOpen) {
      setTimeout(() => inputRef.current?.focus(), 50);
    } else {
      setQuery('');
      setResults([]);
    }
  }, [isSearchOpen]);

  useEffect(() => {
    if (!query.trim()) {
      setResults([]);
      return;
    }

    const timer = setTimeout(async () => {
      setIsSearching(true);
      try {
        const res = await searchMessages(query.trim());
        setResults(res);
      } catch (err) {
        console.warn('Search query failed', err);
      } finally {
        setIsSearching(false);
      }
    }, 150);

    return () => clearTimeout(timer);
  }, [query]);

  if (!isSearchOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-20 px-4 bg-black/60 backdrop-blur-sm">
      <div
        className="fixed inset-0"
        onClick={() => toggleSearch(false)}
      />
      <div
        data-testid="search-modal"
        className="relative w-full max-w-xl rounded-2xl bg-zinc-900 border border-zinc-800 shadow-2xl overflow-hidden z-10"
      >
        <div className="flex items-center gap-2 px-4 py-3 border-b border-zinc-800">
          <svg className="w-4 h-4 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            ref={inputRef}
            data-testid="search-input"
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search all conversations via instant FTS5..."
            className="flex-1 bg-transparent text-zinc-100 placeholder-zinc-500 text-sm focus:outline-none"
          />
          <button
            type="button"
            onClick={() => toggleSearch(false)}
            className="text-xs text-zinc-500 hover:text-zinc-300"
          >
            ESC
          </button>
        </div>

        <div className="max-h-96 overflow-y-auto p-2">
          {isSearching && (
            <div className="p-4 text-center text-xs text-zinc-500">Searching...</div>
          )}
          {!isSearching && results.length === 0 && query.trim() && (
            <div className="p-4 text-center text-xs text-zinc-500">No results found</div>
          )}
          {results.map((item) => (
            <button
              key={`${item.conversation_id}-${item.message_id}`}
              type="button"
              data-testid="search-result-item"
              onClick={async () => {
                await selectConversation(item.conversation_id, item.message_id);
                toggleSearch(false);
              }}
              className="w-full text-left p-2.5 rounded-xl hover:bg-zinc-800/80 transition-colors block group"
            >
              <div className="flex items-center justify-between text-xs text-zinc-400 mb-1">
                <span className="font-medium text-zinc-300 group-hover:text-blue-400 truncate max-w-[300px]">
                  {item.conversation_title || 'Untitled'}
                </span>
                <span className="text-[10px] text-zinc-500 capitalize">{item.role}</span>
              </div>
              <p
                className="text-xs text-zinc-400 line-clamp-2"
                dangerouslySetInnerHTML={{ __html: item.content_snippet }}
              />
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};
