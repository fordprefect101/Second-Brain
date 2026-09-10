import { useEffect, useRef, useState } from 'react';
import { SOURCES, type SourceId, type SearchResult } from '../types';
import { reindex, search } from '../api/search';
import { ApiError } from '../api/client';
import { SourceBadge } from '../components/SourceBadge';

/**
 * Unified search across notes, captures, calendar, tasks, and repositories.
 *
 * Keyword search only. Semantic search arrives in the AI layer, once there are
 * real failed searches to justify it (ADR-007) — and the failures are visible
 * here: search for a synonym of something you know exists and watch it miss.
 *
 * The source filter is not cosmetic. Subscribed calendars produce far more rows
 * than every other source combined, so without a way to exclude them a common
 * word returns a wall of race sessions. Filtering is how the index stays useful
 * while it is unbalanced.
 */

const FILTERS: (SourceId | null)[] = [
  null,
  'obsidian',
  'personal_os',
  'google_calendar',
  'google_tasks',
  'github',
];

export function Search() {
  const [query, setQuery] = useState('');
  const [source, setSource] = useState<SourceId | null>(null);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [indexNote, setIndexNote] = useState<string | null>(null);

  // Tracks the latest request so a slow early response cannot overwrite a fast
  // later one — type "aud" then "audio" quickly and the results must be for
  // "audio", whichever request finishes first.
  const latest = useRef(0);

  useEffect(() => {
    const text = query.trim();
    if (!text) {
      setResults([]);
      setSearching(false);
      return;
    }

    setSearching(true);
    const requestId = ++latest.current;

    // Wait for a pause in typing rather than firing per keystroke.
    const timer = setTimeout(() => {
      search(text, source ?? undefined)
        .then((hits) => {
          if (requestId !== latest.current) return; // superseded by a newer query
          setResults(hits);
          setError(null);
        })
        .catch((err) => {
          if (requestId !== latest.current) return;
          setError(err instanceof ApiError ? err.message : 'Search failed.');
        })
        .finally(() => {
          if (requestId === latest.current) setSearching(false);
        });
    }, 180);

    return () => clearTimeout(timer);
  }, [query, source]);

  async function rebuild() {
    setIndexNote('Reindexing…');
    try {
      const s = await reindex();
      const parts = [
        `${s.notes.indexed + s.notes.skipped} notes`,
        `${s.captures.indexed + s.captures.skipped} captures`,
        `${s.events.indexed + s.events.skipped} events`,
        `${s.tasks.indexed + s.tasks.skipped} tasks`,
        `${s.repositories.indexed + s.repositories.skipped} repos`,
      ];
      const failed = Object.keys(s.errors ?? {});
      setIndexNote(
        parts.join(' · ') +
          // A source that failed must be named. A silently smaller index looks
          // identical to a correct one.
          (failed.length ? ` — failed: ${failed.join(', ')}` : ''),
      );
    } catch {
      setIndexNote('Reindex failed.');
    }
  }

  return (
    <>
      <header className="page-header">
        <h1>Search</h1>
        <p className="page-subtitle">Everything, in one place</p>
      </header>

      <input
        className="search-input"
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search notes, captures, events, tasks, repositories…"
        aria-label="Search"
        autoFocus
      />

      <div className="capture-kinds" style={{ marginBottom: 20 }}>
        {FILTERS.map((id) => (
          <button
            key={id ?? 'all'}
            type="button"
            className={source === id ? 'chip is-selected' : 'chip'}
            onClick={() => setSource(id)}
          >
            {id ? SOURCES[id].label : 'all'}
          </button>
        ))}
      </div>

      {error && <p className="banner is-error">{error}</p>}

      {query.trim() && !searching && results.length === 0 && !error && (
        <div className="empty">
          <p className="empty-title">No matches</p>
          <p className="empty-detail">
            Keyword search matches word stems, so synonyms and typos miss. Rebuild
            the index if you have added things since the last one.
          </p>
        </div>
      )}

      <ul className="list">
        {results.map((result) => (
          <li key={result.id} className="list-item">
            <div className="list-item-meta">
              {/* §12: results must always identify their source. */}
              <SourceBadge source={result.source} />
              <span className="rank">{result.rank.toFixed(3)}</span>
            </div>
            <h3 className="list-item-title">{result.title}</h3>
            {result.excerpt && <p className="list-item-body">{result.excerpt}</p>}
          </li>
        ))}
      </ul>

      <div className="index-controls">
        <button type="button" className="link-button" onClick={() => void rebuild()}>
          rebuild index
        </button>
        {indexNote && <span className="index-note">{indexNote}</span>}
      </div>
    </>
  );
}
