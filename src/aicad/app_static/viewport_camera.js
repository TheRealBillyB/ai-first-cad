// Camera moves in the 3D view, for videos: a turntable orbit around the model's
// centre, a path through views the person stores, and a recording of the view
// to a video file. Nothing here is sent to the server. A recording is saved by
// the browser as a download.

const cameraMotion = {
  mode: null,
  frame: 0,
  started: 0,
  from: null,
  keys: [],
  recorder: null,
  chunks: [],
  stopWith: null,
  turnSeconds: 12,
  legSeconds: 2.5,
};

function modelCentre() {
  const box = ui.mesh && ui.mesh.bounding_box;
  if (!box || !box.min || !box.max) return view.target.slice();
  return [0, 1, 2].map((axis) => (box.min[axis] + box.max[axis]) / 2);
}

// The orbit camera as numbers: a standard view is turned into yaw and pitch first.
function cameraSnapshot() {
  leaveStandardView();
  return { yaw: view.yaw, pitch: view.pitch, distance: view.distance, target: view.target.slice() };
}

function applyCamera(camera) {
  view.direction = null;
  view.up = [0, 0, 1];
  view.yaw = camera.yaw;
  view.pitch = Math.max(-1.2, Math.min(1.2, camera.pitch));
  view.distance = camera.distance;
  view.target = camera.target.slice();
}

function startTurntable(options = {}) {
  stopCameraMotion();
  leaveStandardView();
  // Around the model's centre, at the distance and height the person chose.
  view.target = modelCentre();
  cameraMotion.mode = "turntable";
  cameraMotion.from = { yaw: view.yaw };
  cameraMotion.stopWith = options.once ? "turn" : null;
  cameraMotion.started = performance.now();
  cameraMotion.frame = requestAnimationFrame(stepCamera);
  paintCameraControls();
}

function addCameraKey() {
  cameraMotion.keys.push(cameraSnapshot());
  paintCameraControls();
}

function clearCameraKeys() {
  if (cameraMotion.mode === "path") stopCameraMotion();
  cameraMotion.keys = [];
  paintCameraControls();
}

function playCameraPath() {
  if (cameraMotion.keys.length < 2) throw new Error("Add at least two views to play a path.");
  stopCameraMotion();
  cameraMotion.mode = "path";
  cameraMotion.from = unwrappedKeys(cameraMotion.keys);
  cameraMotion.started = performance.now();
  applyCamera(cameraMotion.from[0]);
  drawSolid();
  cameraMotion.frame = requestAnimationFrame(stepCamera);
  paintCameraControls();
}

// Each yaw is moved by whole turns to be within half a turn of the one before,
// so the path turns the short way between two stored views.
function unwrappedKeys(keys) {
  const out = [];
  keys.forEach((key, index) => {
    const copy = { ...key, target: key.target.slice() };
    if (index > 0) {
      const before = out[index - 1].yaw;
      while (copy.yaw - before > Math.PI) copy.yaw -= 2 * Math.PI;
      while (copy.yaw - before < -Math.PI) copy.yaw += 2 * Math.PI;
    }
    out.push(copy);
  });
  return out;
}

function easeInOut(u) {
  return u * u * (3 - 2 * u);
}

// Catmull-Rom through the stored views: the camera passes through each one
// without stopping, and eases in at the first and out at the last.
function catmull(p0, p1, p2, p3, u) {
  const u2 = u * u;
  const u3 = u2 * u;
  return 0.5 * (2 * p1 + (-p0 + p2) * u + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u2 + (-p0 + 3 * p1 - 3 * p2 + p3) * u3);
}

function cameraOnPath(keys, share) {
  const legs = keys.length - 1;
  const at = Math.max(0, Math.min(1, share)) * legs;
  const index = Math.min(Math.floor(at), legs - 1);
  const u = at - index;
  const k0 = keys[Math.max(0, index - 1)];
  const k1 = keys[index];
  const k2 = keys[index + 1];
  const k3 = keys[Math.min(legs, index + 2)];
  const blend = (field) => catmull(k0[field], k1[field], k2[field], k3[field], u);
  return {
    yaw: blend("yaw"),
    pitch: blend("pitch"),
    distance: Math.max(1e-3, blend("distance")),
    target: [0, 1, 2].map((axis) => catmull(k0.target[axis], k1.target[axis], k2.target[axis], k3.target[axis], u)),
  };
}

function stepCamera(now) {
  if (!cameraMotion.mode) return;
  const seconds = (now - cameraMotion.started) / 1000;
  if (cameraMotion.mode === "turntable") {
    const turn = cameraMotion.turnSeconds;
    if (cameraMotion.stopWith === "turn" && seconds >= turn) {
      view.yaw = cameraMotion.from.yaw + 2 * Math.PI;
      drawSolid();
      finishCameraMotion();
      return;
    }
    view.yaw = cameraMotion.from.yaw + (2 * Math.PI * seconds) / turn;
  } else if (cameraMotion.mode === "path") {
    const keys = cameraMotion.from;
    const total = (keys.length - 1) * cameraMotion.legSeconds;
    applyCamera(cameraOnPath(keys, easeInOut(Math.min(1, seconds / total))));
    if (seconds >= total) {
      drawSolid();
      finishCameraMotion();
      return;
    }
  }
  drawSolid();
  cameraMotion.frame = requestAnimationFrame(stepCamera);
}

