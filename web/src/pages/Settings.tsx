import { useEffect, useState } from 'react';
import { SOURCES, type SourceId } from '../types';
import {
  disconnectGoogle,
  googleStatus,
  startGoogleConnect,
  type GoogleStatus,
} from '../api/google';
import { reindex, summarizeIndexStats } from '../api/search';

/**
 * Connections and configuration.
 *
 * Plan.md §22 asks for clear connection status. "Clear" includes being honest
 * about what disconnecting does NOT do — see the note below the button.
 */

const GOOGLE_SOURCES: SourceId[] = ['google_calendar', 'google_tasks'];

const PHASE: Record<SourceId, string> = {
  personal_os: 'built in',
  obsidian: 'connected',
  google_calendar: 'Phase 3',
  google_tasks: 'Phase 3',
  notion: 'Phase 4',
  google_drive: 'Phase 4',
  google_sheets: 'Phase 4',
  gmail: 'Phase 4',
  github: 'connected',
  spotify: 'Phase 4',
};

export function Settings() {
  const [google, setGoogle] = useState<GoogleStatus | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [indexNote, setIndexNote] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    googleStatus()
      .then((s) => active && setGoogle(s))
      .catch(() => undefined); // status never matters enough to show an error
    return () => {
      active = false;
    };
  }, []);

  async function disconnect() {
    const result = await disconnectGoogle();
    setNote(result.note);
    setGoogle(await googleStatus());
  }

  async function rebuild() {
    setIndexNote('Reindexing…');
    try {
      setIndexNote(summarizeIndexStats(await reindex()));
    } catch {
      setIndexNote('Reindex failed.');
    }
  }

  function googleLabel(): string {
    if (!google) return '…';
    switch (google.state) {
      case 'connected':
        return 'connected';
      case 'expired':
        return 'expired — reconnect';
      case 'not_configured':
        return 'no credentials';
      case 'error':
        return 'error';
      default:
        return 'not connected';
    }
  }

  return (
    <>
      <header className="page-header">
        <h1>Settings</h1>
        <p className="page-subtitle">Connections and configuration</p>
      </header>

      <h2 className="section-heading">Google</h2>
      <div className="list-item">
        <div className="task-row">
          <div className="task-main">
            <span className="list-item-title">Calendar and Tasks</span>
            <span className={google?.connected ? 'status is-connected' : 'status'}>
              {googleLabel()}
            </span>
          </div>
          {google?.state === 'not_configured' ? null : google?.connected ? (
            <button type="button" className="link-button" onClick={() => void disconnect()}>
              disconnect
            </button>
          ) : (
            <button type="button" className="button" onClick={startGoogleConnect}>
              Connect
            </button>
          )}
        </div>

        {google?.scopes && (
          <p className="list-item-ref">
            {google.scopes.map((s) => s.replace('https://www.googleapis.com/auth/', '')).join(' · ')}
          </p>
        )}
        {google?.detail && <p className="list-item-body">{google.detail}</p>}
        {note && <p className="list-item-body">{note}</p>}
      </div>

      {/* Lives here since the bento replaced the Search page, which was its only
          other entry point. The API also rebuilds a stale index at startup. */}
      <h2 className="section-heading">Search index</h2>
      <div className="list-item">
        <div className="task-row">
          <div className="task-main">
            <span className="list-item-title">Rebuild after adding or deleting notes</span>
          </div>
          <button type="button" className="button" onClick={() => void rebuild()}>
            Rebuild
          </button>
        </div>
        {indexNote && <p className="list-item-body">{indexNote}</p>}
      </div>

      <h2 className="section-heading">All sources</h2>
      <ul className="list">
        {Object.values(SOURCES).map((source) => {
          const isGoogle = GOOGLE_SOURCES.includes(source.id);
          // GitHub belongs in this list. Without it, PHASE supplied its label —
          // which is the string 'connected' — while the style said otherwise, so
          // the same word appeared twice on one screen in two different colours,
          // one meaning connected and one meaning not yet built.
          //
          // This asserts rather than checks, like personal_os and obsidian above
          // it: configured at build time, so a revoked token still reads as
          // connected here until the Projects tile says otherwise. Worth replacing
          // with a real status probe when one exists.
          const live =
            source.id === 'personal_os' ||
            source.id === 'obsidian' ||
            source.id === 'github' ||
            (isGoogle && google?.connected);
          return (
            <li key={source.id} className="list-item connection-row">
              <span className="list-item-title">{source.label}</span>
              <span className={live ? 'status is-connected' : 'status'}>
                {live ? 'connected' : PHASE[source.id]}
              </span>
            </li>
          );
        })}
      </ul>
    </>
  );
}
