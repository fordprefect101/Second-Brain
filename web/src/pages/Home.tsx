import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import type { CaptureItem, Note } from '../types';
import { listCaptures } from '../api/captures';
import { ApiError } from '../api/client';
import { listNotes } from '../api/notes';
import { listRepos, type Repository } from '../api/github';
import {
  completeTask,
  createTask,
  createTaskList,
  listEvents,
  listTaskLists,
  listTasks,
  startGoogleConnect,
  type CalendarEvent,
  type Task,
  type TaskList,
} from '../api/google';
import { AskBox } from '../components/AskBox';
import { CaptureBox } from '../components/CaptureBox';
import { Tile, TileEmpty, TileError, TileSkeleton } from '../components/Tile';
import { daysAgo, greeting, isToday, relativeTime, todayLabel } from '../lib/time';
import {
  publishTaskChange,
  selectTaskList,
  useSelectedTaskList,
  useTaskChanges,
} from '../lib/tasks';

/**
 * Today — everything on one surface.
 *
 * Previously three stacked lists in a 780px column, which meant scrolling past
 * things that should have been visible at once. A dashboard's job is to be taken
 * in, not read.
 *
 * Every tile shows a preview and expands to its own route. The expanded views are
 * the pages that already existed — Inbox, Tasks, Projects, Knowledge — reused
 * rather than rewritten, because App renders child routes as overlays over this.
 *
 * allSettled, not all: Google being disconnected must not blank the captures and
 * notes. Each tile degrades on its own, which is the same reason the old version
 * used it and matters more now that there are six.
 */
/* The selected list's storage key moved to lib/tasks, which now owns it — the
   expanded view needs the same value, and two components reading one key is how
   they ended up disagreeing about it. */

/** How long a completed task stays undoable before the write actually fires. */
const UNDO_WINDOW_MS = 4500;

/** Sentinel for the select's "create one" option. Not a list id. */
const NEW_LIST = '__new_list__';

/**
 * Turn a rejected fetch into something a person can act on.
 *
 * "Could not load" tells you nothing you did not already infer from the blank
 * tile. What matters is which of three things happened, because each has a
 * different fix: the API is not running, the token expired, or GitHub is rate
 * limiting. Those are all normal states of this system rather than crashes —
 * unverified Google apps get 7-day refresh tokens, so a dead token is a weekly
 * event, not an incident.
 */
function describe(reason: unknown, source: string): string {
  if (reason instanceof ApiError) {
    if (reason.status === 0) return 'API not running on :8000.';
    if (reason.status === 401) {
      return source === 'repos'
        ? 'GitHub token expired or revoked.'
        : 'Google disconnected — reconnect in Settings.';
    }
    if (reason.status === 429) return 'GitHub rate limit reached. Try later.';
    if (reason.status === 503) return 'Source unavailable.';
  }
  return 'Could not load.';
}

