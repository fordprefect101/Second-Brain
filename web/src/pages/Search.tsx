import { useEffect, useRef, useState } from 'react';
import type { SearchResult } from '../types';
import { reindex, search } from '../api/search';
import { ApiError } from '../api/client';
import { SourceBadge } from '../components/SourceBadge';

/**
 * Unified search across notes and captures.
 *
 * Keyword search only. Semantic search arrives in the AI layer, once there are
 * real failed searches to justify it (ADR-007) — and the failures are visible
 * here: search for a synonym of something in your vault and watch it miss.
 */
export function Search() {
  const [query, setQuery] = useState('');
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
      search(text)
        .then((hits) => {
          if (requestId !== latest.current) return; // a newer query superseded this
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
  }, [query]);

  async function rebuild() {
    setIndexNote('Reindexing…');
    try {
      const stats = await reindex();
      setIndexNote(
        `Notes: ${stats.notes.indexed} indexed, ${stats.notes.skipped} unchanged` +
          ` · Captures: ${stats.captures.indexed} indexed, ${stats.captures.skipped} unchanged`,
      );
    } catch {
      setIndexNote('Reindex failed.');
    }
  }

  return (
    <>
      <header className="page-header">
        <h1>Search</h1>
        <p className="page-subtitle">Notes and captures</p>
      </header>

      <input
        className="search-input"
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search everything…"
        aria-label="Search"
        autoFocus
      />

      {error && <p className="banner is-error">{error}</p>}

      {query.trim() && !searching && results.length === 0 && !error && (
        <div className="empty">
          <p className="empty-title">No matches</p>
          <p className="empty-detail">
            Keyword search matches word stems, so synonyms and typos miss. Reindex if
            you have added notes since the last one.
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
            <p className="list-item-body">{result.excerpt}</p>
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
