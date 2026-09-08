import { Link } from 'react-router-dom';
import { MOCK_CAPTURES } from '../mock/captures';
import { MOCK_NOTES } from '../mock/notes';
import { SourceBadge } from '../components/SourceBadge';
import { relativeTime } from '../lib/time';

/**
 * The daily landing view. Becomes the real Today view at step 10, once captures
 * and notes come from live sources.
 */
export function Home() {
  const toTriage = MOCK_CAPTURES.filter((c) => c.status === 'inbox');
  const recentNotes = [...MOCK_NOTES]
    .sort((a, b) => b.modifiedAt.localeCompare(a.modifiedAt))
    .slice(0, 3);

  return (
    <>
      <header className="page-header">
        <h1>Today</h1>
        <p className="page-subtitle">
          {new Date().toLocaleDateString(undefined, {
            weekday: 'long',
            day: 'numeric',
            month: 'long',
          })}
        </p>
      </header>

      <div className="cards">
        <Link to="/inbox" className="card">
          <span className="card-value">{toTriage.length}</span>
          <span className="card-label">to triage</span>
        </Link>
        <Link to="/knowledge" className="card">
          <span className="card-value">{MOCK_NOTES.length}</span>
          <span className="card-label">notes</span>
        </Link>
        <div className="card is-inert">
          <span className="card-value">—</span>
          <span className="card-label">events · Phase 3</span>
        </div>
      </div>

      <h2 className="section-heading">Recently modified</h2>
      <ul className="list">
        {recentNotes.map((note) => (
          <li key={note.id} className="list-item">
            <div className="list-item-meta">
              <SourceBadge source={note.source} />
              <time dateTime={note.modifiedAt}>{relativeTime(note.modifiedAt)}</time>
            </div>
            <h3 className="list-item-title">{note.title}</h3>
          </li>
        ))}
      </ul>
    </>
  );
}
