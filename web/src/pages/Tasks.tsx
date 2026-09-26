import { useEffect, useState } from 'react';
import {
  completeTask,
  createTask,
  listTaskLists,
  listTasks,
  needsReconnect,
  startGoogleConnect,
  type Task,
  type TaskList,
} from '../api/google';
import { ListSkeleton } from '../components/Tile';
import {
  publishTaskChange,
  selectTaskList,
  useSelectedTaskList,
  useTaskChanges,
} from '../lib/tasks';

/**
 * Google Tasks, expanded.
 *
 * This is the Tasks tile with the room it did not have, so it owes you two things
 * the tile cannot fit: every list at once, and the list names.
 *
 * It used to show the flat contents of every list with no way to filter and no
 * indication of which list anything belonged to — and it ignored the list you had
 * chosen on the dashboard, which is the one piece of context you brought with you.
 * Expanding something should show you more of it, not something else.
 *
 * The selection is shared (lib/tasks), so choosing a list here changes the tile
 * behind this overlay too. That is the point: it is the same choice, not a copy.
 *
 * Subtasks are rendered one level deep because that is all Google Tasks supports.
 * The domain type models a parent id rather than nesting, so a provider allowing
 * deeper hierarchies would not require this interface to change — only this view.
 */
export function Tasks() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [lists, setLists] = useState<TaskList[]>([]);
  const [loading, setLoading] = useState(true);
  const [reconnect, setReconnect] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Shared with the dashboard tile. Null until the lists arrive, at which point
  // the first one stands in — same fallback the tile uses, so they cannot disagree.
  const stored = useSelectedTaskList();
  // "All lists" is a way of looking at this page, not a list you picked, so it
  // stays local and never overwrites the shared choice.
  const [showingAll, setShowingAll] = useState(false);

  useEffect(() => {
    let active = true;

    // allSettled: a failure to enumerate the lists must not blank the tasks. An
    // empty list is also the one case listTasks cannot describe — a list with
    // nothing in it does not appear in its results at all.
    Promise.allSettled([listTasks(), listTaskLists()])
      .then(([t, l]) => {
        if (!active) return;

        if (t.status === 'fulfilled') setTasks(t.value);
        else if (needsReconnect(t.reason)) setReconnect(true);
        else setError(t.reason?.message ?? 'Could not load tasks.');

        if (l.status === 'fulfilled') setLists(l.value);
      })
      .finally(() => active && setLoading(false));

    return () => {
      active = false;
    };
  }, []);

  // Changes made on the dashboard while this is open, applied here rather than
  // refetched. Idempotent, because this also hears its own writes.
  useTaskChanges((change) => {
    if (change.kind === 'completed') {
      setTasks((current) => current.filter((t) => t.id !== change.task.id));
    } else {
      setTasks((current) =>
        current.some((t) => t.id === change.task.id)
          ? current
          : [change.task, ...current],
      );
    }
  });

  async function complete(task: Task) {
    publishTaskChange({ kind: 'completed', task });

    try {
      await completeTask(task.id);
    } catch (err) {
      // Still open in Google, so putting it back is the truthful state rather
      // than a rollback of something that happened.
      //
      // One task restored, not a whole snapshot. This used to keep `previous`
      // and reinstate the entire array, which would also silently undo anything
      // that arrived while the request was in flight.
      publishTaskChange({ kind: 'restored', task });
      if (needsReconnect(err)) setReconnect(true);
      else setError('Could not complete that task.');
    }
  }

  async function add(listId: string, title: string) {
    const created = await createTask(listId, title);
    // One write path: publish only, and let this component's own subscriber put
    // it into state. Doing both worked here purely because the order happened to
    // be the safe one — which is the kind of correctness that breaks the next
    // time two lines get swapped. It did break, in Home.
    publishTaskChange({ kind: 'added', task: created });
  }

  if (reconnect) {
    return (
      <>
        <header className="page-header">
          <h1>Tasks</h1>
        </header>
        <div className="empty">
          <p className="empty-title">Reconnect to Google</p>
          <p className="empty-detail">
            The connection expired. Unverified apps get 7-day refresh tokens, so this
            is expected rather than a fault.
          </p>
          <button type="button" className="button" onClick={startGoogleConnect}>
            Reconnect
          </button>
        </div>
      </>
    );
  }

  // Same fallback chain as the tile: the stored choice, then the first list. A
  // stored id whose list has since been deleted lands on the first one too.
  const active = lists.find((l) => l.id === stored) ?? lists[0] ?? null;
  const scope = showingAll || !active ? null : active;

  const visible = scope ? tasks.filter((t) => t.listId === scope.id) : tasks;
  const parents = visible.filter((t) => !t.parentId);
  // Children are matched against every task, not the filtered set, so a subtask
  // never disappears because of how its parent's list was filtered.
  const childrenOf = (id: string) => tasks.filter((t) => t.parentId === id);

  return (
    <>
      <header className="page-header">
        <h1>Tasks</h1>
        <p className="page-subtitle">
          {loading
            ? 'Loading…'
            : scope
              ? `${visible.length} open in ${scope.name}`
              : `${visible.length} open across ${lists.length || 1} lists`}
        </p>
      </header>

      {error && <p className="banner is-error">{error}</p>}

      {/* Chips rather than the tile's dropdown. A select is right in a tile, where
          seven list names would take the full width; here there is room to show
          them all, and seeing which lists exist is half of what this view is for. */}
      {lists.length > 0 && (
        <div className="list-chips" role="group" aria-label="Which task list to show">
          {lists.map((list) => {
            const count = tasks.filter((t) => t.listId === list.id && !t.parentId).length;
            return (
              <button
                key={list.id}
                type="button"
                className={
                  scope?.id === list.id ? 'chip is-selected' : 'chip'
                }
                aria-pressed={scope?.id === list.id}
                onClick={() => {
                  setShowingAll(false);
                  selectTaskList(list.id);
                }}
              >
                {list.name}
                {count > 0 && <span className="chip-count">{count}</span>}
              </button>
            );
          })}

          <button
            type="button"
            className={showingAll ? 'chip is-selected' : 'chip'}
            aria-pressed={showingAll}
            onClick={() => setShowingAll(true)}
          >
            All lists
          </button>
        </div>
      )}

      {!loading && visible.length === 0 && !error && (
        <div className="empty">
          <p className="empty-title">
            {scope ? `Nothing open in ${scope.name}` : 'Nothing open'}
          </p>
          <p className="empty-detail">
            {scope
              ? 'Add one below, or pick another list.'
              : 'Tasks from Google Tasks appear here.'}
          </p>
        </div>
      )}

      {loading && <ListSkeleton rows={5} />}

      <ul className="list">
        {parents.map((task) => (
          <li key={task.id} className="list-item" data-source="google_tasks">
            <div className="task-row">
              <button
                type="button"
                className="checkbox"
                aria-label={`Complete ${task.title}`}
                onClick={() => void complete(task)}
              />
              <div className="task-main">
                <span className="list-item-title">{task.title}</span>
                {task.due && (
                  <time className="task-due" dateTime={task.due}>
                    {new Date(task.due).toLocaleDateString(undefined, {
                      day: 'numeric',
                      month: 'short',
                    })}
                  </time>
                )}
              </div>

              {/* The list name, and only when it tells you something. Every row
                  used to carry a "google_tasks" badge, which on a page of nothing
                  but Google tasks is seven repetitions of a fact you already have.
                  Which list a task is in is the fact worth showing — and only when
                  more than one list is on screen. */}
              {!scope && task.listName && (
                <span className="task-list-name">{task.listName}</span>
              )}
            </div>

            {childrenOf(task.id).map((child) => (
              <div key={child.id} className="task-row is-subtask">
                <button
                  type="button"
                  className="checkbox"
                  aria-label={`Complete ${child.title}`}
                  onClick={() => void complete(child)}
                />
                <div className="task-main">
                  <span>{child.title}</span>
                </div>
              </div>
            ))}
          </li>
        ))}
      </ul>

      {/* Only with a list chosen. createTask refuses to default a list id, for the
          good reason that a book landing in a work backlog is easy not to notice —
          and "All lists" is precisely the state with no answer to which list. */}
      {scope && !loading && <AddTask listId={scope.id} listName={scope.name} onAdd={add} />}
    </>
  );
}

function AddTask({
  listId,
  listName,
  onAdd,
}: {
  listId: string;
  listName: string;
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
      className="task-add-row"
      value={title}
      onChange={(e) => setTitle(e.target.value)}
      onKeyDown={(e) => e.key === 'Enter' && void submit()}
      placeholder={`+ add to ${listName}`}
      disabled={saving}
      aria-label={`Add a task to ${listName}`}
    />
  );
}
