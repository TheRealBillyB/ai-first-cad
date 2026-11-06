// Report pictures in the panels are small. Clicking one opens it over the app,
// fitted to the window. The scroll wheel or a pinch zooms about the pointer,
// a drag moves it, and the X button, Esc, or a click outside the picture closes
// it. A report picture is any image the file route serves.
//
// Loading this file has no side effects. The page calls
// installPictureViewer(document, window) when it starts.

const PICTURE_ZOOM_MAX = 8;
// One wheel notch (deltaY 100) zooms by about 16 percent.
const PICTURE_WHEEL_RATE = 0.0015;
// A press that moves more than this many pixels is a drag, not a click.
const PICTURE_DRAG_SLOP = 4;

function isReportPicture(element) {
  if (!element || element.tagName !== "IMG") return false;
  if (element.closest && element.closest(".picture-viewer")) return false;
  return String(element.getAttribute("src") || "").startsWith("/api/file?");
}

// The scale that shows the whole picture with a margin, at most twice its own size.
function fitPictureScale(width, height, stageWidth, stageHeight) {
  return Math.min((stageWidth * 0.94) / width, (stageHeight * 0.88) / height, 2);
}

// Zoom by `factor` about the stage point (x, y), so that point stays put.
// The picture never gets smaller than fitted, or more than 8 times that.
function zoomPictureAbout(view, factor, x, y, fit) {
  const scale = Math.min(Math.max(view.scale * factor, fit), fit * PICTURE_ZOOM_MAX);
  const ratio = scale / view.scale;
  return { scale, x: x - (x - view.x) * ratio, y: y - (y - view.y) * ratio };
}

function createPictureViewer(doc, win) {
  const overlay = doc.createElement("div");
  overlay.className = "picture-viewer";
  overlay.hidden = true;
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", "Picture at full size");
  const picture = doc.createElement("img");
  picture.className = "picture-viewer-image";
  picture.setAttribute("draggable", "false");
  const closeButton = doc.createElement("button");
  closeButton.type = "button";
  closeButton.className = "picture-viewer-close";
  closeButton.setAttribute("aria-label", "Close picture");
  closeButton.setAttribute("title", "Close (Esc)");
  closeButton.textContent = "×";
  const hint = doc.createElement("p");
  hint.className = "picture-viewer-hint";
  hint.textContent = "Scroll or pinch to zoom. Drag to move. Esc or a click outside closes.";
  overlay.appendChild(picture);
  overlay.appendChild(closeButton);
  overlay.appendChild(hint);
  doc.body.appendChild(overlay);

  let view = { scale: 1, x: 0, y: 0 };
  let fit = 1;
  let opener = null;
  let dragged = 0;
  const pointers = new Map();

  function draw() {
    picture.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.scale})`;
  }

  function place() {
    const width = picture.naturalWidth;
    const height = picture.naturalHeight;
    if (!width || !height) return;
    const stageWidth = win.innerWidth;
    const stageHeight = win.innerHeight;
    fit = fitPictureScale(width, height, stageWidth, stageHeight);
    view = { scale: fit, x: (stageWidth - width * fit) / 2, y: (stageHeight - height * fit) / 2 };
    draw();
  }

  function open(src, alt, from) {
    opener = from || null;
    pointers.clear();
    dragged = 0;
    picture.onload = place;
    picture.alt = alt || "Report picture";
    picture.src = src;
    overlay.hidden = false;
    if (picture.complete) place();
    closeButton.focus();
  }

  function close() {
    if (overlay.hidden) return;
    overlay.hidden = true;
    pointers.clear();
    picture.onload = null;
    picture.removeAttribute("src");
    if (opener && opener.focus) opener.focus();
    opener = null;
  }

  function zoom(factor, x, y) {
    view = zoomPictureAbout(view, factor, x, y, fit);
    draw();
  }

  closeButton.addEventListener("click", (event) => {
    event.stopPropagation();
    close();
  });
  overlay.addEventListener("click", (event) => {
    // The end of a drag or a pinch is not a click outside the picture.
    if (dragged > PICTURE_DRAG_SLOP) {
      dragged = 0;
      return;
    }
    if (event.target !== picture) close();
  });
  overlay.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      zoom(Math.exp(-event.deltaY * PICTURE_WHEEL_RATE), event.clientX, event.clientY);
    },
    { passive: false },
  );
  overlay.addEventListener("pointerdown", (event) => {
    if (event.target === closeButton) return;
    if (pointers.size === 0) dragged = 0;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  });
  overlay.addEventListener("pointermove", (event) => {
    const last = pointers.get(event.pointerId);
    if (!last) return;
    if (pointers.size === 1) {
      const dx = event.clientX - last.x;
      const dy = event.clientY - last.y;
      dragged += Math.abs(dx) + Math.abs(dy);
      view = { scale: view.scale, x: view.x + dx, y: view.y + dy };
    } else {
      // Two fingers: zoom by how far apart they moved, about the point between them.
      const other = [...pointers.entries()].find(([id]) => id !== event.pointerId)[1];
      const before = Math.hypot(last.x - other.x, last.y - other.y);
      const after = Math.hypot(event.clientX - other.x, event.clientY - other.y);
      dragged += PICTURE_DRAG_SLOP + 1;
      if (before > 0) {
        view = zoomPictureAbout(view, after / before, (event.clientX + other.x) / 2, (event.clientY + other.y) / 2, fit);
      }
    }
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    draw();
  });
  const release = (event) => pointers.delete(event.pointerId);
  overlay.addEventListener("pointerup", release);
  overlay.addEventListener("pointercancel", release);
  // Capture, so Esc closes the picture and nothing behind it sees the key.
  doc.addEventListener(
    "keydown",
    (event) => {
      if (event.key !== "Escape" || overlay.hidden) return;
      event.preventDefault();
      event.stopPropagation();
      close();
    },
    true,
  );
  win.addEventListener("resize", () => {
    if (!overlay.hidden) place();
  });

  return {
    open,
    close,
    zoom,
    isOpen: () => !overlay.hidden,
    view: () => ({ ...view }),
    fit: () => fit,
    element: overlay,
  };
}

function installPictureViewer(doc, win) {
  let viewer = null;
  doc.addEventListener("click", (event) => {
    const target = event.target;
    const image = target && target.closest ? target.closest("img") : null;
    if (!isReportPicture(image)) return;
    event.preventDefault();
    viewer = viewer || createPictureViewer(doc, win);
    viewer.open(image.getAttribute("src"), image.alt, image);
  });
  return () => viewer;
}
