/** Relative time formatting, using the platform rather than a date library. */

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 60 * 60 * 24 * 365],
  ['month', 60 * 60 * 24 * 30],
  ['day', 60 * 60 * 24],
  ['hour', 60 * 60],
  ['minute', 60],
];

const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });

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
