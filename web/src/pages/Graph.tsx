import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import ForceGraph2D from 'react-force-graph-2d';
import { getNoteGraph, type GraphNode, type NoteGraph } from '../api/graph';
import { ApiError } from '../api/client';

/**
 * Obsidian's graph view, in the browser: every note a dot, every [[link]] a line.
 *
 * The dots arrange themselves (d3-force, inside react-force-graph-2d): linked
 * notes pull together, all notes push apart. That is the right layout for this
 * graph — unlike the backlog's 3D project graph, where it is ruled out, because
 * there the third dimension has to mean something.
 *
 * Touch: drag to move, pinch to zoom, tap a dot to select it.
 */

// Drawn on a canvas, so colours come from the theme's CSS variables at draw time.
function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

type DrawNode = GraphNode & { x?: number; y?: number };

export function Graph() {
  const [graph, setGraph] = useState<NoteGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<DrawNode | null>(null);
  const [showUnlinked, setShowUnlinked] = useState(true);

  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    let active = true;
    getNoteGraph()
      .then((data) => active && setGraph(data))
      .catch((err) => {
        if (active) setError(err instanceof ApiError ? err.message : 'Could not load the graph.');
      });
    return () => {
      active = false;
    };
  }, []);

  // The canvas needs pixel sizes; follow the box as the screen turns or resizes.
  useEffect(() => {
    if (!box.current) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(box.current);
    return () => observer.disconnect();
  }, []);

  // A fresh copy for the library, which turns each line's ends into objects.
  const data = useMemo(() => {
    if (!graph) return { nodes: [], links: [] };
    const nodes = graph.nodes.filter((n) => showUnlinked || n.links > 0).map((n) => ({ ...n }));
    return { nodes, links: graph.edges.map((e) => ({ ...e })) };
  }, [graph, showUnlinked]);

  // The selected note and its direct neighbours stay bright; the rest fade.
  const neighbours = useMemo(() => {
    const ids = new Set<string>();
    if (!selected || !graph) return ids;
    ids.add(selected.id);
    for (const e of graph.edges) {
      if (e.source === selected.id) ids.add(e.target);
      if (e.target === selected.id) ids.add(e.source);
    }
    return ids;
  }, [selected, graph]);

  const linked = graph ? graph.nodes.filter((n) => n.links > 0).length : 0;

  return (
    <>
      <header className="page-header">
        <h1>Graph</h1>
        <p className="page-subtitle">
          {graph
            ? `${graph.nodes.length} notes · ${graph.edges.length} links · ${graph.nodes.length - linked} unlinked`
            : 'Reading vault…'}
        </p>
      </header>

      {error && <p className="banner is-error">{error}</p>}

      <label className="graph-toggle">
        <input
          type="checkbox"
          checked={showUnlinked}
          onChange={(e) => setShowUnlinked(e.target.checked)}
        />
        Show notes with no links
      </label>

      <div className="graph-box" ref={box}>
        {graph && size.width > 0 && (
          <ForceGraph2D
            graphData={data}
            width={size.width}
            height={size.height}
            nodeId="id"
            nodeVal={(n: DrawNode) => 1 + n.links}
            nodeRelSize={4}
            cooldownTicks={120}
            onNodeClick={(n: DrawNode) => setSelected(n)}
            onBackgroundClick={() => setSelected(null)}
            linkColor={(l: { source: DrawNode | string; target: DrawNode | string }) => {
              const end = (x: DrawNode | string) => (typeof x === 'string' ? x : x.id);
              const lit = !selected || (neighbours.has(end(l.source)) && neighbours.has(end(l.target)));
              return lit ? token('--border-strong') : token('--border');
            }}
            nodeCanvasObject={(n: DrawNode, ctx: CanvasRenderingContext2D, scale: number) => {
              const radius = 4 * Math.sqrt(1 + n.links);
              const faded = selected !== null && !neighbours.has(n.id);
              ctx.globalAlpha = faded ? 0.25 : 1;
              ctx.beginPath();
              ctx.arc(n.x ?? 0, n.y ?? 0, radius, 0, 2 * Math.PI);
              ctx.fillStyle = n.id === selected?.id ? token('--accent') : token('--text-muted');
              ctx.fill();
              // Names only where they can be read: big notes, the selection, or zoomed in.
              if (scale > 1.6 || n.links >= 8 || neighbours.has(n.id)) {
                ctx.font = `${12 / scale}px Inter, sans-serif`;
                ctx.textAlign = 'center';
                ctx.textBaseline = 'top';
                ctx.fillStyle = token('--text');
                ctx.fillText(n.title, n.x ?? 0, (n.y ?? 0) + radius + 2 / scale);
              }
              ctx.globalAlpha = 1;
            }}
            nodePointerAreaPaint={(n: DrawNode, colour: string, ctx: CanvasRenderingContext2D) => {
              // A generous tap target: a fingertip is wider than a small dot.
              ctx.fillStyle = colour;
              ctx.beginPath();
              ctx.arc(n.x ?? 0, n.y ?? 0, 4 * Math.sqrt(1 + n.links) + 6, 0, 2 * Math.PI);
              ctx.fill();
            }}
          />
        )}
      </div>

      {selected && (
        <div className="graph-card">
          <div>
            <p className="graph-card-title">{selected.title}</p>
            <p className="graph-card-meta">
              {selected.links} {selected.links === 1 ? 'link' : 'links'}
            </p>
          </div>
          <Link to={`/notes/${selected.id}`} className="graph-card-open">
            Open note
          </Link>
        </div>
      )}
    </>
  );
}
