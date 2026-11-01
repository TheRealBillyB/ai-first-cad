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
    const walked = walkSketchChain(edges, entities, index);
    const chain = walked.chain;
    if (chain.length < 3) return;
    if (walked.closed) {
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

function sketchEdgeEnds(edge, entities) {
  if (!edge || edge.construction) return null;
  if (edge.type === "line" && edge.start && edge.end && edge.start !== edge.end) {
    return [edge.start, edge.end];
  }
  if (edge.type !== "arc") return null;
  if (edge.start && edge.end && edge.start !== edge.end) return [edge.start, edge.end];
  if (Array.isArray(edge.through) && edge.through.length >= 2) {
    const start = edge.through[0];
    const end = edge.through[edge.through.length - 1];
    if (start && end && start !== end) return [start, end];
  }
  if (edge.tangent_to && edge.end) {
    const line = (entities || []).find((item) => item && item.type === "line" && item.id === edge.tangent_to);
    const origin = line && (edge.from === "start" ? line.start : line.end);
    if (origin && origin !== edge.end) return [origin, edge.end];
  }
  return null;
}

function walkSketchChain(edges, entities, startIndex) {
  const first = sketchEdgeEnds(edges[startIndex], entities);
  if (!first) return { chain: [], closed: false };
  const chain = first.slice();
  const used = new Set([edges[startIndex].id]);
  let cursor = first[first.length - 1];
  let closed = false;
  while (used.size < edges.length) {
    const next = edges.find((edge) => {
      if (used.has(edge.id)) return false;
      const ends = sketchEdgeEnds(edge, entities);
      return ends && (ends[0] === cursor || ends[ends.length - 1] === cursor);
    });
    if (!next) break;
    const ends = sketchEdgeEnds(next, entities);
    used.add(next.id);
    const forward = ends[0] === cursor;
    const rest = forward ? ends.slice(1) : ends.slice(0, -1).reverse();
    if (rest[rest.length - 1] === chain[0]) {
      chain.push(...rest.slice(0, -1));
      closed = true;
      break;
    }
    chain.push(...rest);
    cursor = rest[rest.length - 1];
  }
  if (chain.some((id) => !id)) return { chain: [], closed: false };
  return { chain, closed };
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

function sketchPlaneSpec(answer, selection) {
  const which = String((answer && answer.plane) || "xy").trim().toLowerCase();
  if (which === "face") {
    const part = selection && selection.part;
    const ref = selection && selection.ref;
    if (!part || !ref) return { error: "Select a planar face, then start the sketch on it." };
    return { plane: { kind: "face", part: part, ref: ref } };
  }
  if (which !== "xy" && which !== "yz" && which !== "xz") {
    return { error: "A sketch plane is xy, yz, xz, or face." };
  }
  const offsetText = String((answer && answer.offset) || "").trim();
  const angleText = String((answer && answer.angle) || "").trim();
  if (!offsetText && !angleText) return { plane: which };
  const plane = { kind: "datum", base: which };
  if (offsetText) {
    const offset = Number(offsetText);
    if (!Number.isFinite(offset)) return { error: "Offset is a distance in millimeters." };
    plane.offset = offset;
  }
  if (angleText) {
    const angle = Number(angleText);
    const axis = String((answer && answer.axis) || "x").trim().toLowerCase();
    if (!Number.isFinite(angle)) return { error: "Angle is degrees." };
    if (axis !== "x" && axis !== "y" && axis !== "z") return { error: "A datum angle is about x, y, or z." };
    plane.angle = angle;
    plane.axis = axis;
  }
  return { plane: plane };
}

function sketchPlaneLabel(plane) {
  if (!plane || plane === "xy") return "XY";
  if (plane === "yz") return "YZ";
  if (plane === "xz") return "XZ";
  if (typeof plane !== "object") return "XY";
  if (plane.kind === "face") return "face of " + plane.part;
  const bits = [String(plane.base || "xy").toUpperCase()];
  if (plane.offset) bits.push("offset " + plane.offset);
  if (plane.angle) bits.push(plane.angle + " deg about " + (plane.axis || "x"));
  return bits.join(", ");
}

function sketchCircleThrough(a, b, c) {
  const determinant = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]));
  if (Math.abs(determinant) < 1e-8) return null;
  const ux = (
    (a[0] * a[0] + a[1] * a[1]) * (b[1] - c[1])
    + (b[0] * b[0] + b[1] * b[1]) * (c[1] - a[1])
    + (c[0] * c[0] + c[1] * c[1]) * (a[1] - b[1])
  ) / determinant;
  const uy = (
    (a[0] * a[0] + a[1] * a[1]) * (c[0] - b[0])
    + (b[0] * b[0] + b[1] * b[1]) * (a[0] - c[0])
    + (c[0] * c[0] + c[1] * c[1]) * (b[0] - a[0])
  ) / determinant;
  const radius = Math.hypot(a[0] - ux, a[1] - uy);
  if (!Number.isFinite(radius) || radius < 1e-6) return null;
  return { center: [ux, uy], radius };
}

