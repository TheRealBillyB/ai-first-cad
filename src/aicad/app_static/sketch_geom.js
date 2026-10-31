// Solved-sketch geometry for the workspace canvas.
// Distances are in sketch millimetres. A hit tolerance is a screen distance
// divided by the view scale, so a small sketch and a large one pick the same way.

function sketchSnapTolerance(scale) {
  if (!Number.isFinite(scale) || scale <= 0) return 8;
  return 8 / scale;
}

function sketchSegmentDistance(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const length2 = dx * dx + dy * dy;
  if (length2 < 1e-18) return Math.hypot(px - ax, py - ay);
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / length2));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

function sketchArcSweep(arc) {
  if (!arc || !arc.center || !arc.start || !arc.end) return null;
  const start = Math.atan2(arc.start.y - arc.center.y, arc.start.x - arc.center.x);
  const end = Math.atan2(arc.end.y - arc.center.y, arc.end.x - arc.center.x);
  let sweep = end - start;
  sweep %= Math.PI * 2;
  if (sweep < 0) sweep += Math.PI * 2;
  if (sweep < 1e-4) return null;
  const radius = Math.hypot(arc.start.x - arc.center.x, arc.start.y - arc.center.y);
  if (!Number.isFinite(radius) || radius <= 0) return null;
  return { start, sweep, radius };
}

function sketchAngleOnSweep(start, sweep, angle) {
  let delta = angle - start;
  delta %= Math.PI * 2;
  if (delta < 0) delta += Math.PI * 2;
  return delta <= sweep + 1e-6;
}

function sketchArcDistance(x, y, arc) {
  const swept = sketchArcSweep(arc);
  if (!swept) return Infinity;
  const angle = Math.atan2(y - arc.center.y, x - arc.center.x);
  if (sketchAngleOnSweep(swept.start, swept.sweep, angle)) {
    return Math.abs(Math.hypot(x - arc.center.x, y - arc.center.y) - swept.radius);
  }
  return Math.min(
    Math.hypot(x - arc.start.x, y - arc.start.y),
    Math.hypot(x - arc.end.x, y - arc.end.y),
  );
}

function sketchArcSamples(arc, count) {
  const swept = sketchArcSweep(arc);
  if (!swept) return [];
  const steps = Math.max(2, count || 24);
  const points = [];
  for (let index = 0; index <= steps; index += 1) {
    const angle = swept.start + swept.sweep * (index / steps);
    points.push([
      arc.center.x + swept.radius * Math.cos(angle),
      arc.center.y + swept.radius * Math.sin(angle),
    ]);
  }
  return points;
}

function sketchPointHit(geometry, x, y, tolerance) {
  const points = (geometry && geometry.points) || {};
  let best = null;
  let bestD = tolerance;
  Object.entries(points).forEach(([id, point]) => {
    if (!point) return;
    const distance = Math.hypot(point.x - x, point.y - y);
    if (distance < bestD) {
      best = id;
      bestD = distance;
    }
  });
  return best;
}

function sketchEntityHit(geometry, x, y, tolerance) {
  const geo = geometry || {};
  let best = null;
  let bestD = tolerance;
  const consider = (id, distance) => {
    if (distance < bestD) {
      best = id;
      bestD = distance;
    }
  };
  Object.entries(geo.points || {}).forEach(([id, point]) => {
    if (!point) return;
    consider(id, Math.hypot(point.x - x, point.y - y));
  });
  Object.entries(geo.lines || {}).forEach(([id, line]) => {
    if (!line || !line.start || !line.end) return;
    consider(id, sketchSegmentDistance(x, y, line.start.x, line.start.y, line.end.x, line.end.y));
  });
  Object.entries(geo.circles || {}).forEach(([id, circle]) => {
    if (!circle || !circle.center || !Number.isFinite(circle.radius)) return;
    const radial = Math.hypot(x - circle.center.x, y - circle.center.y);
    consider(id, Math.abs(radial - circle.radius));
  });
  Object.entries(geo.arcs || {}).forEach(([id, arc]) => {
    consider(id, sketchArcDistance(x, y, arc));
  });
  Object.entries(geo.splines || {}).forEach(([id, spline]) => {
    const samples = (spline && spline.samples) || [];
    let bestOn = Infinity;
    for (let index = 1; index < samples.length; index += 1) {
      const previous = samples[index - 1];
      const current = samples[index];
      if (!previous || !current) continue;
      bestOn = Math.min(
        bestOn,
        sketchSegmentDistance(x, y, previous.x, previous.y, current.x, current.y),
      );
    }
    consider(id, bestOn);
  });
  return best;
}

