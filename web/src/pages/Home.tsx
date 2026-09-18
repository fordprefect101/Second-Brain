import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import type { CaptureItem, Note } from '../types';
import { listCaptures } from '../api/captures';
import { listNotes } from '../api/notes';
import { listEvents, needsReconnect, startGoogleConnect, type CalendarEvent } from '../api/google';
import { CaptureBox } from '../components/CaptureBox';
import { SourceBadge } from '../components/SourceBadge';
import { daysAgo, isToday, relativeTime } from '../lib/time';

/**
 * Today — the daily landing view.
 *
 * Answers "what is going on right now?" rather than listing everything. Three
 * questions, in the order they get asked: what did I capture today, what is still
 * waiting, what was I working on.
 *
 * The capture box is here as well as in Inbox because Plan.md §11 asks for FAST
 * capture. A capture box you have to navigate to is one you stop using — this is
 * the first screen, so a thought can be recorded without going anywhere.
 *
 * Calendar events belong in this view and are absent until Phase 3. The card says
 * so rather than pretending the day has nothing in it.
 */
export function Home() {
  const [captures, setCaptures] = useState<CaptureItem[]>([]);
  const [notes, setNotes] = useState<Note[]>([]);
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [calendarReconnect, setCalendarReconnect] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;

    // allSettled, not all: Google being disconnected must not blank the captures
    // and notes. Each source degrades on its own.
    Promise.allSettled([listCaptures('inbox'), listNotes(50), listEvents(1)]).then(
      ([capturesResult, notesResult, eventsResult]) => {
        if (!active) return;
        if (capturesResult.status === 'fulfilled') setCaptures(capturesResult.value);
        if (notesResult.status === 'fulfilled') setNotes(notesResult.value);
        if (eventsResult.status === 'fulfilled') setEvents(eventsResult.value);
        else if (needsReconnect(eventsResult.reason)) setCalendarReconnect(true);
        setLoading(false);
      },
    );
    return () => {
      active = false;
    };
  }, []);

  /**
   * When an event is, not just what time it starts.
   *
   * The events route takes `days` and returns "now to now + days" — a rolling
   * window, not a calendar day, despite the docstring saying "today". So with the
   * default of 1, a 9am meeting *tomorrow* is in this list, and rendering it as
   * bare "9:00 AM" on a page titled Today reads as nine o'clock this morning.
   *
   * The date is therefore shown for anything not today, and omitted for anything
   * that is — a date on every row of a Today view is noise.
   */
  const timeOf = (event: CalendarEvent) => {
    const time = event.allDay
      ? 'all day'
      : new Date(event.start).toLocaleTimeString(undefined, {
          hour: 'numeric',
          minute: '2-digit',
        });

    if (isToday(event.start)) return time;

    // daysAgo counts backwards, so the future is negative: -1 is tomorrow.
    // Naming it beats "Thu 18 Sep" for the one case that comes up constantly.
    const day =
      daysAgo(event.start) === -1
        ? 'tomorrow'
        : new Date(event.start).toLocaleDateString(undefined, {
            weekday: 'short',
            day: 'numeric',
            month: 'short',
          });

    return `${day} · ${time}`;
  };

  const capturedToday = captures.filter((c) => isToday(c.createdAt));
  const waiting = captures.filter((c) => !isToday(c.createdAt));
  const touchedThisWeek = notes.filter((n) => daysAgo(n.modifiedAt) <= 7);

  const today = new Date();

  return (
    <>
      <header className="page-header">
        <h1>Today</h1>
        <p className="page-subtitle">
          {today.toLocaleDateString(undefined, {
            weekday: 'long',
            day: 'numeric',
            month: 'long',
          })}
        </p>
      </header>

      <CaptureBox onCaptured={(item) => setCaptures((current) => [item, ...current])} />

      <div className="cards">
        <Link to="/inbox" className="card">
          <span className="card-value">{loading ? '·' : captures.length}</span>
          <span className="card-label">to triage</span>
        </Link>
        <Link to="/knowledge" className="card">
          <span className="card-value">{loading ? '·' : notes.length}</span>
          <span className="card-label">notes</span>
        </Link>
        <Link to="/tasks" className="card">
          <span className="card-value">{loading ? '·' : events.length}</span>
          <span className="card-label">events today</span>
        </Link>
      </div>

      {calendarReconnect && (
        <p className="banner">
          Google connection expired.{' '}
          <button type="button" className="link-button" onClick={startGoogleConnect}>
            reconnect
          </button>
        </p>
      )}

      {events.length > 0 && (
        <>
          <h2 className="section-heading">Schedule</h2>
          <ul className="list">
            {events.map((event) => (
              <li key={event.id} className="list-item">
                <div className="list-item-meta">
                  <span className="event-time">{timeOf(event)}</span>
                  {/* Which calendar, not just "Google" — with eight of them,
                      "Formula 1" vs "Family" is what makes an event legible. */}
                  <span className="source-badge">
                    {event.calendarName ?? 'Calendar'}
                  </span>
                </div>
                <h3 className="list-item-title">{event.title}</h3>
                {event.location && (
                  <p className="list-item-body">{event.location}</p>
                )}
              </li>
            ))}
          </ul>
        </>
      )}

      {capturedToday.length > 0 && (
        <>
          <h2 className="section-heading">Captured today</h2>
          <ul className="list">
            {capturedToday.map((capture) => (
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

      {waiting.length > 0 && (
        <>
          <h2 className="section-heading">
            Waiting in the inbox · {waiting.length}
          </h2>
          <ul className="list">
            {waiting.slice(0, 4).map((capture) => (
              <li key={capture.id} className="list-item is-muted">
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
          {waiting.length > 4 && (
            <p className="more-link">
              <Link to="/inbox">{waiting.length - 4} more in the inbox →</Link>
            </p>
          )}
        </>
      )}

      <h2 className="section-heading">
        {touchedThisWeek.length > 0 ? 'Worked on this week' : 'Recently modified'}
      </h2>
      {loading ? (
        <p className="list-item-body">Loading…</p>
      ) : notes.length === 0 ? (
        <div className="empty">
          <p className="empty-title">No notes</p>
          <p className="empty-detail">
            Set OBSIDIAN_VAULT_PATH in .env, or add notes to the vault.
          </p>
        </div>
      ) : (
        <ul className="list">
          {(touchedThisWeek.length > 0 ? touchedThisWeek : notes)
            .slice(0, 5)
            .map((note) => (
              <li key={note.id} className="list-item">
                <div className="list-item-meta">
                  <SourceBadge source={note.source} />
                  <time dateTime={note.modifiedAt}>
                    {relativeTime(note.modifiedAt)}
                  </time>
                </div>
                <h3 className="list-item-title">{note.title}</h3>
              </li>
            ))}
        </ul>
      )}
    </>
  );
}
