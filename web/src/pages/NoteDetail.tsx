import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { getNote, type NoteDetail as Note } from '../api/notes';
import { ApiError } from '../api/client';
import { SourceBadge } from '../components/SourceBadge';
import { relativeTime } from '../lib/time';

/**
 * One note, in full.
 *
 * The page that was missing. Search could tell you which note matched and then
 * offered nowhere to go — a result list where nothing opens reads as a report
 * rather than a tool, and it meant the 500-character excerpt was the most anyone
 * could ever see of their own writing.
 *
 * The body is fetched here and never cached into search_index. That is the whole
 * point of the excerpt limit: a stored copy goes stale the moment the file is
 * edited, so the index holds a preview and the real content is read on demand.
 */
export function NoteDetail() {
  const { id } = useParams<{ id: string }>();
  const [note, setNote] = useState<Note | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let active = true;

    setLoading(true);
    getNote(id)
      .then((n) => active && setNote(n))
      .catch((err) => {
        if (!active) return;
        // 404 here is a real state, not a crash: the index can point at a note
        // that has since been deleted or renamed in the vault. Say that plainly
        // rather than showing a generic failure.
        setError(
          err instanceof ApiError && err.status === 404
            ? 'That note is in the index but not in the vault. It may have been renamed or deleted — rebuild the index to clear it.'
            : 'Could not load that note.',
        );
      })
      .finally(() => active && setLoading(false));

    return () => {
      active = false;
    };
  }, [id]);

  if (loading) return <p className="page-subtitle">Loading…</p>;
  if (error) return <p className="banner is-error">{error}</p>;
  if (!note) return null;

  return (
    <article className="note">
      <Link to="/search" className="back-link">
        ← back
      </Link>

      <header className="note-head">
        <h1 className="note-title">{note.title}</h1>
        <div className="note-meta">
          <SourceBadge source={note.source} />
          <span>edited {relativeTime(note.modifiedAt)}</span>
          {note.tags.map((tag) => (
            <span key={tag} className="tag">
              #{tag}
            </span>
          ))}
        </div>
      </header>

      {/* Rendered as pre-wrap rather than parsed Markdown. Honest for now: a
          real renderer is a dependency decision, and showing raw '## Heading'
          is better than silently mangling the formatting of your own notes. */}
      <div className="note-body">{note.body}</div>
    </article>
  );
}