function sketchSelectionAfter(current, hit, shiftKey) {
  const selected = Array.isArray(current) ? current.filter((id) => typeof id === "string") : [];
  if (!hit) return [];
  if (shiftKey) {
    return selected.includes(hit) ? selected.filter((id) => id !== hit) : selected.concat([hit]);
  }
  if (selected.length === 1 && selected[0] !== hit) return [selected[0], hit];
  return [hit];
}

function sketchSolvedPoint(spec, geometry, id) {
  const points = geometry && geometry.points;
  const point = points && points[id];
  if (point && Number.isFinite(Number(point.x)) && Number.isFinite(Number(point.y))) {
    return [Number(point.x), Number(point.y)];
  }
  const entities = (spec && spec.entities) || [];
  const entity = entities.find((item) => item && item.id === id);
  if (entity && Array.isArray(entity.at) && entity.at.length >= 2) {
    const x = Number(entity.at[0]);
    const y = Number(entity.at[1]);
    if (Number.isFinite(x) && Number.isFinite(y)) return [x, y];
  }
  return [0, 0];
}

function sketchProfile(spec) {
  const entities = (spec && spec.entities) || [];
  const edges = entities.filter((entity) =>
    entity && (entity.type === "line" || entity.type === "arc") && !entity.construction
  );
  let best = [];
  let bestClosed = false;
  edges.forEach((edge, index) => {
    const chain = walkSketchChain(edges, index);
    if (chain.length < 3) return;
    const closed = sketchChainCloses(edges, chain);
    if (closed) {
      if (!bestClosed || chain.length > best.length) {
        best = chain;
        bestClosed = true;
      }
      return;
    }
    if (!bestClosed && chain.length > best.length) best = chain;
  });
  if (!bestClosed) {
    const spline = entities.find((entity) =>
      entity && entity.type === "spline" && entity.closed && !entity.construction
      && Array.isArray(entity.points) && entity.points.length >= 3
    );
    if (spline) return spline.points.slice();
  }
  return best;
}

function walkSketchChain(edges, startIndex) {
  const first = edges[startIndex];
  const chain = [first.start, first.end];
  const used = new Set([first.id]);
  let cursor = first.end;
  while (used.size < edges.length) {
    const next = edges.find((edge) => !used.has(edge.id) && (edge.start === cursor || edge.end === cursor));
    if (!next) break;
    used.add(next.id);
    cursor = next.start === cursor ? next.end : next.start;
    if (cursor === chain[0]) break;
    chain.push(cursor);
  }
  return chain;
}

function sketchChainCloses(edges, chain) {
  if (!chain || chain.length < 3) return false;
  const start = chain[0];
  const end = chain[chain.length - 1];
  return edges.some((edge) =>
    (edge.start === end && edge.end === start) || (edge.start === start && edge.end === end)
  );
}

function sketchStepName(steps, base) {
  const used = new Set((steps || []).map((step) => step && step.name).filter((name) => typeof name === "string"));
  if (!used.has(base)) return base;
  let index = 2;
  while (used.has(base + index)) index += 1;
  return base + index;
}

function sketchSolidSteps(op, spec, height, previousSteps) {
  const entities = (spec && spec.entities) || [];
  const circles = entities
    .filter((entity) => entity && entity.type === "circle" && !entity.construction)
    .map((entity) => entity.id);
  const loop = sketchProfile(spec);
  const h = Number(height);
  if (op === "revolve") {
    if (loop.length < 3) return { error: "A revolve needs a profile of at least three points." };
    return { steps: [{ op: "revolve", points: loop, axis: "y", name: "revolve" }] };
  }
  if (op === "cut") {
    const prior = Array.isArray(previousSteps) ? previousSteps.map((step) => Object.assign({}, step)) : [];
    const hasBase = prior.some((step) => step && (step.op === "extrude" || step.op === "revolve" || step.op === "gusset"));
    if (!hasBase) return { error: "A cut needs a solid. Extrude or revolve a profile first." };
    let cut;
    if (circles.length) cut = { op: "cut", circle: circles[0], height: h, name: sketchStepName(prior, "cut") };
    else if (loop.length >= 3) cut = { op: "cut", points: loop.slice(), height: h, name: sketchStepName(prior, "cut") };
    else return { error: "A cut needs a circle or a profile of at least three points." };
    return { steps: prior.concat([cut]) };
  }
  if (loop.length >= 3) {
    const steps = [{ op: "extrude", points: loop, height: h, name: "extrude" }];
    circles.forEach((circle) => {
      steps.push({ op: "cut", circle, height: h + 2, z: -1, name: "cut_" + circle });
    });
    return { steps };
  }
  if (circles.length) return { steps: [{ op: "extrude", circle: circles[0], height: h, name: "extrude" }] };
  return { error: "Extrude needs a profile of at least three points, or a circle." };
}

