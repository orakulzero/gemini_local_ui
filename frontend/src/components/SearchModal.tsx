import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useSessionStore } from '../store/sessionStore';
import { useChatStore } from '../store/chatStore';
import { searchMessages, fetchRagStatus, triggerRagIndex, searchRagHybrid } from '../services/api';
import { queryClient, queryKeys } from '../api/queryClient';
import { SearchResultItem, RagStatusResponse, RagSearchResponse } from '../types';

type SearchMode = 'fts5' | 'hybrid';

export const SearchModal: React.FC = () => {
  const isSearchOpen = useSessionStore((s) => s.isSearchOpen);
  const toggleSearch = useSessionStore((s) => s.toggleSearch);
  const selectConversation = useChatStore((s) => s.selectConversation);

  const [mode, setMode] = useState<SearchMode>('fts5');
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResultItem[]>([]);
  const [ragResponse, setRagResponse] = useState<RagSearchResponse | null>(null);
  const [ragStatus, setRagStatus] = useState<RagStatusResponse | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [isReindexing, setIsReindexing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const prevModeRef = useRef<SearchMode>('fts5');

  const refreshStatus = useCallback(async () => {
    try {
      setRagStatus(await fetchRagStatus());
    } catch (err) {
      console.warn('Failed to fetch RAG status', err);
    }
  }, []);

  useEffect(() => {
    if (isSearchOpen) {
      setTimeout(() => inputRef.current?.focus(), 50);
      void refreshStatus();
    } else {
      setQuery('');
      setResults([]);
      setRagResponse(null);
    }
  }, [isSearchOpen, refreshStatus]);

  const runSearch = useCallback(async (q: string, activeMode: SearchMode) => {
    const trimmed = q.trim();
    if (!trimmed) {
      setResults([]);
      setRagResponse(null);
      return;
    }
    setIsSearching(true);
    try {
      if (activeMode === 'fts5') {
        setRagResponse(null);
        setResults(await searchMessages(trimmed));
      } else {
        setResults([]);
        setRagResponse(await searchRagHybrid({ query: trimmed, mode: 'hybrid', top_k: 10 }));
      }
    } catch (err) {
      console.warn('Search query failed', err);
    } finally {
      setIsSearching(false);
    }
  }, []);

  useEffect(() => {
    if (!isSearchOpen) return;
    const modeChanged = prevModeRef.current !== mode;
    prevModeRef.current = mode;
    if (!query.trim()) {
      setResults([]);
      setRagResponse(null);
      return;
    }
    const timer = setTimeout(() => { void runSearch(query, mode); }, modeChanged ? 0 : 150);
    return () => clearTimeout(timer);
  }, [query, mode, isSearchOpen, runSearch]);

  const handleReindex = async () => {
    if (isReindexing) return;
    setIsReindexing(true);
    try {
      await triggerRagIndex({ source_types: ['conversation', 'brain_digest'] });
      await refreshStatus();
      queryClient.invalidateQueries({ queryKey: queryKeys.ragStatus }).catch(() => {});
    } catch (err) {
      console.warn('RAG reindex failed', err);
    } finally {
      setIsReindexing(false);
    }
  };

  if (!isSearchOpen) return null;
  const ragHits = ragResponse?.results ?? [];
  const isDegraded = Boolean(ragStatus?.degraded || ragStatus?.sqlite_vec_loaded === false || ragResponse?.degraded);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-20 px-4 bg-black/60 backdrop-blur-sm">
      <div className="fixed inset-0" onClick={() => toggleSearch(false)} />
      <div data-testid="search-modal" className="relative w-full max-w-xl rounded-2xl bg-zinc-900 border border-zinc-800 shadow-2xl overflow-hidden z-10">
        <form onSubmit={(e) => { e.preventDefault(); void runSearch(query, mode); }} className="flex items-center gap-2 px-4 py-3 border-b border-zinc-800">
          <svg className="w-4 h-4 text-zinc-400 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input ref={inputRef} data-testid="search-input" type="text" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={mode === 'hybrid' ? 'Hybrid RRF search across chats & brain...' : 'Search all conversations via instant FTS5...'} className="flex-1 bg-transparent text-zinc-100 placeholder-zinc-500 text-sm focus:outline-none" />
          <button type="button" onClick={() => toggleSearch(false)} className="text-xs text-zinc-500 hover:text-zinc-300">ESC</button>
        </form>
        <div className="flex items-center justify-between gap-2 px-4 py-2 border-b border-zinc-800/80 bg-zinc-950/50 text-xs">
          <div className="flex items-center gap-1">
            <button type="button" data-testid="search-mode-fts5" onClick={() => setMode('fts5')} className={`px-2.5 py-1 rounded-lg transition-colors ${mode === 'fts5' ? 'bg-zinc-800 text-zinc-100 font-medium' : 'text-zinc-400 hover:text-zinc-200'}`}>Messages FTS5</button>
            <button type="button" data-testid="search-mode-hybrid" onClick={() => setMode('hybrid')} className={`px-2.5 py-1 rounded-lg transition-colors ${mode === 'hybrid' ? 'bg-emerald-600/25 text-emerald-200 border border-emerald-500/40 font-medium' : 'text-zinc-400 hover:text-zinc-200'}`}>Hybrid RRF</button>
          </div>
          <div className="flex items-center gap-2">
            <span data-testid="rag-status-badge" className={`px-2 py-0.5 rounded-full text-[11px] border ${isDegraded ? 'bg-amber-950/50 border-amber-700/50 text-amber-300' : 'bg-zinc-800/80 border-zinc-700 text-zinc-300'}`}>
              {ragStatus ? `${ragStatus.total_chunks} chunks · ${isDegraded ? 'degraded' : 'sqlite-vec'}` : '... chunks'}
            </span>
            <button type="button" data-testid="rag-reindex-btn" disabled={isReindexing} onClick={handleReindex} className="px-2 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 disabled:opacity-50 transition-colors">
              {isReindexing ? 'Reindexing...' : 'Reindex'}
            </button>
          </div>
        </div>
        <div className="max-h-96 overflow-y-auto p-2 space-y-1">
          {isSearching && <div className="p-4 text-center text-xs text-zinc-500">Searching...</div>}
          {!isSearching && query.trim() && ((mode === 'fts5' && results.length === 0) || (mode === 'hybrid' && ragHits.length === 0)) && (
            <div className="p-4 text-center text-xs text-zinc-500">No results found</div>
          )}
          {mode === 'fts5' && results.map((item) => (
            <button key={`${item.conversation_id}-${item.message_id}`} type="button" data-testid="search-result-item" onClick={async () => { await selectConversation(item.conversation_id, item.message_id); toggleSearch(false); }} className="w-full text-left p-2.5 rounded-xl hover:bg-zinc-800/80 transition-colors block group">
              <div className="flex items-center justify-between text-xs text-zinc-400 mb-1">
                <span className="font-medium text-zinc-300 group-hover:text-blue-400 truncate max-w-[300px]">{item.conversation_title || 'Untitled'}</span>
                <span className="text-[10px] text-zinc-500 capitalize">{item.role}</span>
              </div>
              <p className="text-xs text-zinc-400 line-clamp-2" dangerouslySetInnerHTML={{ __html: item.content_snippet }} />
            </button>
          ))}
          {mode === 'hybrid' && ragHits.map((hit) => (
            <div key={`${hit.source_type}-${hit.source_id}-${hit.chunk_id}`} data-testid="rag-result-item" className="p-2.5 rounded-xl bg-zinc-900/60 hover:bg-zinc-800/80 border border-zinc-800/70 transition-colors">
              <div className="flex items-center justify-between gap-2 text-xs mb-1">
                <div className="flex items-center gap-1.5 min-w-0">
                  <span className="px-1.5 py-0.5 rounded bg-zinc-800 text-[10px] text-emerald-300 shrink-0">{hit.source_type}</span>
                  <button type="button" onClick={async () => { if (hit.source_type === 'conversation' && hit.source_id) { await selectConversation(hit.source_id); toggleSearch(false); } }} className={`font-medium truncate text-left ${hit.source_type === 'conversation' ? 'text-zinc-200 hover:text-blue-400 cursor-pointer' : 'text-zinc-200 cursor-default'}`}>
                    {hit.source_title || hit.source_id}
                  </button>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-[10px] text-zinc-400 font-mono">{Number(hit.rrf_score ?? hit.score ?? 0).toFixed(3)}</span>
                  <button type="button" data-testid="rag-attach-btn" onClick={() => { useChatStore.getState().injectContext(ragResponse?.formatted_xml_context || hit.content); toggleSearch(false); }} className="px-2 py-0.5 rounded-md bg-blue-600/20 hover:bg-blue-600/40 text-blue-300 border border-blue-500/30 text-[11px]">
                    Attach
                  </button>
                </div>
              </div>
              <p className="text-xs text-zinc-400 line-clamp-2 whitespace-pre-wrap">{hit.content}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
