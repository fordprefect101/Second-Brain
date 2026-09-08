import { api } from './client';
import type { Note } from '../types';

export interface NoteDetail extends Note {
  body: string;
}

export function listNotes(limit = 200): Promise<Note[]> {
  return api.get<Note[]>(`/notes?limit=${limit}`);
}

export function getNote(id: string): Promise<NoteDetail> {
  return api.get<NoteDetail>(`/notes/${id}`);
}
