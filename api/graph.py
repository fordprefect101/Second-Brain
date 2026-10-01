"""The note graph: which notes link to which, the way Obsidian's graph view draws it.

Links are read from the note bodies on every request, like everything else about
notes (Plan.md §2: the vault is the source of truth, nothing here is a copy).

How a [[link]] finds its note, following Obsidian:
  [[Note]]              the note whose filename is Note.md, anywhere in the vault
  [[Folder/Note]]       the note at that path
  [[Note|shown text]]   the alias is display only
  [[Note#Heading]]      a link to part of a note is a link to the note
  ![[picture.png]]      an embedded file that is not a note: not part of the graph
Two notes with the same filename: the one with the shorter path wins, as in Obsidian.
A link to a note that does not exist yet is counted, not drawn.
"""

from __future__ import annotations

from dataclasses import dataclass

from api.providers.obsidian import WIKILINK_RE, ObsidianVaultProvider
from api.services import ProviderNote


@dataclass
class Graph:
    nodes: list[ProviderNote]
    edges: list[tuple[str, str]]  # (from provider_id, to provider_id), no repeats
    unresolved: int  # links to notes that do not exist


def link_targets(body: str) -> list[str]:
    """The note names a body links to, without aliases, headings or code."""
    targets = []
    for raw in WIKILINK_RE.findall(ObsidianVaultProvider._strip_code(body)):
        name = raw.split("|")[0].split("#")[0].split("^")[0].strip()
        if name.lower().endswith(".md"):
            name = name[:-3]
        elif "." in name.rsplit("/", 1)[-1]:
            continue  # picture.png, file.pdf: an attachment, not a note
        if name:
            targets.append(name)
    return targets


def _key(path_or_name: str) -> str:
    name = path_or_name[:-3] if path_or_name.lower().endswith(".md") else path_or_name
    return name.lower()


def build_graph(notes: list[ProviderNote]) -> Graph:
    by_path = {_key(n.provider_id): n.provider_id for n in notes}
    by_name: dict[str, str] = {}
    for n in sorted(notes, key=lambda n: (len(n.provider_id), n.provider_id)):
        by_name.setdefault(_key(n.provider_id.rsplit("/", 1)[-1]), n.provider_id)

    edges: dict[tuple[str, str], None] = {}  # a dict keeps first-seen order
    unresolved = 0
    for note in notes:
        for target in link_targets(note.body or ""):
            found = by_path.get(_key(target)) or by_name.get(_key(target.rsplit("/", 1)[-1]))
            if found is None:
                unresolved += 1
            elif found != note.provider_id:
                edges[(note.provider_id, found)] = None
    return Graph(nodes=notes, edges=list(edges), unresolved=unresolved)
