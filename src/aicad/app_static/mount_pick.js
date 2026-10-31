// Turn a viewport click into a catalog mount request.
// rotation_deg is about the mount axis and snaps to 15 degree steps.
// Halfway rounds toward +∞, the same rule as the session.

function snapMountDegrees(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  const snapped = Math.floor(number / 15 + 0.5) * 15;
  let wrapped = ((snapped + 180) % 360) - 180;
  if (wrapped <= -180 + 1e-9) return 180;
  if (Math.abs(wrapped) < 1e-9) return 0;
  return wrapped;
}

function mountPickRequest(catalogPart, hit, previous) {
  if (!catalogPart) return { error: "Select the catalog part to mount." };
  if (!hit || !hit.part || !hit.ref) return { error: "Pick a face or a round face to mount on." };
  if (hit.part === catalogPart) return { error: "Pick a face on another part." };
  const prior = previous && typeof previous === "object" ? previous : {};
  const mount = {
    part: hit.part,
    ref: hit.ref,
    rotation_deg: snapMountDegrees(prior.rotation_deg),
    flip: !!prior.flip,
  };
  if (prior.mate) mount.mate = prior.mate;
  return { name: catalogPart, mount };
}
