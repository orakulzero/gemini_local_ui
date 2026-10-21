import React, { useEffect, useCallback } from 'react';

export interface LightboxImage {
  url: string;
  alt?: string;
  width?: number;
  height?: number;
  prompt?: string;
}

export interface LightboxModalProps {
  image: LightboxImage | null;
  onClose: () => void;
}

export const LightboxModal: React.FC<LightboxModalProps> = ({ image, onClose }) => {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    if (image) {
      window.addEventListener('keydown', handleKeyDown);
    }
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [image, onClose]);

  const handleDownload = useCallback(() => {
    if (!image) return;
    try {
      const a = document.createElement('a');
      a.href = image.url;
      a.target = '_blank';
      a.download = `gemini_image_${Date.now()}.png`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    } catch (e) {
      console.error('Failed to download image', e);
    }
  }, [image]);

  if (!image) {
    return null;
  }

  return (
    <div
      data-testid="lightbox-modal"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 animate-in fade-in duration-200"
      onClick={onClose}
    >
      <div
        className="relative max-w-4xl max-h-[90vh] flex flex-col bg-zinc-900 border border-zinc-700/80 rounded-xl overflow-hidden shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Top Header */}
        <div className="flex items-center justify-between px-4 py-2.5 bg-zinc-950/80 border-b border-zinc-800 text-xs text-zinc-300">
          <span className="truncate max-w-md font-medium text-zinc-200">
            {image.alt || image.prompt || 'Generated Image'}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              data-testid="lightbox-download-btn"
              onClick={handleDownload}
              className="px-2.5 py-1 rounded bg-blue-600 hover:bg-blue-500 text-white font-medium transition-colors flex items-center gap-1"
              title="Download image"
            >
              <span>💾</span> Download
            </button>
            <button
              type="button"
              data-testid="lightbox-close-btn"
              onClick={onClose}
              className="p-1 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded transition-colors text-sm leading-none"
              title="Close (Esc)"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Image viewport */}
        <div className="flex-1 overflow-auto flex items-center justify-center p-2 bg-black/40">
          <img
            src={image.url}
            alt={image.alt || 'Full Resolution'}
            className="max-h-[75vh] w-auto max-w-full object-contain rounded"
          />
        </div>

        {/* Footer info */}
        {(image.alt || image.prompt) && (
          <div className="px-4 py-2 bg-zinc-950/90 border-t border-zinc-800 text-xs text-zinc-400">
            <p className="line-clamp-2">{image.prompt || image.alt}</p>
          </div>
        )}
      </div>
    </div>
  );
};
