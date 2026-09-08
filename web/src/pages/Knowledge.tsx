import { useEffect, useState } from 'react';
import type { Note } from '../types';
import { getNote, listNotes, type NoteDetail } from '../api/notes';
import { ApiError } from '../api/client';
import { SourceBadge } from '../components/SourceBadge';
import { relativeTime } from '../lib/time';

/**
 * Notes from the real vault.
 *
 * Nothing in this file knows that an Obsidian note is a file on disk — there is
 * no path to render, because Note has no path field. Adding Notion in Phase 4
 * means a second provider, not a change here.
 *
 * Bodies are fetched on demand rather than with the list. That is not a
 * performance tweak: caching bodies would make the Personal OS a second source of
 * truth for the vault's contents (Plan.md §2).
 */
export function Knowledge() {
  const [notes, setNotes] = useState<Note[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<NoteDetail | null>(null);

  useEffect(() => {
    let active = true;
    listNotes()
      .then((items) => active && setNotes(items))
      .catch((err) => {
        if (!active) return;
        setError(err instanceof ApiError ? err.message : 'Could not load notes.');
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  async function toggle(id: string) {
    if (openId === id) {
      setOpenId(null);
      setDetail(null);
      return;
    }
    setOpenId(id);
    setDetail(null);
    try {
      setDetail(await getNote(id));
    } catch {
      setError('Could not open that note.');
    }
  }

  return (
    <>
      <header className="page-header">
        <h1>Knowledge</h1>
        <p className="page-subtitle">
          {loading ? 'Reading vault…' : `${notes.length} notes`}
        </p>
      </header>

      {error && <p className="banner is-error">{error}</p>}

      {!loading && notes.length === 0 && !error && (
        <div className="empty">
          <p className="empty-title">No notes found</p>
          <p className="empty-detail">The vault is empty, or the path is wrong.</p>
        </div>
      )}

      <ul className="list">
        {notes.map((note) => (
          <li key={note.id} className="list-item">
            <div className="list-item-meta">
              <SourceBadge source={note.source} />
              <time dateTime={note.modifiedAt}>{relativeTime(note.modifiedAt)}</time>
              <button
                type="button"
                className="link-button"
                onClick={() => void toggle(note.id)}
              >
                {openId === note.id ? 'close' : 'open'}
              </button>
            </div>

            <h3 className="list-item-title">{note.title}</h3>

            {openId === note.id ? (
              detail ? (
                <pre className="note-body">{detail.body}</pre>
              ) : (
                <p className="list-item-body">Loading…</p>
              )
            ) : (
              <p className="list-item-body">{note.excerpt}</p>
            )}

            {note.tags.length > 0 && (
              <div className="tags">
                {note.tags.map((tag) => (
                  <span key={tag} className="tag">
                    #{tag}
                  </span>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
    </>
  );
}
