// The reference grid and origin axes in the 3D view.
// They are drawn first, with depth testing off, so every part covers them.
// Nothing here takes part in picking. Model units are millimeters.

const GRID_HALF_CELLS = 30;
const GRID_MAJOR_EVERY = 5;
const GRID_MINOR_COLOR = [0.17, 0.19, 0.22];
const GRID_MAJOR_COLOR = [0.27, 0.3, 0.35];
const AXIS_COLORS = [
  [0.85, 0.33, 0.31],
  [0.42, 0.75, 0.36],
  [0.36, 0.56, 0.9],
];

const GUIDE_VS = `
attribute vec3 aPos;
attribute vec3 aColor;
uniform mat4 uMvp;
uniform vec3 uFade;
varying vec3 vColor;
varying vec2 vOffset;
void main() {
  vColor = aColor;
  vOffset = (aPos.xy - uFade.xy) / uFade.z;
  gl_Position = uMvp * vec4(aPos, 1.0);
}`;
// The fade color is the view's clear color in drawSolid.
const GUIDE_FS = `
precision mediump float;
varying vec3 vColor;
varying vec2 vOffset;
void main() {
  float fade = 1.0 - smoothstep(0.35, 1.0, length(vOffset));
  gl_FragColor = vec4(mix(vec3(0.08, 0.09, 0.11), vColor, fade), 1.0);
}`;

// The spacing of 1, 2, or 5 times a power of ten nearest to a twentieth of the camera distance.
function gridStep(distance) {
  const wanted = Math.max(Number(distance) || 0, 1e-6) / 20;
  const power = 10 ** Math.floor(Math.log10(wanted));
  const scaled = wanted / power;
  const multiple = scaled < 1.5 ? 1 : scaled < 3.5 ? 2 : scaled < 7.5 ? 5 : 10;
  return Number((multiple * power).toPrecision(3));
}

function guideGeometry(target, distance) {
  const step = gridStep(distance);
  const major = step * GRID_MAJOR_EVERY;
  // Lines stay on whole multiples of the spacing while the camera pans.
  const center = [Math.round(target[0] / major) * major, Math.round(target[1] / major) * major];
  const radius = step * GRID_HALF_CELLS;
  const positions = [];
  const colors = [];
  const line = (from, to, color) => {
    positions.push(...from, ...to);
    colors.push(...color, ...color);
  };
  for (let index = -GRID_HALF_CELLS; index <= GRID_HALF_CELLS; index += 1) {
    const color = index % GRID_MAJOR_EVERY === 0 ? GRID_MAJOR_COLOR : GRID_MINOR_COLOR;
    const x = center[0] + index * step;
    const y = center[1] + index * step;
    line([x, center[1] - radius, 0], [x, center[1] + radius, 0], color);
    line([center[0] - radius, y, 0], [center[0] + radius, y, 0], color);
  }
  const length = major * 2;
  AXIS_COLORS.forEach((color, axis) => {
    const end = [0, 0, 0];
    end[axis] = length;
    line([0, 0, 0], end, color);
  });
  return { step, center, radius, positions, colors };
}

function bindGuideAttribute(gl, program, name, buffer) {
  const location = gl.getAttribLocation(program, name);
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.enableVertexAttribArray(location);
  gl.vertexAttribPointer(location, 3, gl.FLOAT, false, 0, 0);
}

const guideState = { key: "", count: 0, program: null, positions: null, colors: null, geometry: null };

// Draw the guides and return the grid spacing in millimeters.
function drawViewportGuides(gl, mvp, target, distance) {
  if (!guideState.program) {
    guideState.program = program(gl, GUIDE_VS, GUIDE_FS);
    guideState.positions = gl.createBuffer();
    guideState.colors = gl.createBuffer();
  }
  const step = gridStep(distance);
  const major = step * GRID_MAJOR_EVERY;
  const key = [step, Math.round(target[0] / major), Math.round(target[1] / major)].join("|");
  if (key !== guideState.key) {
    const geometry = guideGeometry(target, distance);
    gl.bindBuffer(gl.ARRAY_BUFFER, guideState.positions);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(geometry.positions), gl.STATIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, guideState.colors);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(geometry.colors), gl.STATIC_DRAW);
    guideState.key = key;
    guideState.geometry = geometry;
    guideState.count = geometry.positions.length / 3;
  }
  const { center, radius } = guideState.geometry;
  gl.useProgram(guideState.program);
  bindGuideAttribute(gl, guideState.program, "aPos", guideState.positions);
  bindGuideAttribute(gl, guideState.program, "aColor", guideState.colors);
  gl.uniformMatrix4fv(gl.getUniformLocation(guideState.program, "uMvp"), false, mvp);
  gl.uniform3f(gl.getUniformLocation(guideState.program, "uFade"), center[0], center[1], radius);
  gl.disable(gl.DEPTH_TEST);
  gl.drawArrays(gl.LINES, 0, guideState.count);
  gl.enable(gl.DEPTH_TEST);
  return step;
}
