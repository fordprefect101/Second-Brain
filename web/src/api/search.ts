import { api } from './client';
import type { SearchResult } from '../types';

export interface IndexStats {
  notes: { seen: number; indexed: number; skipped: number; removed: number };
  captures: { seen: number; indexed: number; skipped: number; removed: number };
}

export function search(query: string, limit = 20): Promise<SearchResult[]> {
  return api.get<SearchResult[]>(
    `/search?q=${encodeURIComponent(query)}&limit=${limit}`,
  );
}

export function reindex(): Promise<IndexStats> {
  return api.post<IndexStats>('/search/reindex');
}
