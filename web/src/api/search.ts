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

/** One line per rebuild: totals per source, then any source that failed. */
export function summarizeIndexStats(s: IndexStats): string {
  const parts = [
    `${s.notes.indexed + s.notes.skipped} notes`,
    `${s.captures.indexed + s.captures.skipped} captures`,
    `${s.events.indexed + s.events.skipped} events`,
    `${s.tasks.indexed + s.tasks.skipped} tasks`,
    `${s.repositories.indexed + s.repositories.skipped} repos`,
  ];
  const failed = Object.keys(s.errors ?? {});
  return (
    parts.join(' · ') +
    // A source that failed must be named. A silently smaller index looks
    // identical to a correct one.
    (failed.length ? ` — failed: ${failed.join(', ')}` : '')
  );
}
