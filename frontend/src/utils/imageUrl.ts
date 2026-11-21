/**
 * Rewrites cross-site Google FIFE / gg-dl image URLs to our same-origin authenticated
 * proxy (`/api/images/proxy?url=...`) so Chrome never blocks generated Imagen images
 * with `ERR_BLOCKED_BY_RESPONSE.NotSameSite` (`Cross-Origin-Resource-Policy: same-site`).
 */
const GOOGLE_FIFE_IMAGE_RE =
  /^https:\/\/(?:[a-zA-Z0-9-]+\.)*(?:googleusercontent\.com\/(?:rd-)?gg|fife\.usercontent\.google\.com\/)/i;

export function resolveRenderableImageUrl(url: string): string {
  if (!url || typeof url !== 'string') return '';
  const trimmed = url.trim();
  if (!trimmed) return '';
  if (trimmed.startsWith('/files/') || trimmed.startsWith('/api/images/proxy')) {
    return trimmed;
  }
  if (GOOGLE_FIFE_IMAGE_RE.test(trimmed)) {
    return `/api/images/proxy?url=${encodeURIComponent(trimmed)}`;
  }
  return trimmed;
}