// The move ended by itself. A recording made for it ends with it.
function finishCameraMotion() {
  cameraMotion.mode = null;
  cameraMotion.frame = 0;
  if (cameraMotion.recorder && cameraMotion.stopWith) stopRecording();
  cameraMotion.stopWith = null;
  paintCameraControls();
}

// A drag, a standard view, or another move stops the camera. A recording goes on.
function stopCameraMotion() {
  if (cameraMotion.frame) cancelAnimationFrame(cameraMotion.frame);
  cameraMotion.frame = 0;
  const was = cameraMotion.mode;
  cameraMotion.mode = null;
  if (was) paintCameraControls();
}

// MP4 where the browser can write it (Chrome, Edge, and Safari can), else WebM.
function recordingType() {
  const recorder = globalThis.MediaRecorder;
  if (!recorder || typeof recorder.isTypeSupported !== "function") return "";
  const types = ["video/mp4;codecs=avc1", "video/mp4", "video/webm;codecs=vp9", "video/webm"];
  return types.find((type) => recorder.isTypeSupported(type)) || "";
}

// "turn" records one turntable turn, "path" records the stored path, and
// anything else records until Record is pressed again.
function startRecording(what) {
  const canvas = $("view");
  if (!globalThis.MediaRecorder || !canvas || typeof canvas.captureStream !== "function") {
    throw new Error("This browser cannot record the view.");
  }
  if (what === "path" && cameraMotion.keys.length < 2) throw new Error("Add at least two views to record a path.");
  const type = recordingType();
  if (!type) throw new Error("This browser cannot write MP4 or WebM video.");
  const recorder = new MediaRecorder(canvas.captureStream(60), { mimeType: type, videoBitsPerSecond: 16000000 });
  cameraMotion.chunks = [];
  recorder.ondataavailable = (event) => {
    if (event.data && event.data.size) cameraMotion.chunks.push(event.data);
  };
  recorder.onstop = () => saveRecording(new Blob(cameraMotion.chunks, { type: recorder.mimeType || type }));
  cameraMotion.recorder = recorder;
  recorder.start(250);
  if (what === "turn") startTurntable({ once: true });
  else if (what === "path") {
    playCameraPath();
    cameraMotion.stopWith = "path";
  } else paintCameraControls();
}

function stopRecording() {
  const recorder = cameraMotion.recorder;
  cameraMotion.recorder = null;
  if (recorder && recorder.state !== "inactive") recorder.stop();
  paintCameraControls();
}

function recordingName(type) {
  const base = String(ui.viewAssembly || ui.part || "view").replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "") || "view";
  return `${base}_view.${String(type).includes("mp4") ? "mp4" : "webm"}`;
}

function saveRecording(blob) {
  const name = recordingName(blob.type);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
  const webm = !String(blob.type).includes("mp4");
  cameraReadout(
    `Saved ${name} to the browser's downloads.` +
      (webm ? " This browser records WebM. Chrome, Edge, and Safari record MP4." : "")
  );
}

function cameraReadout(text) {
  const readout = $("camera-readout");
  if (readout) readout.textContent = text;
}

function paintCameraControls() {
  const turntable = $("camera-turntable");
  if (!turntable) return;
  turntable.textContent = cameraMotion.mode === "turntable" ? "Stop turning" : "Turntable";
  $("camera-play").textContent = cameraMotion.mode === "path" ? "Stop path" : `Play path (${cameraMotion.keys.length})`;
  $("camera-play").disabled = cameraMotion.keys.length < 2 && cameraMotion.mode !== "path";
  const record = $("camera-record");
  record.textContent = cameraMotion.recorder ? "Stop recording" : "Record";
  record.classList.toggle("on", Boolean(cameraMotion.recorder));
  if (cameraMotion.recorder) cameraReadout("Recording the view…");
  else if (cameraMotion.mode === "turntable") cameraReadout(`Turning once every ${cameraMotion.turnSeconds} s around the centre.`);
  else if (cameraMotion.mode === "path") cameraReadout(`Playing ${cameraMotion.keys.length} views.`);
}

function bindCameraControls() {
  const turntable = $("camera-turntable");
  if (!turntable) return;
  turntable.onclick = () => (cameraMotion.mode === "turntable" ? stopCameraMotion() : startTurntable());
  $("camera-add").onclick = () => addCameraKey();
  $("camera-clear").onclick = () => clearCameraKeys();
  $("camera-play").onclick = () => {
    if (cameraMotion.mode === "path") stopCameraMotion();
    else playCameraPath();
  };
  $("camera-turn-seconds").onchange = (event) => {
    const value = Number(event.target.value);
    if (Number.isFinite(value) && value >= 2 && value <= 120) cameraMotion.turnSeconds = value;
  };
  $("camera-record").onclick = () => {
    if (cameraMotion.recorder) {
      stopRecording();
      return;
    }
    try {
      startRecording($("camera-record-what").value);
    } catch (error) {
      cameraReadout(error.message);
    }
  };
  paintCameraControls();
}

if (typeof document !== "undefined" && document.addEventListener) {
  document.addEventListener("DOMContentLoaded", bindCameraControls);
}
