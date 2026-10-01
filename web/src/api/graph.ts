import { api } from './client';

export interface GraphNode {
  id: string;
  title: string;
  /** Lines touching this note, either direction. */
  links: number;
}

export interface GraphEdge {
  source: string;
  target: string;
}

export interface NoteGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** [[Links]] to notes that do not exist yet: counted, not drawn. */
  unresolved: number;
}

export function getNoteGraph(): Promise<NoteGraph> {
  return api.get<NoteGraph>('/notes/graph');
}
