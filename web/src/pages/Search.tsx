import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { SOURCES, type SourceId, type SearchResult } from '../types';
import { reindex, search, summarizeIndexStats } from '../api/search';
import { ApiError } from '../api/client';
import { SourceBadge } from '../components/SourceBadge';
import { relativeTime } from '../lib/time';

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

/**
 * When a result is — formatted by what its timestamp actually means.
 *
 * `modifiedAt` carries a different fact per source (see types.ts), so one format
 * cannot serve all five. A calendar event needs an absolute date: "Practice 2"
 * with no date is unusable, and "in 3 days" is not how anyone thinks about a race
 * weekend. Everything else is a last-changed time, where relative reads better —
 * "edited 2 days ago" beats a timestamp you have to mentally subtract.
 *
 * Tasks use their due date, so they get the absolute treatment too: a deadline is
 * a point in time, not an elapsed one.
 */
function whenOf(result: SearchResult): string {
  const iso = result.modifiedAt;
  if (!iso) return '';

  if (result.source === 'google_calendar' || result.source === 'google_tasks') {
    const when = new Date(iso);
    const date = when.toLocaleDateString(undefined, {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
    });
    // Midnight almost always means all-day or no time set, rather than an event
    // genuinely at 00:00 — showing "12:00 AM" would be precise and misleading.
    const isMidnight = when.getHours() === 0 && when.getMinutes() === 0;
    if (isMidnight) return date;

    return `${date} · ${when.toLocaleTimeString(undefined, {
      hour: 'numeric',
      minute: '2-digit',
    })}`;
  }

  return relativeTime(iso);
}

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
      setIndexNote(summarizeIndexStats(await reindex()));
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
        {results.map((result) => {
          const body = (
            <>
              <div className="list-item-meta">
                {/* §12: results must always identify their source. */}
                <SourceBadge source={result.source} />
                {result.modifiedAt && <span>{whenOf(result)}</span>}
              </div>
              <h3 className="list-item-title">{result.title}</h3>
              {result.excerpt && <p className="list-item-body">{result.excerpt}</p>}
            </>
          );

          // Only notes have somewhere to go. Tasks, events and repos are shown
          // but not linked, because a row that looks clickable and isn't is
          // worse than one that plainly isn't — which is what every result used
          // to be. Their detail views arrive with their own pages.
          return result.source === 'obsidian' ? (
            <li key={result.id}>
              <Link
                to={`/notes/${result.id}`}
                className="list-item is-clickable"
                data-source={result.source}
              >
                {body}
              </Link>
            </li>
          ) : (
            <li key={result.id} className="list-item" data-source={result.source}>
              {body}
            </li>
          );
        })}
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
