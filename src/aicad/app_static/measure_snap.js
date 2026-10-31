// Screen-space snaps for the measure tool.
// A straight edge is the segment between samples. The click can land on a
// vertex, on a segment midpoint, or on the nearest point of that segment.

function measureSegmentClosest(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const length2 = dx * dx + dy * dy;
  if (length2 < 1e-18) return { t: 0, distance: Math.hypot(px - ax, py - ay) };
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / length2));
  return { t, distance: Math.hypot(px - (ax + t * dx), py - (ay + t * dy)) };
}

function measureSnapPoint(x, y, edges, project, options) {
  const limit = options && Number.isFinite(options.limit) ? options.limit : 14;
  const part = options ? options.part : null;
  let vertex = null;
  let vertexD = limit;
  let midpoint = null;
  let midpointD = limit;
  let along = null;
  let alongD = limit;
  const copy = (point) => (point && point.slice ? point.slice() : point);
  (edges || []).forEach((edge) => {
    if (!edge || !edge.points) return;
    if (part && edge.part && edge.part !== part) return;
    let previous = null;
    let previousWorld = null;
    edge.points.forEach((world) => {
      if (!world) {
        previous = null;
        previousWorld = null;
        return;
      }
      const screen = project ? project(world) : world;
      if (!screen) {
        previous = null;
        previousWorld = null;
        return;
      }
      const vertexDistance = Math.hypot(screen[0] - x, screen[1] - y);
      if (vertexDistance < vertexD) {
        vertexD = vertexDistance;
        vertex = copy(world);
      }
      if (previous && previousWorld) {
        const mid = previousWorld.map((value, index) => (value + world[index]) / 2);
        const midScreen = project ? project(mid) : mid;
        if (midScreen) {
          const midDistance = Math.hypot(midScreen[0] - x, midScreen[1] - y);
          if (midDistance < midpointD) {
            midpointD = midDistance;
            midpoint = mid;
          }
        }
        const hit = measureSegmentClosest(x, y, previous[0], previous[1], screen[0], screen[1]);
        if (hit.distance < alongD) {
          alongD = hit.distance;
          along = previousWorld.map((value, index) => value + hit.t * (world[index] - value));
        }
      }
      previous = screen;
      previousWorld = world;
    });
  });
  if (vertex && midpoint) return vertexD <= midpointD ? vertex : midpoint;
  if (vertex) return vertex;
  if (midpoint) return midpoint;
  return along;
}
