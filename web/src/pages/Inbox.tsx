import { useEffect, useState } from 'react';
import type { CaptureItem } from '../types';
import { archiveCapture, listCaptures } from '../api/captures';
import { ApiError } from '../api/client';
import { CaptureBox } from '../components/CaptureBox';
import { relativeTime } from '../lib/time';

/**
 * Capture and triage, backed by the real API.
 *
 * Note how much of this file is not about captures. Three pieces of state
 * (data / loading / error), an effect to fetch, a mounted flag to avoid setting
 * state after unmount, and a manual local update after each mutation. That is the
 * cost of hand-rolled data fetching, and it will be repeated in Home.tsx —
 * which is the evidence for the step 5.5 comparison.
 */
export function Inbox() {
  const [captures, setCaptures] = useState<CaptureItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    listCaptures('inbox')
      .then((items) => {
        if (active) setCaptures(items);
      })
      .catch((err) => {
        if (active) {
          setError(err instanceof ApiError ? err.message : 'Could not load captures.');
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    // Without this, a fast unmount (React StrictMode double-mounts in dev) sets
    // state on a component that is gone.
    return () => {
      active = false;
    };
  }, []);

  async function archive(id: string) {
    // Optimistic: remove immediately, restore if the request fails. Triage should
    // feel instant, and this action is low-stakes enough to assume success.
    const previous = captures;
    setCaptures((current) => current.filter((c) => c.id !== id));
    try {
      await archiveCapture(id);
    } catch {
      setCaptures(previous);
      setError('Could not archive that item.');
    }
  }

  return (
    <>
      <header className="page-header">
        <h1>Inbox</h1>
        <p className="page-subtitle">
          {loading ? 'Loading…' : `${captures.length} to triage`}
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

      <ul className="list">
        {captures.map((capture) => (
          <li key={capture.id} className="list-item">
            <div className="list-item-meta">
              <span className={`kind kind-${capture.kind}`}>{capture.kind}</span>
              <time dateTime={capture.createdAt}>{relativeTime(capture.createdAt)}</time>
              <button
                type="button"
                className="link-button"
                onClick={() => void archive(capture.id)}
              >
                archive
              </button>
            </div>
            <p className="list-item-body">{capture.body}</p>
          </li>
        ))}
      </ul>
    </>
  );
}
