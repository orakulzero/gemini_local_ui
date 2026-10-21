import React, { useState } from 'react';
import { LightboxModal, LightboxImage } from './LightboxModal';

export interface ImageGalleryProps {
  images: Array<{
    url: string;
    alt?: string;
    width?: number;
    height?: number;
    prompt?: string;
  }>;
}

export const ImageGallery: React.FC<ImageGalleryProps> = ({ images }) => {
  const [selectedImage, setSelectedImage] = useState<LightboxImage | null>(null);

  if (!images || images.length === 0) {
    return null;
  }

  return (
    <>
      <div data-testid="image-gallery-grid" className="mt-3 flex flex-wrap gap-2.5">
        {images.map((img, idx) => (
          <div
            key={idx}
            data-testid={`image-thumbnail-${idx}`}
            onClick={() => setSelectedImage(img)}
            className="group relative cursor-pointer overflow-hidden rounded-lg border border-zinc-700/60 bg-zinc-900 shadow-sm transition-all hover:border-blue-500/60 hover:shadow-md max-w-[240px]"
          >
            <img
              src={img.url}
              alt={img.alt || 'Generated asset'}
              className="h-36 w-full object-cover transition-transform duration-200 group-hover:scale-105"
              loading="lazy"
            />
            <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent opacity-0 group-hover:opacity-100 transition-opacity flex items-end p-2">
              <span className="text-[11px] text-white font-medium truncate drop-shadow">
                {img.alt || 'Click to zoom 🔍'}
              </span>
            </div>
          </div>
        ))}
      </div>

      <LightboxModal
        image={selectedImage}
        onClose={() => setSelectedImage(null)}
      />
    </>
  );
};
