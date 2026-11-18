import { describe, expect, it } from "vitest";
import { CELL_COUNT, cellCenter, cellDistance, cellNeighbours, cellRowCol, fitGrid } from "./grid";

const g = { ox: 0, oy: 0, cw: 86, ch: 43 };

describe("grid", () => {
  it("has 560 cells", () => expect(CELL_COUNT).toBe(560));
  it("row/col", () => {
    expect(cellRowCol(0)).toEqual({ row: 0, col: 0 });
    expect(cellRowCol(15)).toEqual({ row: 1, col: 1 });
    expect(cellRowCol(559)).toEqual({ row: 39, col: 13 });
  });
  it("centre follows §1 formula with odd-row stagger", () => {
    expect(cellCenter(0, g)).toEqual({ x: 0, y: 0 });
    expect(cellCenter(14, g)).toEqual({ x: 43, y: 21.5 });
    expect(cellCenter(28, g)).toEqual({ x: 0, y: 43 });
  });
  it("neighbours are at distance 1", () => {
    for (const n of cellNeighbours(300)) expect(cellDistance(300, n)).toBe(1);
    expect(cellNeighbours(300)).toHaveLength(4);
    expect(cellNeighbours(0).length).toBeLessThan(4);
  });
  it("fitGrid fits inside the box", () => {
    const f = fitGrid(400, 300);
    const last = cellCenter(559, f);
    expect(last.x + f.cw / 2).toBeLessThanOrEqual(400.001);
    expect(last.y + f.ch / 2).toBeLessThanOrEqual(300.001);
    expect(cellCenter(0, f).x - f.cw / 2).toBeGreaterThanOrEqual(-0.001);
  });
});
