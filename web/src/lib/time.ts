/** Relative time formatting, using the platform rather than a date library. */

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 60 * 60 * 24 * 365],
  ['month', 60 * 60 * 24 * 30],
  ['day', 60 * 60 * 24],
  ['hour', 60 * 60],
  ['minute', 60],
];

const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });

/**
 * Did this happen today, in the viewer's local timezone?
 *
 * Compares calendar dates, not elapsed hours. Something captured at 23:50 is not
 * "today" at 00:10 the next morning, even though it was twenty minutes ago — a
 * Today view has to agree with what the wall clock says.
 */
export function isToday(iso: string): boolean {
  const then = new Date(iso);
  const now = new Date();
  return (
    then.getFullYear() === now.getFullYear() &&
    then.getMonth() === now.getMonth() &&
    then.getDate() === now.getDate()
  );
}

/** Days since `iso`, by calendar date rather than elapsed time. */
export function daysAgo(iso: string): number {
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diff = startOfDay(new Date()).getTime() - startOfDay(new Date(iso)).getTime();
  return Math.round(diff / 86_400_000);
}

/** '2 hours ago', 'yesterday', 'just now'. */
export function relativeTime(iso: string): string {
  const seconds = (Date.parse(iso) - Date.now()) / 1000;
  const abs = Math.abs(seconds);

  for (const [unit, secondsInUnit] of UNITS) {
    if (abs >= secondsInUnit) {
      return rtf.format(Math.round(seconds / secondsInUnit), unit);
    }
  }
  return 'just now';
}