export function Home() {
  const [captures, setCaptures] = useState<CaptureItem[]>([]);
  const [notes, setNotes] = useState<Note[]>([]);
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [taskLists, setTaskLists] = useState<TaskList[]>([]);
  const [repos, setRepos] = useState<Repository[]>([]);

  const [loading, setLoading] = useState(true);

  // Ticked off, not yet written. Held here rather than removed from `tasks`, so
  // undo restores the real object instead of a reconstruction.
  const [pending, setPending] = useState<Task[]>([]);
  // The just-captured item, so it can animate in. Capture previously gave no
  // confirmation at all — the textarea cleared and you inferred success from a
  // row appearing, if you happened to be looking at the right part of the tile.
  const [justCaptured, setJustCaptured] = useState<string | null>(null);
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  // Per source, not global. One dead integration must degrade one tile — which
  // is why the fetch uses allSettled — but allSettled then swallows the reason,
  // so it has to be caught here or the failure is invisible.
  const [errors, setErrors] = useState<Record<string, string>>({});

  // Lifted out of TaskPanel so the tile's count can describe what is actually on
  // screen. It read "23" above six rows from one list, because the count was
  // every open task and the body was one list. Two true numbers, shown together,
  // making a false impression.
  //
  // Now lifted again, out of this component entirely (lib/tasks): the expanded
  // view has to show the same list, and it is mounted at the same time as this
  // one, so the choice cannot live in either component's state.
  const selectedList = useSelectedTaskList();

  const load = useCallback(() => {
    setLoading(true);
    setErrors({});

    return Promise.allSettled([
      listCaptures('inbox'),
      listNotes(50),
      listEvents(1),
      listTasks(),
      listTaskLists(),
      listRepos(30),
    ]).then(([c, n, e, t, tl, r]) => {
      const failed: Record<string, string> = {};

      if (c.status === 'fulfilled') setCaptures(c.value);
      else failed.captures = describe(c.reason, 'captures');

      if (n.status === 'fulfilled') setNotes(n.value);
      else failed.notes = describe(n.reason, 'notes');

      if (e.status === 'fulfilled') setEvents(e.value);
      else failed.events = describe(e.reason, 'events');

      if (t.status === 'fulfilled') setTasks(t.value);
      else failed.tasks = describe(t.reason, 'tasks');

      if (tl.status === 'fulfilled') setTaskLists(tl.value);
      else failed.tasks ??= describe(tl.reason, 'tasks');

      if (r.status === 'fulfilled') setRepos(r.value);
      else failed.repos = describe(r.reason, 'repos');

      setErrors(failed);
      setLoading(false);
    });
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Flush nothing on unmount — just stop the timers. Navigating away mid-window
  // would otherwise fire a request from a component that no longer exists.
  useEffect(() => {
    const running = timers.current;
    return () => {
      running.forEach(clearTimeout);
      running.clear();
    };
  }, []);

  /**
   * When an event is, not just what time it starts.
   *
   * /events?days=1 returns "now to now + 24h", not a calendar day — so a 9am
   * meeting tomorrow is in this list, and bare "9:00 AM" on a page titled Today
   * reads as this morning. Date shown for anything not today, omitted for
   * anything that is: a date on every row of a Today view is noise.
   */
  const timeOf = (event: CalendarEvent) => {
    const time = event.allDay
      ? 'all day'
      : new Date(event.start).toLocaleTimeString(undefined, {
          hour: 'numeric',
          minute: '2-digit',
        });

    if (isToday(event.start)) return time;

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

  const open = tasks.filter((t) => !t.completed && !pending.some((p) => p.id === t.id));
  const recentNotes = notes.filter((n) => daysAgo(n.modifiedAt) <= 7);

  // Falls back to the first list, which also covers a stored id whose list has
  // since been deleted.
  const activeList =
    taskLists.find((l) => l.id === selectedList)?.id ?? taskLists[0]?.id ?? null;
  const shownTasks = open.filter((t) => t.listId === activeList);

  // Writes to the shared store, so the expanded view follows — and so does this
  // tile when the choice is made over there instead.
  const chooseList = selectTaskList;

  /**
   * Writes made in the expanded view, applied here.
   *
   * Not a refetch. The two views hold two arrays and the overlay renders over a
   * still-mounted grid, so adding a task in the expanded Tasks page used to leave
   * this tile showing a list that did not contain it. Re-fetching would cost five
   * seconds and a screen of skeletons to learn one fact this already knows.
   *
   * Idempotent by construction, because this also hears its own publishes.
   */
  useTaskChanges((change) => {
    if (change.kind === 'completed') {
      setTasks((current) => current.filter((t) => t.id !== change.task.id));
      setPending((current) => current.filter((t) => t.id !== change.task.id));
    } else {
      setTasks((current) =>
        current.some((t) => t.id === change.task.id)
          ? current
          : [change.task, ...current],
      );
    }
  });

  /**
   * Ticking a task off, with a window to change your mind.
   *
   * The write is DELAYED rather than optimistic. Optimistic means sending
   * immediately and rolling back on failure — which leaves no way to undo a
   * correct write, and `complete_task` only goes one way: there is no API to
   * un-complete a task, so a rollback could not have worked anyway.
   *
   * So the row leaves immediately, an undo sits there for a few seconds, and the
   * request only fires when that window closes. Undo inside the window means
   * nothing was ever sent — and an undo that never touched the network cannot
   * itself fail.
   */
  function complete(task: Task) {
    setPending((current) => [...current, task]);

    const timer = setTimeout(() => {
      // Announced when the write actually fires, not when the row is ticked.
      // During the undo window nothing has been sent, so the expanded view is
      // right to keep showing the task — it is still open.
      publishTaskChange({ kind: 'completed', task });

      void completeTask(task.id).catch(() => {
        // Put it back. The task is still open in Google, so the tile showing it
        // again is the truthful state, not a rollback of something that happened.
        // Publish only — the subscriber above is what returns it to this list.
        publishTaskChange({ kind: 'restored', task });
      });
      setPending((current) => current.filter((t) => t.id !== task.id));
      timers.current.delete(task.id);
    }, UNDO_WINDOW_MS);

    timers.current.set(task.id, timer);
  }

  async function addList(name: string) {
    const created = await createTaskList(name);
    setTaskLists((current) => [...current, created]);
    chooseList(created.id);
  }

  function undoComplete(task: Task) {
    const timer = timers.current.get(task.id);
    if (timer) clearTimeout(timer);
    timers.current.delete(task.id);
    setPending((current) => current.filter((t) => t.id !== task.id));
  }

  async function add(listId: string, title: string) {
    const created = await createTask(listId, title);

    // Publish, and do not also update state here.
    //
    // This used to do both, and every added task appeared twice. The publisher
    // hears its own event, so the subscriber above had already queued the
    // prepend — its `some(id)` guard ran against a state that did not contain
    // the task yet, passed, and added it. The unconditional prepend that
    // followed then added it a second time.
    //
    // The guard cannot save you here: both updates are queued in the same pass,
    // and only one of them was checking. So there is one write path, not two.
    // (Prepend rather than append is the subscriber's job now — the tile shows
    // the first few of a list that can hold twenty, and a new task appended off
    // the end of the preview reads exactly like a failed write.)
    publishTaskChange({ kind: 'added', task: created });
  }

  return (
    <>
      {/* The page says what it is before it shows you anything. Five tiles and no
          header starts mid-sentence — and the greeting is the one line on this
          surface that is about you rather than about your data. */}
      <div className="day-head">
        <h1 className="day-greeting">{greeting()}</h1>
        <span className="day-date">{todayLabel()}</span>
      </div>

      {/* Outside .bento: with column-count, a child becomes a column item, and
          Ask has to span the full width. */}
      <AskBox />

      {/* The hero band: the two tiles you act on rather than read. Explicitly
          asymmetric — 1.618 : 1 — because the masonry region below can only make
          every tile the same width, and equal width says every tile matters
          equally. On a Today view that is false.

          Except on a day with nothing scheduled, when it is false the other way.
          Giving 60% of the most prominent row to the words "Nothing scheduled" is
          the same dead-space bug the old fixed grid had, just moved. So the weight
          follows the content: an empty calendar stops outranking a full inbox. */}
      <div className="bento-hero" data-weighted={loading || events.length > 0 ? 'today' : 'even'}>

      {/* No expandTo: there is no calendar page yet, and an expand button that
          lands on "not found" is worse than no expand button. It gets one when
          the page exists. */}
      <Tile
        title="Today"
        count={events.length}
        accent="google_calendar"
        className="area-today tile-hero"
      >
        {loading ? (
          <TileSkeleton rows={4} />
        ) : errors.events ? (
          <TileError
            onAction={
              errors.events.includes('disconnected') ? startGoogleConnect : load
            }
            actionLabel={
              errors.events.includes('disconnected') ? 'reconnect' : 'retry'
            }
          >
            {errors.events}
          </TileError>
        ) : events.length === 0 ? (
          <TileEmpty>Nothing scheduled.</TileEmpty>
        ) : (
          // Eight rather than six: the hero is wide enough to run this list in two
          // columns past six items (index.css, .tile-hero .rows), so the extra
          // width buys more of the day instead of more whitespace.
          <ul className="rows">
            {events.slice(0, 8).map((event) => (
              <li key={event.id} className="row" data-source="google_calendar">
                <span className="row-lead">{timeOf(event)}</span>
                <span className="row-main">{event.title}</span>
              </li>
            ))}
          </ul>
        )}
      </Tile>

      <Tile title="Inbox" count={captures.length} expandTo="/inbox" accent="personal_os" className="area-inbox">
        <CaptureBox
          onCaptured={(item) => {
            setCaptures((c) => [item, ...c]);
            setJustCaptured(item.id);
          }}
        />
        {loading ? (
          <TileSkeleton rows={3} />
        ) : errors.captures ? (
          <TileError onAction={load}>{errors.captures}</TileError>
        ) : captures.length === 0 ? (
          <TileEmpty>Nothing waiting.</TileEmpty>
        ) : (
          <ul className="rows">
            {captures.slice(0, 5).map((capture) => (
              <li
                key={capture.id}
                className={
                  capture.id === justCaptured ? 'row is-new' : 'row'
                }
                data-source="personal_os"
              >
                <span className="row-main">{capture.body}</span>
                <span className="row-trail">{relativeTime(capture.createdAt)}</span>
              </li>
            ))}
          </ul>
        )}
      </Tile>

      </div>

      {/* Everything else. Masonry, because these three have genuinely variable
          heights — Notes may have one item and Tasks six — and a fixed grid could
          only leave gaps or stretch tiles full of empty space. */}
      <div className="bento">

      {/* count is the SELECTED list, not every open task. It used to read "23"
          above six rows from one list — both numbers true, the pair misleading. */}
      <Tile
        title="Tasks"
        count={shownTasks.length}
        expandTo="/tasks"
        accent="google_tasks"
        className="area-tasks"
      >
        {loading ? (
          <TileSkeleton rows={5} />
        ) : errors.tasks ? (
          <TileError
            onAction={errors.tasks.includes('disconnected') ? startGoogleConnect : load}
            actionLabel={errors.tasks.includes('disconnected') ? 'reconnect' : 'retry'}
          >
            {errors.tasks}
          </TileError>
        ) : (
          <TaskPanel
            lists={taskLists}
            tasks={open}
            selected={activeList}
            onSelect={chooseList}
            onCreateList={addList}
            pending={pending}
            onComplete={complete}
            onUndo={undoComplete}
            onAdd={add}
          />
        )}
      </Tile>

      <Tile title="Projects" count={repos.length} expandTo="/projects" accent="github" className="area-projects">
        {loading ? (
          <TileSkeleton rows={5} />
        ) : errors.repos ? (
          <TileError onAction={load}>{errors.repos}</TileError>
        ) : repos.length === 0 ? (
          <TileEmpty>No repositories.</TileEmpty>
        ) : (
          <ul className="rows">
            {repos.slice(0, 8).map((repo) => (
              <li key={repo.id} className="row" data-source="github">
                <span className="row-main">{repo.name}</span>
                <span className="row-trail">{relativeTime(repo.pushedAt)}</span>
              </li>
            ))}
          </ul>
        )}
      </Tile>

      <Tile
        title="Notes"
        count={recentNotes.length}
        expandTo="/knowledge"
        accent="obsidian"
        className="area-notes"
      >
        {loading ? (
          <TileSkeleton rows={5} />
        ) : errors.notes ? (
          <TileError onAction={load}>{errors.notes}</TileError>
        ) : recentNotes.length === 0 ? (
          <TileEmpty>Nothing edited this week.</TileEmpty>
        ) : (
          <ul className="rows">
            {recentNotes.slice(0, 8).map((note) => (
              <li key={note.id} className="row" data-source="obsidian">
                <Link to={`/notes/${note.id}`} className="row-main row-link">
                  {note.title}
                </Link>
                <span className="row-trail">{relativeTime(note.modifiedAt)}</span>
              </li>
            ))}
          </ul>
        )}
        </Tile>
      </div>
    </>
  );
}


/**
 * One list at a time, chosen by the user.
 *
 * Stacking every list was worse in two ways. It was cramped — seven groups in a
 * third of the grid — and it only rendered lists that already had tasks, so an
 * empty "To Read" was invisible and therefore unaddable. That is the exact
 * problem list_task_lists() was added to solve, reintroduced in the UI.
 *
 * A select rather than chips: seven lists, and "Etsy Website Changes" alone would
 * take the tile's full width.
 *
 * The add row targets whatever is selected, so it stays unambiguous — the choice
 * is made once, above, rather than implied by the position of an input.
 */
function TaskPanel({
  lists,
  tasks,
  selected,
  pending,
  onSelect,
  onCreateList,
  onComplete,
  onUndo,
  onAdd,
}: {
  lists: TaskList[];
  tasks: Task[];
  /** Owned by Home, because the tile's header count has to agree with what is
      rendered here — two sources of truth for "which list" was exactly how the
      header came to say 23 above six rows. */
  selected: string | null;
  /** Ticked off, write not yet sent. Shown struck through with an undo. */
  pending: Task[];
  onSelect: (id: string) => void;
  onCreateList: (name: string) => Promise<void>;
  onComplete: (task: Task) => void;
  onUndo: (task: Task) => void;
  onAdd: (listId: string, title: string) => Promise<void>;
}) {
  // A select option is not an action, so choosing "new list" does not create
  // one — it swaps the select for an input. Naming it is the action.
  const [naming, setNaming] = useState(false);

  const [first] = lists;
  if (!first) return <TileEmpty>No task lists.</TileEmpty>;

  const active = lists.find((l) => l.id === selected) ?? first;
  const items = tasks.filter((t) => t.listId === active.id);
  const pendingHere = pending.filter((t) => t.listId === active.id);

  return (
    <div className="task-panel">
      {naming ? (
        <NewList onCreate={onCreateList} onCancel={() => setNaming(false)} />
      ) : (
        <select
          className="task-select"
          value={active.id}
          onChange={(e) =>
            e.target.value === NEW_LIST ? setNaming(true) : onSelect(e.target.value)
          }
          aria-label="Which task list to show"
        >
          {lists.map((list) => (
            <option key={list.id} value={list.id}>
              {list.name}
            </option>
          ))}
          <option value={NEW_LIST}>+ new list…</option>
        </select>
      )}

      {/* Ticked off but not yet written. Kept on screen rather than vanishing,
          because a row that disappears leaves you unsure which one you hit. */}
      {pendingHere.length > 0 && (
        <ul className="rows">
          {pendingHere.map((task) => (
            <li key={task.id} className="row is-done" data-source="google_tasks">
              <input type="checkbox" className="row-check" checked readOnly />
              <span className="row-main">{task.title}</span>
              <button type="button" className="row-undo" onClick={() => onUndo(task)}>
                undo
              </button>
            </li>
          ))}
        </ul>
      )}

      {items.length === 0 && pendingHere.length === 0 ? (
        <TileEmpty>Nothing open in this list.</TileEmpty>
      ) : items.length === 0 ? null : (
        <ul className="rows">
          {items.slice(0, 10).map((task) => (
            <li key={task.id} className="row" data-source="google_tasks">
              <input
                type="checkbox"
                className="row-check"
                checked={false}
                onChange={() => onComplete(task)}
                aria-label={`Complete ${task.title}`}
              />
              <span className="row-main">{task.title}</span>
            </li>
          ))}
        </ul>
      )}

      <AddTask listId={active.id} onAdd={onAdd} />
    </div>
  );
}

/**
 * Naming a new list.
 *
 * Escape cancels and blur cancels, so there is no way to get stuck in this state
 * — the select is the only way back and it is one keystroke away. Enter on an
 * empty field cancels too, rather than creating a list called "".
 */
function NewList({
  onCreate,
  onCancel,
}: {
  onCreate: (name: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);

  async function submit() {
    const trimmed = name.trim();
    if (!trimmed) return onCancel();
    if (saving) return;

    setSaving(true);
    try {
      await onCreate(trimmed);
      onCancel();
    } finally {
      setSaving(false);
    }
  }

  return (
    <input
      className="task-select task-newlist"
      value={name}
      onChange={(e) => setName(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') void submit();
        if (e.key === 'Escape') onCancel();
      }}
      onBlur={() => !saving && onCancel()}
      placeholder="list name…"
      disabled={saving}
      aria-label="Name the new list"
      autoFocus
    />
  );
}

function AddTask({
  listId,
  onAdd,
}: {
  listId: string;
  onAdd: (listId: string, title: string) => Promise<void>;
}) {
  const [title, setTitle] = useState('');
  const [saving, setSaving] = useState(false);

  async function submit() {
    const trimmed = title.trim();
    if (!trimmed || saving) return;

    setSaving(true);
    try {
      await onAdd(listId, trimmed);
      setTitle('');
    } finally {
      setSaving(false);
    }
  }

  return (
    <input
      className="task-add"
      value={title}
      onChange={(e) => setTitle(e.target.value)}
      onKeyDown={(e) => e.key === 'Enter' && void submit()}
      placeholder="+ add"
      disabled={saving}
      aria-label="Add a task to the selected list"
    />
  );
}