function sketchTangentContinuation(lineStart, lineEnd, atEnd, target) {
  const origin = atEnd ? lineEnd : lineStart;
  let dx = atEnd ? lineEnd[0] - lineStart[0] : lineStart[0] - lineEnd[0];
  let dy = atEnd ? lineEnd[1] - lineStart[1] : lineStart[1] - lineEnd[1];
  const length = Math.hypot(dx, dy);
  if (length < 1e-9) return null;
  dx /= length;
  dy /= length;
  const normalX = -dy;
  const normalY = dx;
  const offsetX = target[0] - origin[0];
  const offsetY = target[1] - origin[1];
  const along = normalX * offsetX + normalY * offsetY;
  if (Math.abs(along) < 1e-9) return null;
  const signed = (offsetX * offsetX + offsetY * offsetY) / (2 * along);
  if (Math.abs(signed) < 1e-6) return null;
  return {
    center: [origin[0] + signed * normalX, origin[1] + signed * normalY],
    radius: Math.abs(signed),
    startAtLine: signed > 0,
  };
}

function sketchSnap(geometry, x, y, tolerance, spacing) {
  const hit = sketchPointHit(geometry, x, y, tolerance);
  if (hit && geometry && geometry.points && geometry.points[hit]) {
    const point = geometry.points[hit];
    return { x: Number(point.x), y: Number(point.y), point: hit };
  }
  const step = Number(spacing);
  if (Number.isFinite(step) && step > 0) {
    const gx = Math.round(x / step) * step;
    const gy = Math.round(y / step) * step;
    if (Math.hypot(gx - x, gy - y) <= tolerance) return { x: gx, y: gy, point: null };
  }
  return { x, y, point: null };
}

function sketchUniqueName(spec, base) {
  const used = new Set((spec.constraints || []).map((item) => item && item.name).filter(Boolean));
  if (!used.has(base)) return base;
  let index = 2;
  while (used.has(base + index)) index += 1;
  return base + index;
}

function sketchLineContinuation(spec, pointId) {
  const lines = ((spec && spec.entities) || []).filter((entity) =>
    entity && entity.type === "line" && !entity.construction
  );
  const ending = lines.find((line) => line.end === pointId);
  if (ending) return { line: ending.id, from: "end" };
  const starting = lines.find((line) => line.start === pointId);
  if (starting) return { line: starting.id, from: "start" };
  return null;
}

function sketchPlacePoint(spec, geometry, x, y, tolerance) {
  const hit = sketchPointHit(geometry, x, y, tolerance);
  if (hit && sketchKnownStart(spec, hit)) return { id: hit, created: false };
  const id = sketchFreshId("p", spec);
  spec.entities.push({ id, type: "point", at: [sketchRound(x), sketchRound(y)] });
  return { id, created: true };
}

