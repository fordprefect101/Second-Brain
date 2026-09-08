import { SOURCES, type SourceId } from '../types';

/**
 * Names the service a piece of information came from.
 *
 * Plan.md §12 requires results to identify their source. Making that a component
 * from the start means the rule is applied by default rather than remembered later,
 * when there are four providers and inconsistent labelling.
 */
export function SourceBadge({ source }: { source: SourceId }) {
  return <span className="source-badge">{SOURCES[source].label}</span>;
}
