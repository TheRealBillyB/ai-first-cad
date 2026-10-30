// Screen-space edge picking for the workspace view.
// A straight edge is two samples. The click is measured to the segment
// between samples, so any point along the edge can hit.

function edgeSegmentDistance(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const length2 = dx * dx + dy * dy;
  if (length2 < 1e-18) return Math.hypot(px - ax, py - ay);
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / length2));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

function edgePickDistance(x, y, points, project) {
  if (!points || !points.length) return Infinity;
  let best = Infinity;
  let previous = null;
  for (let index = 0; index < points.length; index += 1) {
    const screen = project ? project(points[index]) : points[index];
    if (!screen) {
      previous = null;
      continue;
    }
    const distance = previous
      ? edgeSegmentDistance(x, y, previous[0], previous[1], screen[0], screen[1])
      : Math.hypot(screen[0] - x, screen[1] - y);
    if (distance < best) best = distance;
    previous = screen;
  }
  return best;
}

function edgePickHit(x, y, edges, project, limit) {
  const tolerance = Number.isFinite(limit) ? limit : 18;
  let best = null;
  let bestD = tolerance;
  (edges || []).forEach((edge) => {
    if (!edge) return;
    const distance = edgePickDistance(x, y, edge.points, project);
    if (distance < bestD) {
      bestD = distance;
      best = { ref: edge.ref, part: edge.part || null };
    }
  });
  return best;
}