function sketchPendingPoint(sketch, pointId) {
  if (!sketch || typeof pointId !== "string" || pointId === "") return null;
  const entities = (sketch.spec && sketch.spec.entities) || [];
  const entity = entities.find((item) => item && item.id === pointId && item.type === "point");
  if (!entity || !Array.isArray(entity.at) || entity.at.length < 2) return null;
  const x = Number(entity.at[0]);
  const y = Number(entity.at[1]);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return { id: pointId, at: [x, y] };
}

function sketchWithPendingPoint(sketch, pending) {
  if (!sketch || !pending) return sketch;
  const spec = sketch.spec || {};
  const entities = Array.isArray(spec.entities) ? spec.entities.slice() : [];
  const present = entities.some((item) => item && item.id === pending.id);
  if (!present) entities.push({ id: pending.id, type: "point", at: [pending.at[0], pending.at[1]] });
  const geometry = sketch.geometry ? { ...sketch.geometry } : {};
  const points = { ...(geometry.points || {}) };
  const known = points[pending.id];
  if (!known) points[pending.id] = { x: pending.at[0], y: pending.at[1] };
  if (present && known) return sketch;
  return { ...sketch, spec: { ...spec, entities }, geometry: { ...geometry, points } };
}

function sketchCloneSpec(spec) {
  const source = spec || {};
  return {
    plane: source.plane || "xy",
    entities: (source.entities || []).map((entity) => Object.assign({}, entity)),
    constraints: (source.constraints || []).map((constraint) => Object.assign({}, constraint)),
  };
}

function sketchFreshId(prefix, spec) {
  const used = new Set(((spec && spec.entities) || []).map((entity) => entity && entity.id));
  let index = 0;
  while (used.has(prefix + index)) index += 1;
  return prefix + index;
}

function sketchRound(value) {
  return Math.round(Number(value) * 1000) / 1000;
}

function sketchGridSpacing(sketch) {
  const spec = sketch && sketch.spec;
  const raw = spec && spec.grid_mm != null ? spec.grid_mm : sketch && sketch.grid_mm;
  const value = Number(raw);
  if (Number.isFinite(value) && value > 0) return value;
  return 10;
}

function sketchGridLines(view) {
  const width = Number(view && view.width);
  const height = Number(view && view.height);
  const scale = Number(view && view.scale);
  const spacing = Number(view && view.spacing);
  if (!(width > 0) || !(height > 0) || !(scale > 0) || !(spacing > 0)) return [];
  const cx = Number.isFinite(Number(view.centerX)) ? Number(view.centerX) : 0;
  const cy = Number.isFinite(Number(view.centerY)) ? Number(view.centerY) : 0;
  const rotation = Number.isFinite(Number(view.rotation)) ? Number(view.rotation) : 0;
  const turn = Math.cos(rotation);
  const rise = Math.sin(rotation);
  const project = (x, y) => {
    const dx = x - cx;
    const dy = y - cy;
    const rx = dx * turn - dy * rise;
    const ry = dx * rise + dy * turn;
    return [width / 2 + rx * scale, height / 2 - ry * scale];
  };
  const unproject = (sx, sy) => {
    const rx = (sx - width / 2) / scale;
    const ry = (height / 2 - sy) / scale;
    return [cx + rx * turn + ry * rise, cy - rx * rise + ry * turn];
  };
  const corners = [
    unproject(0, 0),
    unproject(width, 0),
    unproject(width, height),
    unproject(0, height),
  ];
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  corners.forEach((point) => {
    minX = Math.min(minX, point[0]);
    maxX = Math.max(maxX, point[0]);
    minY = Math.min(minY, point[1]);
    maxY = Math.max(maxY, point[1]);
  });
  const lines = [];
  const limit = 800;
  const startX = Math.floor(minX / spacing + 1e-9) * spacing;
  const startY = Math.floor(minY / spacing + 1e-9) * spacing;
  for (let x = startX; x <= maxX + spacing * 1e-9 && lines.length < limit; x += spacing) {
    const from = project(x, minY);
    const to = project(x, maxY);
    lines.push({ axis: "x", at: x, world: [x, minY, x, maxY], screen: [from[0], from[1], to[0], to[1]] });
  }
  const verticals = lines.length;
  for (let y = startY; y <= maxY + spacing * 1e-9 && lines.length - verticals < limit; y += spacing) {
    const from = project(minX, y);
    const to = project(maxX, y);
    lines.push({ axis: "y", at: y, world: [minX, y, maxX, y], screen: [from[0], from[1], to[0], to[1]] });
  }
  return lines;
}