function sketchArcClick(spec, geometry, mode, pending, x, y, tolerance) {
  const next = sketchCloneSpec(spec);
  const placed = (Array.isArray(pending) ? pending : []).filter((id) => sketchKnownStart(next, id));
  const tool = mode === "center" || mode === "tangent" ? mode : "three";
  if (tool === "tangent" && placed.length === 0) {
    const hit = sketchPointHit(geometry, x, y, tolerance);
    const continuation = hit && sketchLineContinuation(next, hit);
    if (!continuation) {
      return {
        spec,
        pending: [],
        commit: false,
        finished: false,
        error: "A tangent arc starts at the end of a line.",
      };
    }
    return { spec: next, pending: [hit], commit: false, finished: false, error: null };
  }
  const point = sketchPlacePoint(next, geometry, x, y, tolerance);
  if (placed.includes(point.id)) {
    return { spec: next, pending: placed, commit: point.created, finished: false, error: null };
  }
  const sequence = placed.concat([point.id]);
  if (tool === "three" && sequence.length < 3) {
    return { spec: next, pending: sequence, commit: point.created, finished: false, error: null };
  }
  if (tool === "center" && sequence.length < 3) {
    return { spec: next, pending: sequence, commit: point.created, finished: false, error: null };
  }
  const id = sketchFreshId("a", next);
  if (tool === "three") {
    const [start, middle, end] = sequence;
    const circle = sketchCircleThrough(
      sketchSolvedPoint(next, geometry, start),
      sketchSolvedPoint(next, geometry, middle),
      sketchSolvedPoint(next, geometry, end),
    );
    if (!circle) {
      return {
        spec,
        pending: placed,
        commit: false,
        finished: false,
        error: "Those three points are in a line, so they do not define an arc.",
      };
    }
    next.entities.push({ id, type: "arc", through: [start, middle, end] });
    return { spec: next, pending: [], commit: true, finished: true, error: null };
  }
  if (tool === "center") {
    const [center, start, end] = sequence;
    const centerAt = sketchSolvedPoint(next, geometry, center);
    const startAt = sketchSolvedPoint(next, geometry, start);
    const radius = Math.max(0.5, Math.hypot(startAt[0] - centerAt[0], startAt[1] - centerAt[1]));
    const endEntity = next.entities.find((entity) => entity && entity.id === end);
    if (endEntity && point.created) {
      const angle = Math.atan2(y - centerAt[1], x - centerAt[0]);
      endEntity.at = [
        sketchRound(centerAt[0] + radius * Math.cos(angle)),
        sketchRound(centerAt[1] + radius * Math.sin(angle)),
      ];
    }
    next.entities.push({ id, type: "arc", center, start, end });
    next.constraints.push({
      type: "radius",
      entity: id,
      value: sketchRound(radius),
      name: sketchUniqueName(next, id + "_radius"),
    });
    return { spec: next, pending: [], commit: true, finished: true, error: null };
  }
  const continuation = sketchLineContinuation(next, sequence[0]);
  const lineEntity = continuation && next.entities.find((entity) => entity && entity.id === continuation.line);
  if (!continuation || !lineEntity) {
    return { spec, pending: [], commit: false, finished: false, error: "A tangent arc starts at the end of a line." };
  }
  const lineStart = sketchSolvedPoint(next, geometry, lineEntity.start);
  const lineEnd = sketchSolvedPoint(next, geometry, lineEntity.end);
  const target = sketchSolvedPoint(next, geometry, sequence[1]);
  const frame = sketchTangentContinuation(lineStart, lineEnd, continuation.from === "end", target);
  if (!frame) {
    return {
      spec,
      pending: placed,
      commit: false,
      finished: false,
      error: "That point is on the line, so a tangent arc does not continue it.",
    };
  }
  next.entities.push({
    id,
    type: "arc",
    tangent_to: continuation.line,
    end: sequence[1],
    from: continuation.from,
  });
  next.constraints.push({
    type: "radius",
    entity: id,
    value: sketchRound(frame.radius),
    name: sketchUniqueName(next, id + "_radius"),
  });
  return { spec: next, pending: [], commit: true, finished: true, error: null };
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
