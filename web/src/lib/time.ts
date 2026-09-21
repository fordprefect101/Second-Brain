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

/**
 * 'Good morning' / 'Good evening' — the page's opening line.
 *
 * By the wall clock rather than by anything clever. The 2am case is separate on
 * purpose: "Good morning" at 2am is wrong in a way that reads as a bug, and a tool
 * this personal may as well notice.
 */
export function greeting(now = new Date()): string {
  const hour = now.getHours();
  if (hour < 5) return 'Still up';
  if (hour < 12) return 'Good morning';
  if (hour < 17) return 'Good afternoon';
  if (hour < 22) return 'Good evening';
  return 'Good night';
}

/**
 * 'Sunday, 21 September' — the dateline beside the greeting.
 *
 * No year: a Today view is never showing a different one, and it is one more number
 * on a surface that already has plenty. Locale-formatted, like every other date in
 * this file, so the day/month order follows the reader rather than this repo.
 */
export function todayLabel(now = new Date()): string {
  return now.toLocaleDateString(undefined, {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
  });
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
