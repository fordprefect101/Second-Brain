import { MOCK_NOTES } from '../mock/notes';
import { SourceBadge } from '../components/SourceBadge';
import { relativeTime } from '../lib/time';

/**
 * Notes from whichever services provide them. Backed by mock data now; the real
 * ObsidianVaultProvider arrives at step 7.
 *
 * Nothing here knows that an Obsidian note is a file — there is no path to render,
 * because Note deliberately has no path field.
 */
export function Knowledge() {
  const notes = [...MOCK_NOTES].sort((a, b) =>
    b.modifiedAt.localeCompare(a.modifiedAt),
  );

  return (
    <>
      <header className="page-header">
        <h1>Knowledge</h1>
        <p className="page-subtitle">{notes.length} notes</p>
      </header>

      <ul className="list">
        {notes.map((note) => (
          <li key={note.id} className="list-item">
            <div className="list-item-meta">
              <SourceBadge source={note.source} />
              <time dateTime={note.modifiedAt}>{relativeTime(note.modifiedAt)}</time>
            </div>
            <h3 className="list-item-title">{note.title}</h3>
            <p className="list-item-body">{note.excerpt}</p>
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
