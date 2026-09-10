import { useEffect, useState } from 'react';
import {
  completeTask,
  listTasks,
  needsReconnect,
  startGoogleConnect,
  type Task,
} from '../api/google';
import { SourceBadge } from '../components/SourceBadge';

/**
 * Google Tasks.
 *
 * Subtasks are rendered one level deep because that is all Google Tasks supports.
 * The domain type models a parent id rather than nesting, so a provider allowing
 * deeper hierarchies would not require this interface to change — only this view.
 */
export function Tasks() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [reconnect, setReconnect] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    listTasks()
      .then((items) => active && setTasks(items))
      .catch((err) => {
        if (!active) return;
        if (needsReconnect(err)) setReconnect(true);
        else setError(err?.message ?? 'Could not load tasks.');
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  async function complete(task: Task) {
    const previous = tasks;
    setTasks((current) => current.filter((t) => t.id !== task.id));
    try {
      await completeTask(task.id);
    } catch (err) {
      setTasks(previous);
      if (needsReconnect(err)) setReconnect(true);
      else setError('Could not complete that task.');
    }
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

  const parents = tasks.filter((t) => !t.parentId);
  const childrenOf = (id: string) => tasks.filter((t) => t.parentId === id);

  return (
    <>
      <header className="page-header">
        <h1>Tasks</h1>
        <p className="page-subtitle">
          {loading ? 'Loading…' : `${tasks.length} open`}
        </p>
      </header>

      {error && <p className="banner is-error">{error}</p>}

      {!loading && tasks.length === 0 && !error && (
        <div className="empty">
          <p className="empty-title">Nothing open</p>
          <p className="empty-detail">Tasks from Google Tasks appear here.</p>
        </div>
      )}

      <ul className="list">
        {parents.map((task) => (
          <li key={task.id} className="list-item">
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
              <SourceBadge source="google_tasks" />
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
    </>
  );
}
