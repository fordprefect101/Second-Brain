import { SOURCES, type SourceId } from '../types';

/**
 * Connection status per integration. Plan.md §22 asks for clear connection status,
 * so the list shows every planned source and its honest state.
 *
 * Hardcoded at step 4. Backed by the `connections` table once step 5 adds API calls.
 */
const CONNECTION_PHASE: Record<SourceId, string> = {
  personal_os: 'built in',
  obsidian: 'Phase 2',
  google_calendar: 'Phase 3',
  google_tasks: 'Phase 3',
  notion: 'Phase 4',
  google_drive: 'Phase 4',
  google_sheets: 'Phase 4',
  gmail: 'Phase 4',
  github: 'Phase 4',
  spotify: 'Phase 4',
};

export function Settings() {
  return (
    <>
      <header className="page-header">
        <h1>Settings</h1>
        <p className="page-subtitle">Connections and configuration</p>
      </header>

      <h2 className="section-heading">Connections</h2>
      <ul className="list">
        {Object.values(SOURCES).map((source) => {
          const connected = source.id === 'personal_os';
          return (
            <li key={source.id} className="list-item connection-row">
              <span className="list-item-title">{source.label}</span>
              <span className={connected ? 'status is-connected' : 'status'}>
                {connected ? 'connected' : CONNECTION_PHASE[source.id]}
              </span>
            </li>
          );
        })}
      </ul>
    </>
  );
}
