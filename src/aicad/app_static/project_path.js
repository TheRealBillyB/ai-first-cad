// Reports name their pictures with a full path. The file route answers only a
// path relative to the project, so the page sends that. A path outside the
// project is left as it is, and the route refuses it.

function projectRelativePath(path, root) {
  const text = String(path);
  if (!root) return text;
  const base = String(root);
  const windows = base.includes("\\");
  const clean = (value) => (windows ? value.replace(/\\/g, "/") : value).replace(/\/+$/, "");
  const full = clean(text);
  const prefix = clean(base);
  const head = full.slice(0, prefix.length);
  const same = windows ? head.toLowerCase() === prefix.toLowerCase() : head === prefix;
  if (!same || full[prefix.length] !== "/") return text;
  return full.slice(prefix.length + 1);
}
