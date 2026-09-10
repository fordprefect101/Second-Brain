import { api } from './client';
import type { SearchResult } from '../types';

interface SourceStats {
  seen: number;
  indexed: number;
  skipped: number;
  removed: number;
}

export interface IndexStats {
  notes: SourceStats;
  captures: SourceStats;
  events: SourceStats;
  tasks: SourceStats;
  repositories: SourceStats;
  errors: Record<string, string>;
}

export function search(
  query: string,
  source?: string,
  limit = 20,
): Promise<SearchResult[]> {
  const filter = source ? `&source=${encodeURIComponent(source)}` : '';
  return api.get<SearchResult[]>(
    `/search?q=${encodeURIComponent(query)}&limit=${limit}${filter}`,
  );
}

export function reindex(): Promise<IndexStats> {
  return api.post<IndexStats>('/search/reindex');
}
