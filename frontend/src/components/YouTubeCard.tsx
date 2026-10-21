import React, { useState } from 'react';

export interface YouTubeVideoItem {
  video_id: string;
  title: string;
  url: string;
  thumbnail_url: string;
  channel?: string | null;
  duration?: string | null;
}

export interface YouTubeCardProps {
  videos: YouTubeVideoItem[];
}

export const YouTubeCard: React.FC<YouTubeCardProps> = ({ videos }) => {
  const [playingVideoId, setPlayingVideoId] = useState<string | null>(null);

  if (!videos || videos.length === 0) {
    return null;
  }

  return (
    <div data-testid="youtube-cards-container" className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-2xl">
      {videos.map((vid) => {
        const isPlaying = playingVideoId === vid.video_id;

        return (
          <div
            key={vid.video_id}
            data-testid={`youtube-card-${vid.video_id}`}
            className="flex flex-col rounded-xl overflow-hidden border border-zinc-800 bg-zinc-900/90 shadow-sm transition-all hover:border-zinc-700"
          >
            {/* 16:9 Media container */}
            <div className="relative aspect-video w-full bg-black flex items-center justify-center overflow-hidden">
              {isPlaying ? (
                <iframe
                  data-testid={`youtube-iframe-${vid.video_id}`}
                  src={`https://www.youtube-nocookie.com/embed/${vid.video_id}?autoplay=1`}
                  title={vid.title}
                  allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                  allowFullScreen
                  className="w-full h-full border-0"
                />
              ) : (
                <div
                  className="relative w-full h-full cursor-pointer group"
                  onClick={() => setPlayingVideoId(vid.video_id)}
                >
                  <img
                    src={vid.thumbnail_url || `https://i.ytimg.com/vi/${vid.video_id}/hqdefault.jpg`}
                    alt={vid.title}
                    className="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105"
                    loading="lazy"
                  />
                  {/* Play Button Overlay */}
                  <div className="absolute inset-0 flex items-center justify-center bg-black/30 group-hover:bg-black/10 transition-colors">
                    <div className="w-12 h-12 rounded-full bg-red-600/90 group-hover:bg-red-600 text-white flex items-center justify-center shadow-lg transition-transform group-hover:scale-110">
                      <span className="text-lg ml-0.5">▶</span>
                    </div>
                  </div>
                  {vid.duration && (
                    <div className="absolute bottom-2 right-2 px-1.5 py-0.5 rounded bg-black/80 text-[10px] font-mono text-white font-medium">
                      {vid.duration}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Title & Channel Info */}
            <div className="p-2.5 flex items-start justify-between gap-2">
              <div className="min-w-0">
                <a
                  href={vid.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs font-semibold text-zinc-200 hover:text-blue-400 line-clamp-2 transition-colors"
                  title={vid.title}
                >
                  {vid.title}
                </a>
                {vid.channel && (
                  <p className="text-[11px] text-zinc-500 mt-0.5 truncate">{vid.channel}</p>
                )}
              </div>
              <a
                href={vid.url}
                target="_blank"
                rel="noopener noreferrer"
                title="Open on YouTube"
                className="text-zinc-500 hover:text-red-400 p-1 text-xs shrink-0 transition-colors"
              >
                ↗
              </a>
            </div>
          </div>
        );
      })}
    </div>
  );
};
