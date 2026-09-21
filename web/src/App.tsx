import { useCallback, useEffect, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { TopBar } from './components/TopBar';
import { Home } from './pages/Home';

/**
 * How long the overlay animates out for. Must match --dur-exit in index.css.
 *
 * It is a safety net rather than the mechanism: the animationend event is what
 * normally triggers the navigation, and this fires only if that event never
 * arrives — a background tab, or a browser that dropped the animation. Slightly
 * longer than the CSS duration so the two do not race.
 */
const EXIT_FALLBACK_MS = 260;

/**
 * Layout shell — the grid, with everything else layered over it.
 *
 * The grid is rendered unconditionally rather than as a route, so every other
 * page becomes an overlay on top of it. That is what makes expanding a tile feel
 * like opening something rather than navigating away, and it means the existing
 * pages (Inbox, Tasks, Projects, NoteDetail) are reused untouched as their own
 * expanded views.
 *
 * Routing still does the work, which is the point: the URL changes, so browser
 * back closes the overlay, refresh reopens it, and any view is linkable. An
 * overlay whose only exit is the right X is the dead end this avoids.
 *
 * The cost is that deep-linking to /inbox also mounts the grid behind it, so its
 * tiles fetch too. On a local single-user tool that is a few milliseconds, bought
 * in exchange for closing being instant instead of a fresh page render.
 */
export function App() {
  const location = useLocation();
  const navigate = useNavigate();

  const isOverlay = location.pathname !== '/';

  /**
   * Closing, but not yet closed.
   *
   * React has no way to animate something out on its own: change the route and the
   * node is gone in the same frame, which is why opening a tile animated and
   * closing it was a hard cut. Nothing was wrong with the CSS — the element simply
   * stopped existing before any exit animation could run.
   *
   * So closing becomes two steps. This flag adds the class that animates the panel
   * away, and the navigation — the thing that actually unmounts it — waits until
   * that animation ends.
   */
  const [exiting, setExiting] = useState(false);

  /**
   * Close one layer, not all of them.
   *
   * It used to go straight to '/'. That loses your place on the way back out:
   * Notes tile -> /knowledge -> open a note -> Escape, and the list you were
   * browsing is gone. Stepping back returns you to it.
   *
   * The guard matters because history may not contain this app at all — open
   * /notes/abc in a fresh tab and going back leaves the site entirely. React
   * Router marks that entry `key: 'default'`, which is the only reliable signal
   * that nothing preceded it.
   */
  const navigateBack = useCallback(() => {
    setExiting(false);
    if (location.key === 'default') navigate('/');
    else navigate(-1);
  }, [location.key, navigate]);

  /**
   * Start closing.
   *
   * Reduced motion skips the animation rather than waiting out a duration that no
   * longer animates anything — otherwise asking the OS for less motion would buy
   * you a 140ms delay on every close, which is the opposite of what was asked for.
   *
   * The `exiting` guard makes a second press a no-op: Escape twice in quick
   * succession would otherwise queue two history pops and skip a layer.
   */
  const close = useCallback(() => {
    if (exiting) return;

    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      navigateBack();
      return;
    }
    setExiting(true);
  }, [exiting, navigateBack]);

  // The net, for when animationend never arrives — a backgrounded tab throttles
  // animations, and one that never ends would strand the overlay open forever.
  useEffect(() => {
    if (!exiting) return;
    const timer = setTimeout(navigateBack, EXIT_FALLBACK_MS);
    return () => clearTimeout(timer);
  }, [exiting, navigateBack]);

  useEffect(() => {
    if (!isOverlay) return;

    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') close();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOverlay, close]);

  return (
    <div className="app">
      <TopBar />

      <main className="main">
        <Home />
      </main>

      {isOverlay && (
        <div
          className={exiting ? 'overlay is-exiting' : 'overlay'}
          // Clicking the backdrop closes; clicking inside must not. Without the
          // stopPropagation below, selecting text in a note would close it.
          onClick={close}
          role="presentation"
          // The navigation rides the end of the animation rather than a timer, so
          // the two cannot drift apart when the CSS duration changes. Guarded by
          // name because the panel's own entrance animation also bubbles an
          // animationend up to this element, and closing on that one would make the
          // overlay shut itself 200ms after opening.
          onAnimationEnd={(event) => {
            if (exiting && event.animationName === 'overlay-out') navigateBack();
          }}
        >
          <div className="overlay-panel" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="overlay-close"
              onClick={close}
              aria-label="Close"
            >
              ×
            </button>
            <Outlet />
          </div>
        </div>
      )}
    </div>
  );
}
