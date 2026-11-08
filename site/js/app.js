(function () {
  "use strict";
  var items = window.ROADMAP_ITEMS || [];
  var LABEL = { done: "In the 0.9.0 preview", wip: "Before 1.0", next: "Next · CAM", future: "Idea · not built yet" };
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var filter = "all", topic = "all", query = "", current = -1, lastFocus = null;

  // build cards
  $$(".grid").forEach(function (grid) {
    var groups = grid.getAttribute("data-group").split(" ");
    items.forEach(function (it, i) {
      if (groups.indexOf(it.status) < 0) return;
      var wantTopic = grid.getAttribute("data-topic");
      if (wantTopic && it.category !== wantTopic) return;
      var b = document.createElement("button");
      b.type = "button"; b.className = "card"; b.id = "card-" + it.id;
      b.setAttribute("data-status", it.status); b.setAttribute("data-i", i);
      b.setAttribute("aria-label", it.title + ", " + LABEL[it.status]);
      b.innerHTML = '<img loading="lazy" src="images/items/' + it.id + '.svg" alt="" width="800" height="600">' +
        '<span class="meta"><span class="dot dot-' + it.status + '"></span><span><span class="t"></span><span class="c"></span></span></span>';
      $(".t", b).textContent = it.title; $(".c", b).textContent = it.category;
      b.addEventListener("click", function () { open(i); });
      grid.appendChild(b);
    });
  });
  ["all", "done", "wip", "next", "future"].forEach(function (k) {
    var n = k === "all" ? items.length : items.filter(function (x) { return x.status === k; }).length;
    var el = $('[data-count="' + k + '"]'); if (el) el.textContent = n;
  });
  var futures = items.filter(function (x) { return x.status === "future"; });
  var tAll = $('[data-topic-count="all"]'); if (tAll) tAll.textContent = futures.length;
  ["Electronics","Analysis","Construction","Marine","Robotics","App"].forEach(function (k) {
    var el = $('[data-topic-count="' + k + '"]');
    if (el) el.textContent = futures.filter(function (x) { return x.category === k; }).length;
  });

  function visible(it) {
    if (filter === "all") {
      if (topic !== "all" && (it.status !== "future" || it.category !== topic)) return false;
    } else if (it.status !== filter) return false;
    else if (filter === "future" && topic !== "all" && it.category !== topic) return false;
    if (!query) return true;
    return (it.title + " " + it.category + " " + it.desc).toLowerCase().indexOf(query) >= 0;
  }
  function apply() {
    var any = false;
    $$(".card").forEach(function (c) { var ok = visible(items[+c.getAttribute("data-i")]); c.hidden = !ok; any = any || ok; });
    $$(".idea-group").forEach(function (g) { g.hidden = !$$(".card", g).some(function (c) { return !c.hidden; }); });
    $$(".stage").forEach(function (s) { s.hidden = !$$(".card", s).some(function (c) { return !c.hidden; }); });
    $(".empty").hidden = any;
  }
  function setTopicChips() {
    $$("[data-topic]").forEach(function (c) {
      if (!c.classList.contains("chip")) return;
      var on = c.getAttribute("data-topic") === topic;
      c.classList.toggle("is-on", on); c.setAttribute("aria-pressed", on);
    });
  }
  $$(".chip[data-filter]").forEach(function (ch) {
    ch.addEventListener("click", function () {
      filter = ch.getAttribute("data-filter");
      if (filter !== "all" && filter !== "future") topic = "all";
      $$(".chip[data-filter]").forEach(function (c) { var on = c === ch; c.classList.toggle("is-on", on); c.setAttribute("aria-pressed", on); });
      setTopicChips();
      apply();
    });
  });
  $$(".idea-bar .chip").forEach(function (ch) {
    ch.addEventListener("click", function () {
      topic = ch.getAttribute("data-topic");
      if (filter !== "all" && filter !== "future") {
        filter = "future";
        $$(".chip[data-filter]").forEach(function (c) {
          var on = c.getAttribute("data-filter") === "future";
          c.classList.toggle("is-on", on); c.setAttribute("aria-pressed", on);
        });
      }
      setTopicChips();
      apply();
    });
  });
  $("#q").addEventListener("input", function (e) { query = e.target.value.trim().toLowerCase(); apply(); });
  $("#reset").addEventListener("click", function () { $("#q").value = ""; query = ""; topic = "all"; setTopicChips(); $('[data-filter="all"]').click(); });

  // panel
  var panel = $("#panel"), scrim = $(".scrim");
  function order() { return items.map(function (_, i) { return i; }).filter(function (i) { return visible(items[i]); }); }
  function open(i, noHash) {
    var it = items[i]; if (!it) return;
    if (current < 0) lastFocus = document.activeElement;
    current = i;
    $("#p-img").src = "images/items/" + it.id + ".svg"; $("#p-img").alt = it.title + " illustration";
    var b = $("#p-badge"); b.className = "badge badge-" + it.status; b.textContent = LABEL[it.status];
    $("#p-title").textContent = it.title; $("#p-cat").textContent = it.category; $("#p-desc").textContent = it.desc;
    $("#p-svg").href = "images/items/" + it.id + ".svg"; $("#p-png").href = "images/items/" + it.id + ".png";
    var o = order(), k = o.indexOf(i);
    $("#p-pos").textContent = (k + 1) + " / " + o.length;
    panel.hidden = false; scrim.hidden = false; document.body.style.overflow = "hidden";
    panel.scrollTop = 0; $("#p-close").focus();
    if (!noHash) history.replaceState(null, "", "#item-" + it.id);
  }
  function close() {
    panel.hidden = true; scrim.hidden = true; document.body.style.overflow = ""; current = -1;
    history.replaceState(null, "", location.pathname + location.search);
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  function step(d) { var o = order(), k = o.indexOf(current); if (!o.length) return; open(o[(k + d + o.length) % o.length]); }
  $("#p-close").addEventListener("click", close);
  scrim.addEventListener("click", close);
  $("#p-prev").addEventListener("click", function () { step(-1); });
  $("#p-next").addEventListener("click", function () { step(1); });
  document.addEventListener("keydown", function (e) {
    if (panel.hidden) return;
    if (e.key === "Escape") close();
    else if (e.key === "ArrowRight") step(1);
    else if (e.key === "ArrowLeft") step(-1);
    else if (e.key === "Tab") { // simple focus trap
      var f = $$("button, a[href]", panel), first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });
  // swipe on phones
  var sx = null;
  panel.addEventListener("touchstart", function (e) { sx = e.touches[0].clientX; }, { passive: true });
  panel.addEventListener("touchend", function (e) { if (sx === null) return; var dx = e.changedTouches[0].clientX - sx; if (Math.abs(dx) > 60) step(dx < 0 ? 1 : -1); sx = null; });

  function sizeTop() { document.documentElement.style.setProperty("--toph", $(".top").offsetHeight + "px"); }
  sizeTop(); window.addEventListener("resize", sizeTop);
  apply();
  var m = location.hash.match(/^#item-(.+)$/);
  if (m) { var idx = items.findIndex(function (x) { return x.id === m[1]; }); if (idx >= 0) open(idx, true); }
})();
