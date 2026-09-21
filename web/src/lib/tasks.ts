import { useEffect, useRef, useSyncExternalStore } from 'react';
import type { Task } from '../api/google';

/**
 * The one thing the tile and the expanded view have to agree about.
 *
 * Which list you are looking at was state inside Home, written to localStorage and
 * read by nobody else — so opening the expanded view dropped the choice on the
 * floor and showed every task in every list instead. Two views of the same thing,
 * disagreeing about what the thing is.
 *
 * localStorage on its own could not have fixed it. The `storage` event fires in
 * *other* tabs, not the one that wrote, and here both views are mounted at once —
 * the grid stays rendered behind the overlay. So the selection needs a real
 * subscription, and useSyncExternalStore is exactly the primitive for a value that
 * lives outside React and can change from either side.
 *
 * localStorage is still where it persists; this store is how it propagates.
 */

const KEY = 'personal-os:tasks:list';

function readStored(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

let selected: string | null = readStored();
const selectionListeners = new Set<() => void>();

export function selectTaskList(id: string): void {
  if (id === selected) return;
  selected = id;
  try {
    localStorage.setItem(KEY, id);
  } catch {
    // Private mode. The choice still applies for this session.
  }
  selectionListeners.forEach((notify) => notify());
}

export function useSelectedTaskList(): string | null {
  return useSyncExternalStore(
    (notify) => {
      selectionListeners.add(notify);
      return () => {
        selectionListeners.delete(notify);
      };
    },
    () => selected,
    () => selected,
  );
}

/**
 * Task writes, announced to whoever else is on screen.
 *
 * The second half of the same problem. Add a task in the expanded view and the
 * tile behind it kept its own copy of the list — so closing the overlay showed you
 * a Tasks tile that did not contain the task you had just added. Not stale in the
 * sense of needing a refresh: two components holding two arrays, one of them wrong.
 *
 * Deliberately not a cache or a query library. This publishes the change itself
 * rather than invalidating anything, so nobody refetches — which matters because
 * /tasks takes five seconds, and "correct after a five second reload" is not
 * correct enough to be worth the round trip. The real caching decision (Plan.md §4)
 * is untouched by this.
 *
 * Handlers must be idempotent: the publisher hears its own event, and dropping a
 * task by id or adding one that is already there both cost nothing the second time.
 */
export type TaskChange =
  | { kind: 'added'; task: Task }
  | { kind: 'completed'; task: Task }
  | { kind: 'restored'; task: Task };

const changeListeners = new Set<(change: TaskChange) => void>();

export function publishTaskChange(change: TaskChange): void {
  changeListeners.forEach((notify) => notify(change));
}

export function useTaskChanges(handler: (change: TaskChange) => void): void {
  // Held in a ref so a handler that closes over fresh state does not resubscribe
  // on every render.
  const latest = useRef(handler);
  latest.current = handler;

  useEffect(() => {
    const listener = (change: TaskChange) => latest.current(change);
    changeListeners.add(listener);
    return () => {
      changeListeners.delete(listener);
    };
  }, []);
}
