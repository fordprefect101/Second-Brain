import { useEffect, useState } from 'react';
import type { CaptureItem } from '../types';
import {
  archiveCapture,
  listCaptures,
  routeCapture,
  undoRoute,
} from '../api/captures';
import { ApiError } from '../api/client';
import { CaptureBox } from '../components/CaptureBox';
import { ListSkeleton } from '../components/Tile';
import { relativeTime } from '../lib/time';

/**
 * Capture and triage.
 *
 * Two exits now. Archive dismisses without creating anything; "send to vault"
 * writes a real markdown file into Obsidian and leaves the capture row behind as a
 * record of what it became.
 *
 * Routing asks for confirmation (Plan.md §22) because it is the only action in this
 * app that modifies the user's own files. Archive does not — it changes one column
 * and is reversible in the data.
 */
export function Inbox() {
  const [captures, setCaptures] = useState<CaptureItem[]>([]);
  const [routed, setRouted] = useState<CaptureItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  function load() {
    return Promise.allSettled([listCaptures('inbox'), listCaptures('routed')]).then(
      ([inbox, sent]) => {
        if (inbox.status === 'fulfilled') setCaptures(inbox.value);
        if (sent.status === 'fulfilled') setRouted(sent.value);
        if (inbox.status === 'rejected') {
          const err = inbox.reason;
          setError(err instanceof ApiError ? err.message : 'Could not load captures.');
        }
      },
    );
  }

  useEffect(() => {
    let active = true;
    load().finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  async function archive(id: string) {
    // Optimistic: triage should feel instant, and this is low-stakes.
    const previous = captures;
    setCaptures((current) => current.filter((c) => c.id !== id));
    try {
      await archiveCapture(id);
    } catch {
      setCaptures(previous);
      setError('Could not archive that item.');
    }
  }

  async function route(capture: CaptureItem) {
    const title = capture.body.split('\n')[0]?.slice(0, 60) ?? '';
    if (!window.confirm(`Create a note in your Obsidian vault?\n\n"${title}"`)) return;

    // NOT optimistic. This writes a file — the UI should reflect what actually
    // happened on disk, not what was hoped for.
    setBusy(capture.id);
    setError(null);
    try {
      await routeCapture(capture.id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not route that item.');
    } finally {
      setBusy(null);
    }
  }

  async function undo(id: string) {
    setBusy(id);
    setError(null);
    try {
      await undoRoute(id);
      await load();
    } catch (err) {
      // A 409 here is usually the note having been edited in Obsidian, and the
      // message explains that — worth showing verbatim rather than replacing.
      setError(err instanceof ApiError ? err.message : 'Could not undo.');
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <header className="page-header">
        <h1>Inbox</h1>
        <p className="page-subtitle">
          {loading ? 'Loading…' : `${captures.length} to triage`}
          {routed.length > 0 && ` · ${routed.length} in the vault`}
        </p>
      </header>

      <CaptureBox onCaptured={(item) => setCaptures((current) => [item, ...current])} />

      {error && <p className="banner is-error">{error}</p>}

      {!loading && captures.length === 0 && !error && (
        <div className="empty">
          <p className="empty-title">Inbox zero</p>
          <p className="empty-detail">Captured thoughts land here for triage.</p>
        </div>
      )}

      {loading && <ListSkeleton rows={4} />}

      <ul className="list">
        {captures.map((capture) => (
          <li key={capture.id} className="list-item" data-source="personal_os">
            <div className="list-item-meta">
              <span className={`kind kind-${capture.kind}`}>{capture.kind}</span>
              <time dateTime={capture.createdAt}>{relativeTime(capture.createdAt)}</time>
              <span className="row-actions">
                <button
                  type="button"
                  className="link-button"
                  disabled={busy === capture.id}
                  onClick={() => void route(capture)}
                >
                  {busy === capture.id ? 'writing…' : 'send to vault'}
                </button>
                <button
                  type="button"
                  className="link-button"
                  onClick={() => void archive(capture.id)}
                >
                  archive
                </button>
              </span>
            </div>
            <p className="list-item-body">{capture.body}</p>
          </li>
        ))}
      </ul>

      {routed.length > 0 && (
        <>
          <h2 className="section-heading">Sent to the vault</h2>
          <ul className="list">
            {routed.map((capture) => (
              <li key={capture.id} className="list-item is-muted">
                <div className="list-item-meta">
                  <span className={`kind kind-${capture.kind}`}>{capture.kind}</span>
                  <time dateTime={capture.createdAt}>
                    {relativeTime(capture.createdAt)}
                  </time>
                  <span className="row-actions">
                    <button
                      type="button"
                      className="link-button"
                      disabled={busy === capture.id}
                      onClick={() => void undo(capture.id)}
                    >
                      {busy === capture.id ? 'undoing…' : 'undo'}
                    </button>
                  </span>
                </div>
                <p className="list-item-body">{capture.body}</p>
                {/* Provenance: what this became. A record, never a live copy. */}
                {capture.routedToRef && (
                  <p className="list-item-ref">→ {capture.routedToRef}</p>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
