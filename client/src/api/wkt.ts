export type Ring = [number, number][];

/** Parse simple POLYGON WKT (exterior ring only — server geometry is simple). */
export function parsePolygonWkt(wkt: string): Ring {
  const m = wkt.match(/POLYGON\s*\(\(([^)]+)\)/i);
  if (!m) return [];
  return m[1].split(",").map((pair) => {
    const [x, y] = pair.trim().split(/\s+/).map(Number);
    return [x, y] as [number, number];
  });
}

export function rectToWkt(x0: number, y0: number, x1: number, y1: number): string {
  const [a, b] = [Math.min(x0, x1), Math.max(x0, x1)];
  const [c, d] = [Math.min(y0, y1), Math.max(y0, y1)];
  return `POLYGON ((${a} ${c}, ${b} ${c}, ${b} ${d}, ${a} ${d}, ${a} ${c}))`;
}

export const ringToPoints = (ring: Ring) =>
  ring.map(([x, y]) => `${x},${y}`).join(" ");

export const PT_TO_MM = 25.4 / 72;

export function polygonToWkt(pts: Ring): string {
  const ring = [...pts, pts[0]];
  return `POLYGON ((${ring.map(([x, y]) => `${x} ${y}`).join(", ")}))`;
}
