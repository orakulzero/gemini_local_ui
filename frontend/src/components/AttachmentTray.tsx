import React from 'react';
import { ChatAttachment } from '../types';

export interface AttachmentTrayProps {
  attachments: ChatAttachment[];
  onRemove: (index: number) => void;
}

export const AttachmentTray: React.FC<AttachmentTrayProps> = ({ attachments, onRemove }) => {
  if (attachments.length === 0) return null;

  return (
    <div
      data-testid="attachment-tray"
      className="flex items-center gap-2 p-2.5 overflow-x-auto border-b border-zinc-800"
    >
      {attachments.map((att, idx) => {
        const isImage = att.mime_type.startsWith('image/') && att.previewUrl;
        const sizeFormatted =
          att.size_bytes > 1024 * 1024
            ? `${(att.size_bytes / (1024 * 1024)).toFixed(1)} MB`
            : `${Math.round(att.size_bytes / 1024)} KB`;

        return (
          <div
            key={`${att.filename}-${idx}`}
            data-testid="attachment-card"
            className="relative group flex items-center gap-2 px-2.5 py-1.5 rounded-xl bg-zinc-800/90 border border-zinc-700/60 max-w-[200px] shrink-0"
          >
            {isImage ? (
              <img
                src={att.previewUrl}
                alt={att.filename}
                className="w-8 h-8 rounded-lg object-cover bg-zinc-900 shrink-0"
              />
            ) : (
              <div className="w-8 h-8 rounded-lg bg-zinc-700/60 flex items-center justify-center shrink-0 text-zinc-300">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
              </div>
            )}

            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium text-zinc-200 truncate">{att.filename}</p>
              <p className="text-[10px] text-zinc-400">{sizeFormatted}</p>
            </div>

            <button
              type="button"
              data-testid="attachment-remove-btn"
              onClick={() => onRemove(idx)}
              aria-label={`Remove ${att.filename}`}
              className="p-1 text-zinc-400 hover:text-red-400 rounded-full hover:bg-zinc-700 transition-colors"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        );
      })}
    </div>
  );
};
