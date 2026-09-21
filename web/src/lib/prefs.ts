import { useCallback, useState } from 'react';

/** Module-level, so switching theme twice quickly does not leave the class behind. */
let fadeTimer: ReturnType<typeof setTimeout>;

/**
 * A preference that lives on <html> as a data attribute.
 *
 * The attribute is the point. React does not style anything here — CSS does, via
 * `:root[data-theme='dark']` and `:root[data-density='compact']` — so the only job
 * of this hook is to keep one attribute and one localStorage key in step. No
 * context, no provider, no theme object threaded through components: the cascade
 * already broadcasts to every element on the page, which is exactly what a theme
 * needs to do.
 *
 * The initial value is read from the attribute rather than from storage, because
 * the inline script in index.html has already applied it before React mounted (see
 * the comment there — it is what stops a forced theme flashing on load). Reading
 * storage again here would be a second source of truth for the same fact.
 */
export function useRootPref<T extends string>(
  attribute: 'theme' | 'density',
  allowed: readonly T[],
  fallback: T,
): [T, (next: T) => void] {
  const key = `personal-os:${attribute}`;

  const [value, setValue] = useState<T>(() => {
    const applied = document.documentElement.dataset[attribute];
    return allowed.includes(applied as T) ? (applied as T) : fallback;
  });

  const choose = useCallback(
    (next: T) => {
      setValue(next);

      // Fade the repaint instead of snapping it. A whole screen of white going to
      // near-black in a single frame is the one moment in this app that is
      // genuinely unpleasant to look at.
      //
      // The class is temporary on purpose (see .is-theming in index.css): leaving a
      // colour transition on permanently would also make every hover fade, and a
      // hover has to be instant to feel like a response.
      if (attribute === 'theme' && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
        const root = document.documentElement;
        root.classList.add('is-theming');
        clearTimeout(fadeTimer);
        fadeTimer = setTimeout(() => root.classList.remove('is-theming'), 220);
      }

      document.documentElement.dataset[attribute] = next;
      try {
        localStorage.setItem(key, next);
      } catch {
        // Private browsing, or storage disabled. The choice still applies for this
        // session; it just will not be remembered. Losing the preference is a
        // smaller failure than throwing inside a click handler.
      }
    },
    [attribute, key],
  );

  return [value, choose];
}
