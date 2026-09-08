import { MOCK_CAPTURES } from '../mock/captures';
import { relativeTime } from '../lib/time';

/**
 * Triage view for captures. Read-only at step 4 — the capture box and the routing
 * actions arrive at step 5, once there is a real API behind them.
 */
export function Inbox() {
  const unprocessed = MOCK_CAPTURES.filter((c) => c.status === 'inbox');
  const routed = MOCK_CAPTURES.filter((c) => c.status === 'routed');

  return (
    <>
      <header className="page-header">
        <h1>Inbox</h1>
        <p className="page-subtitle">
          {unprocessed.length} to triage
          {routed.length > 0 && ` · ${routed.length} routed`}
        </p>
      </header>

      <ul className="list">
        {unprocessed.map((capture) => (
          <li key={capture.id} className="list-item">
            <div className="list-item-meta">
              <span className={`kind kind-${capture.kind}`}>{capture.kind}</span>
              <time dateTime={capture.createdAt}>{relativeTime(capture.createdAt)}</time>
            </div>
            <p className="list-item-body">{capture.body}</p>
          </li>
        ))}
      </ul>

      {routed.length > 0 && (
        <>
          <h2 className="section-heading">Routed</h2>
          <ul className="list">
            {routed.map((capture) => (
              <li key={capture.id} className="list-item is-muted">
                <div className="list-item-meta">
                  <span className={`kind kind-${capture.kind}`}>{capture.kind}</span>
                  <time dateTime={capture.createdAt}>
                    {relativeTime(capture.createdAt)}
                  </time>
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
