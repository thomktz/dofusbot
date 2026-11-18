// Dofus 560-cell isometric map geometry (protocol §1).
export const GRID_COLS = 14;
export const GRID_ROWS = 40;
export const CELL_COUNT = GRID_COLS * GRID_ROWS; // 560

export interface GridCalib { ox: number; oy: number; cw: number; ch: number }

export function cellRowCol(id: number): { row: number; col: number } {
  return { row: Math.floor(id / GRID_COLS), col: id % GRID_COLS };
}

/** Cell centre: x = ox + col*cw + (row%2)*cw/2, y = oy + row*ch/2. */
export function cellCenter(id: number, g: GridCalib): { x: number; y: number } {
  const { row, col } = cellRowCol(id);
  return { x: g.ox + col * g.cw + (row % 2) * g.cw / 2, y: g.oy + row * g.ch / 2 };
}

/** SVG points string for the diamond of a cell. */
export function cellDiamond(id: number, g: GridCalib, inset = 0): string {
  const { x, y } = cellCenter(id, g);
  const hw = g.cw / 2 - inset, hh = g.ch / 2 - inset / 2;
  return `${x},${y - hh} ${x + hw},${y} ${x},${y + hh} ${x - hw},${y}`;
}

/** Fit the 14×40 iso grid (2:1 diamonds) into a box, centred. Total extent: 14.5 cw × 20.5 ch. */
export function fitGrid(width: number, height: number, pad = 0): GridCalib {
  const cw = Math.min((width - 2 * pad) / 14.5, ((height - 2 * pad) / 20.5) * 2);
  const ch = cw / 2;
  const gw = 14.5 * cw, gh = 20.5 * ch;
  return { cw, ch, ox: (width - gw) / 2 + cw / 2, oy: (height - gh) / 2 + ch / 2 };
}

/** Manhattan-ish distance on the iso grid, in movement steps (diagonal coordinates). */
export function cellDistance(a: number, b: number): number {
  const p = toDiag(a), q = toDiag(b);
  return Math.abs(p.u - q.u) + Math.abs(p.v - q.v);
}
function toDiag(id: number) {
  const { row, col } = cellRowCol(id);
  const x2 = col * 2 + (row % 2);
  return { u: (x2 + row) / 2, v: (x2 - row) / 2 };
}

/** Neighbouring cells (4 iso directions) that exist on the map. */
export function cellNeighbours(id: number): number[] {
  const { row } = cellRowCol(id);
  const odd = row % 2;
  const cand = [
    [-1, odd ? 0 : -1], [-1, odd ? 1 : 0],
    [1, odd ? 0 : -1], [1, odd ? 1 : 0],
  ];
  const out: number[] = [];
  const col = id % GRID_COLS;
  for (const [dr, dc] of cand) {
    const r = row + dr, c = col + dc;
    if (r >= 0 && r < GRID_ROWS && c >= 0 && c < GRID_COLS) out.push(r * GRID_COLS + c);
  }
  return out;
}