function sketchKnownStart(spec, startId) {
  if (typeof startId !== "string" || !startId) return null;
  const entities = (spec && spec.entities) || [];
  return entities.some((entity) => entity && entity.id === startId) ? startId : null;
}

function sketchLineClick(spec, geometry, lineStart, x, y, tolerance) {
  const next = sketchCloneSpec(spec);
  const start = sketchKnownStart(next, lineStart);
  const hit = sketchPointHit(geometry, x, y, tolerance);
  const id = hit || sketchFreshId("p", next);
  if (!hit) next.entities.push({ id, type: "point", at: [sketchRound(x), sketchRound(y)] });
  if (!start || id === start) {
    return { spec: next, lineStart: start || id, commit: !hit, finished: false };
  }
  next.entities.push({ id: sketchFreshId("l", next), type: "line", start, end: id });
  return { spec: next, lineStart: null, commit: true, finished: true };
}

function sketchOpenSpline(sketch, splineId) {
  if (!sketch || typeof splineId !== "string" || !splineId) return null;
  const entity = ((sketch.spec && sketch.spec.entities) || []).find((item) =>
    item && item.id === splineId && item.type === "spline" && !item.closed
  );
  return entity ? splineId : null;
}

function sketchSplineClick(spec, geometry, splineId, pendingPoint, x, y, tolerance) {
  const next = sketchCloneSpec(spec);
  const current = (next.entities || []).find((entity) =>
    entity && entity.id === splineId && entity.type === "spline" && !entity.closed
  );
  const hit = sketchPointHit(geometry, x, y, tolerance);
  const known = hit && sketchKnownStart(next, hit) ? hit : null;
  if (current && known === current.points[0] && current.points.length >= 3) {
    current.closed = true;
    return { spec: next, splineId: null, pending: null, commit: true, finished: true, error: null };
  }
  let id = known;
  let created = false;
  if (!id) {
    id = sketchFreshId("p", next);
    next.entities.push({ id, type: "point", at: [sketchRound(x), sketchRound(y)] });
    created = true;
  }
  if (!current) {
    if (pendingPoint && sketchKnownStart(next, pendingPoint) && id !== pendingPoint) {
      const spline = sketchFreshId("s", next);
      next.entities.push({ id: spline, type: "spline", points: [pendingPoint, id], closed: false });
      return { spec: next, splineId: spline, pending: null, commit: true, finished: false, error: null };
    }
    return { spec: next, splineId: null, pending: id, commit: created, finished: false, error: null };
  }
  if (current.points.indexOf(id) !== -1) {
    return {
      spec,
      splineId: current.id,
      pending: null,
      commit: false,
      finished: false,
      error: "That point is already on the spline.",
    };
  }
  current.points = current.points.concat([id]);
  return { spec: next, splineId: current.id, pending: null, commit: true, finished: false, error: null };
}

function sketchCircleClick(spec, geometry, centerId, x, y, tolerance) {
  const next = sketchCloneSpec(spec);
  const center = sketchKnownStart(next, centerId);
  if (!center) {
    const hit = sketchPointHit(geometry, x, y, tolerance);
    const id = hit || sketchFreshId("p", next);
    if (!hit) next.entities.push({ id, type: "point", at: [sketchRound(x), sketchRound(y)] });
    return { spec: next, circleCenter: id, commit: !hit, finished: false };
  }
  const at = sketchSolvedPoint(next, geometry, center);
  const radius = Math.max(0.5, Math.hypot(x - at[0], y - at[1]));
  const id = sketchFreshId("c", next);
  next.entities.push({ id, type: "circle", center, radius });
  next.constraints.push({
    type: "diameter",
    entity: id,
    value: sketchRound(radius * 2),
    name: id + "_diameter",
  });
  return { spec: next, circleCenter: null, commit: true, finished: true };
}
