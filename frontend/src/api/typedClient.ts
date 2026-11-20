import createClient from 'openapi-fetch';
import type { paths, components } from './schema';

export const apiClient = createClient<paths>({
  baseUrl: '',
  fetch: (req) => globalThis.fetch(req),
});

export type ApiPaths = paths;
export type ApiComponents = components;
