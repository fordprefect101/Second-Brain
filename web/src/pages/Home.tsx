import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import type { CaptureItem, Note } from '../types';
import { listCaptures } from '../api/captures';
import { listNotes } from '../api/notes';
import { SourceBadge } from '../components/SourceBadge';
import { relativeTime } from '../lib/time';

/**
 * The daily landing view. Captures are real; notes are still mock until step 7.
 *
 * This component fetches the SAME data as Inbox, independently. Navigate between
 * the two and watch the network tab: every visit refetches, with a loading flicker
 * each time, because nothing is shared or cached. Two components, two copies, two
 * requests.
 *
 * That duplication is deliberate and is exactly the problem step 5.5 evaluates.
 * Do not fix it by lifting state into a context — that is a third option worth
 * discussing on its merits, not a workaround to apply quietly.
 */
export function Home() {
  const [captures, setCaptures] = useState<CaptureItem[]>([]);
  const [notes, setNotes] = useState<Note[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;

    // Two independent requests, and Inbox and Knowledge each fire their own copies
    // when you navigate there. Nothing is shared or cached — the duplication step
    // 5.5 was meant to evaluate, now visible across four components.
    Promise.allSettled([listCaptures('inbox'), listNotes(50)])
      .then(([capturesResult, notesResult]) => {
        if (!active) return;
        if (capturesResult.status === 'fulfilled') setCaptures(capturesResult.value);
        if (notesResult.status === 'fulfilled') setNotes(notesResult.value);
      })
      .finally(() => {
        // Home degrades quietly: a dashboard count is not worth an error banner.
        // Inbox and Knowledge surface the real errors.
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  const recentNotes = notes.slice(0, 3);

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
          <span className="card-value">{loading ? '·' : captures.length}</span>
          <span className="card-label">to triage</span>
        </Link>
        <Link to="/knowledge" className="card">
          <span className="card-value">{loading ? '·' : notes.length}</span>
          <span className="card-label">notes</span>
        </Link>
        <div className="card is-inert">
          <span className="card-value">—</span>
          <span className="card-label">events · Phase 3</span>
        </div>
      </div>

      {captures.length > 0 && (
        <>
          <h2 className="section-heading">Latest captures</h2>
          <ul className="list">
            {captures.slice(0, 3).map((capture) => (
              <li key={capture.id} className="list-item">
                <div className="list-item-meta">
                  <span className={`kind kind-${capture.kind}`}>{capture.kind}</span>
                  <time dateTime={capture.createdAt}>
                    {relativeTime(capture.createdAt)}
                  </time>
                </div>
                <p className="list-item-body">{capture.body}</p>
              </li>
            ))}
          </ul>
        </>
      )}

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
