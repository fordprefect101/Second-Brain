import type { ReactNode } from 'react';

export interface SegmentedOption<T extends string> {
  value: T;
  /** Read by screen readers and shown on hover — the only text these buttons have. */
  label: string;
  icon: ReactNode;
}

/**
 * A two-or-three state setting, with every state visible.
 *
 * Deliberately not a cycling button. A single button that advances through
 * system → light → dark is smaller, but you cannot tell what the next press will do
 * without pressing it, and with three states getting back to the one you wanted can
 * take two tries. A preference should be a choice you can see, not a sequence you
 * have to discover.
 *
 * aria-pressed rather than a radio group: these apply instantly rather than being
 * submitted, so they are toggle buttons, and role="group" with a label is what tells
 * a screen reader the three of them are one control.
 */
export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly SegmentedOption<T>[];
  value: T;
  onChange: (next: T) => void;
}) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className="seg-btn"
          aria-pressed={value === option.value}
          aria-label={option.label}
          title={option.label}
          onClick={() => onChange(option.value)}
        >
          {option.icon}
        </button>
      ))}
    </div>
  );
}

/**
 * The icons. Inline rather than a dependency, because five 14px glyphs do not
 * justify an icon package — and because stroke: currentColor is what lets the
 * selected one pick up the accent from CSS rather than being told about it here.
 */
function Glyph({ children }: { children: ReactNode }) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

export const icons = {
  sun: (
    <Glyph>
      <circle cx="8" cy="8" r="3" />
      <path d="M8 1.2v1.5M8 13.3v1.5M1.2 8h1.5M13.3 8h1.5M3.2 3.2l1.1 1.1M11.7 11.7l1.1 1.1M12.8 3.2l-1.1 1.1M4.3 11.7l-1.1 1.1" />
    </Glyph>
  ),

  moon: (
    <Glyph>
      <path d="M13.4 9.6A5.7 5.7 0 0 1 6.4 2.6 5.7 5.7 0 1 0 13.4 9.6z" />
    </Glyph>
  ),

  /* Half-filled circle: the conventional mark for "whatever the system says". */
  auto: (
    <Glyph>
      <circle cx="8" cy="8" r="5.6" />
      <path d="M8 2.4a5.6 5.6 0 0 1 0 11.2z" fill="currentColor" stroke="none" />
    </Glyph>
  ),

  /* Density as line spacing, which is literally what it changes. */
  cozy: (
    <Glyph>
      <path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11" />
    </Glyph>
  ),

  compact: (
    <Glyph>
      <path d="M2.5 3h11M2.5 5.5h11M2.5 8h11M2.5 10.5h11M2.5 13h11" />
    </Glyph>
  ),
};
