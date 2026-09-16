/* Interactive knowledge-graph viewer: canvas force layout with a Barnes-Hut
   quadtree, type/relation filters, neighbourhood isolation and an inspector
   that shows the original source and the macro-expanded code side by side. */
(function () {
  "use strict";

  const DATA = JSON.parse(document.getElementById("graph-data").textContent);
  const TYPE_COLORS = __TYPE_COLORS__;
  const REL_COLORS = __REL_COLORS__;
  const TYPE_LABELS = __TYPE_LABELS__;
  const REL_LABELS = __REL_LABELS__;

  const nodes = DATA.nodes.map((n, i) => Object.assign({}, n, { idx: i, x: 0, y: 0, vx: 0, vy: 0 }));
  const edges = DATA.edges.map(e => Object.assign({}, e));
  const byId = new Map(nodes.map(n => [n.id, n]));
  edges.forEach(e => { e.a = byId.get(e.s); e.b = byId.get(e.t); });
  const validEdges = edges.filter(e => e.a && e.b);

  const adj = new Map();
  nodes.forEach(n => adj.set(n.id, []));
  validEdges.forEach(e => {
    adj.get(e.s).push({ other: e.t, rel: e.type, dir: "out", edge: e });
    adj.get(e.t).push({ other: e.s, rel: e.type, dir: "in", edge: e });
  });

  const typeCounts = {}, relCounts = {};
  nodes.forEach(n => typeCounts[n.type] = (typeCounts[n.type] || 0) + 1);
  validEdges.forEach(e => relCounts[e.type] = (relCounts[e.type] || 0) + 1);

  const state = {
    types: new Set(Object.keys(typeCounts)),
    rels: new Set(Object.keys(relCounts)),
    selected: null,
    hovered: null,
    showExternal: false,
    isolate: false,
    depth: 2,
    labels: true,
    curves: true,
    layout: "force",
    zoom: 1, panX: 0, panY: 0,
    repulsion: 120,
    alpha: 1,
    visible: [], visibleEdges: [], visibleIds: new Set()
  };

  const DEFAULT_HIDDEN = new Set(["PARAMETER", "LOCAL"]);

  /* ------------------------------------------------------------------ */
  function neighboursWithin(id, depth) {
    const seen = new Set([id]);
    let frontier = [id];
    for (let d = 0; d < depth; d++) {
      const next = [];
      for (const cur of frontier) {
        for (const link of adj.get(cur) || []) {
          if (!seen.has(link.other)) { seen.add(link.other); next.push(link.other); }
        }
      }
      frontier = next;
      if (!frontier.length) break;
    }
    return seen;
  }

  function computeVisible() {
    let allowed = null;
    if (state.isolate && state.selected) allowed = neighboursWithin(state.selected, state.depth);

    const keep = [];
    for (const n of nodes) {
      if (!state.types.has(n.type)) continue;
      if (!state.showExternal && n.external) continue;
      if (allowed && !allowed.has(n.id)) continue;
      keep.push(n);
    }
    const ids = new Set(keep.map(n => n.id));
    const ke = validEdges.filter(e =>
      ids.has(e.s) && ids.has(e.t) && state.rels.has(e.type) &&
      (state.showExternal || (!e.a.external && !e.b.external)));
    state.visible = keep;
    state.visibleEdges = ke;
    state.visibleIds = ids;
    // keep positions of previously laid out nodes
    let fresh = 0;
    for (const n of keep) if (!n.placed) { seed(n); n.placed = true; fresh++; }
    if (fresh > 0) reheat(1);
    document.getElementById("hud").textContent =
      keep.length + " 节点 / " + ke.length + " 关系";
  }

  function seed(n) {
    const golden = Math.PI * (3 - Math.sqrt(5));
    const i = n.idx + 1;
    const r = 14 * Math.sqrt(i);
    const a = i * golden;
    n.x = Math.cos(a) * r;
    n.y = Math.sin(a) * r;
    n.vx = n.vy = 0;
  }

  function reheat(a) { state.alpha = Math.max(state.alpha, a); }

  /* ------------------------------ physics --------------------------- */
  function QuadTree(nodes) {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of nodes) {
      if (n.x < minX) minX = n.x; if (n.x > maxX) maxX = n.x;
      if (n.y < minY) minY = n.y; if (n.y > maxY) maxY = n.y;
    }
    if (!isFinite(minX)) { minX = minY = -1; maxX = maxY = 1; }
    const size = Math.max(maxX - minX, maxY - minY, 1) * 1.02;
    const root = { x: (minX + maxX) / 2, y: (minY + maxY) / 2, size: size, mass: 0, cx: 0, cy: 0, kids: null, node: null };
    for (const n of nodes) insert(root, n);
    return root;
  }

  function insert(q, n) {
    if (q.mass === 0 && !q.kids && !q.node) { q.node = n; q.mass = 1; q.cx = n.x; q.cy = n.y; return; }
    if (q.node) {
      const old = q.node;
      q.node = null;
      subdivide(q);
      insertChild(q, old);
    }
    if (!q.kids) subdivide(q);
    insertChild(q, n);
    q.cx = (q.cx * q.mass + n.x) / (q.mass + 1);
    q.cy = (q.cy * q.mass + n.y) / (q.mass + 1);
    q.mass += 1;
  }

  function subdivide(q) {
    const h = q.size / 2, k = q.size / 4;
    q.kids = [
      { x: q.x - k, y: q.y - k, size: h, mass: 0, cx: 0, cy: 0, kids: null, node: null },
      { x: q.x + k, y: q.y - k, size: h, mass: 0, cx: 0, cy: 0, kids: null, node: null },
      { x: q.x - k, y: q.y + k, size: h, mass: 0, cx: 0, cy: 0, kids: null, node: null },
      { x: q.x + k, y: q.y + k, size: h, mass: 0, cx: 0, cy: 0, kids: null, node: null }
    ];
  }

  function insertChild(q, n) {
    let best = null, bestD = Infinity;
    for (const k of q.kids) {
      const d = (n.x - k.x) ** 2 + (n.y - k.y) ** 2;
      if (d < bestD) { bestD = d; best = k; }
    }
    insert(best, n);
  }

  function repulse(node, q, strength) {
    if (!q || q.mass === 0) return;
    if (q.node === node) return;
    let dx = node.x - q.cx, dy = node.y - q.cy;
    let d2 = dx * dx + dy * dy;
    if (q.kids && (q.size * q.size) / Math.max(d2, 1e-6) < 1.2) {
      for (const k of q.kids) repulse(node, k, strength);
      return;
    }
    if (d2 < 1e-6) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 0.01; }
    const f = strength * q.mass / d2;
    const d = Math.sqrt(d2);
    node.vx += (dx / d) * f;
    node.vy += (dy / d) * f;
  }

  function step() {
    if (state.alpha < 0.004) return;
    const rep = state.repulsion * state.repulsion;
    const tree = QuadTree(state.visible);
    for (const n of state.visible) {
      if (n.fixed) continue;
      repulse(n, tree, rep);
    }
    // springs
    const ideal = 62;
    for (const e of state.visibleEdges) {
      const a = e.a, b = e.b;
      if (!state.visibleIds.has(a.id) || !state.visibleIds.has(b.id)) continue;
      let dx = b.x - a.x, dy = b.y - a.y;
      let d = Math.hypot(dx, dy) || 0.01;
      const k = e.type === "CONTAINS" ? 0.35 : 1.0;
      const f = (d - ideal) * 0.0022 * k;
      const fx = (dx / d) * f, fy = (dy / d) * f;
      if (!a.fixed) { a.vx += fx; a.vy += fy; }
      if (!b.fixed) { b.vx -= fx; b.vy -= fy; }
    }
    // gravity towards origin keeps disconnected parts together
    for (const n of state.visible) {
      if (n.fixed) continue;
      n.vx += -n.x * 0.0016;
      n.vy += -n.y * 0.0016;
      n.vx *= 0.82; n.vy *= 0.82;
      const sp = Math.hypot(n.vx, n.vy);
      if (sp > 14) { n.vx *= 14 / sp; n.vy *= 14 / sp; }
      n.x += n.vx; n.y += n.vy;
    }
    state.alpha *= 0.985;
  }

  /* ------------------------------ drawing --------------------------- */
  const cv = document.getElementById("cv");
  const ctx = cv.getContext("2d");
  let dpr = Math.min(window.devicePixelRatio || 1, 2);

  function resize() {
    const r = cv.parentElement.getBoundingClientRect();
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    cv.width = Math.max(1, r.width * dpr);
    cv.height = Math.max(1, r.height * dpr);
  }
  window.addEventListener("resize", resize);

  function radius(n) {
    const d = (n.in_degree || 0) + (n.out_degree || 0);
    return 3.4 + Math.min(9, Math.sqrt(d) * 1.5);
  }

  function toScreen(n) {
    return { x: (n.x * state.zoom) + state.panX + cv.width / (2 * dpr),
             y: (n.y * state.zoom) + state.panY + cv.height / (2 * dpr) };
  }

  function highlightSet() {
    const active = state.hovered || state.selected;
    if (!active) return null;
    return neighboursWithin(active, 1);
  }

  function draw() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const W = cv.width / dpr, H = cv.height / dpr;
    ctx.clearRect(0, 0, W, H);
    ctx.save();
    ctx.translate(W / 2 + state.panX, H / 2 + state.panY);
    ctx.scale(state.zoom, state.zoom);

    const hl = highlightSet();
    // edges
    ctx.lineCap = "round";
    for (const e of state.visibleEdges) {
      const a = e.a, b = e.b;
      const dim = hl && !(hl.has(a.id) && hl.has(b.id));
      ctx.strokeStyle = REL_COLORS[e.type] || "#64748b";
      ctx.globalAlpha = dim ? 0.05 : Math.min(0.85, 0.28 + 0.16 * Math.log2(1 + (e.count || 1)));
      ctx.lineWidth = (dim ? 0.6 : state.zoom > 1.4 ? 1.5 : 1.0) / Math.max(state.zoom, 0.4);
      ctx.beginPath();
      if (state.curves) {
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        const dx = b.x - a.x, dy = b.y - a.y;
        const cx = mx - dy * 0.12, cy = my + dx * 0.12;
        ctx.moveTo(a.x, a.y); ctx.quadraticCurveTo(cx, cy, b.x, b.y);
      } else {
        ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
      }
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    // nodes
    const showLabels = state.labels && state.zoom > 0.55;
    ctx.font = (11 / Math.max(state.zoom, 0.6)) + "px 'Segoe UI',sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (const n of state.visible) {
      const r = radius(n);
      const dim = hl && !hl.has(n.id);
      const color = TYPE_COLORS[n.type] || "#94a3b8";
      ctx.globalAlpha = dim ? 0.14 : 1;
      if (n === state.selected || n === state.hovered) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 5 / state.zoom, 0, 6.2832);
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 1.6 / state.zoom;
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, 6.2832);
      ctx.fillStyle = color;
      ctx.shadowColor = color;
      ctx.shadowBlur = dim ? 0 : 7;
      ctx.fill();
      ctx.shadowBlur = 0;
      if (n.external) {
        ctx.strokeStyle = "#0b1020"; ctx.lineWidth = 1 / state.zoom; ctx.stroke();
      }
      if (n.dead_code) {
        ctx.save();
        ctx.setLineDash([3 / state.zoom, 2.4 / state.zoom]);
        ctx.strokeStyle = "#f87171";
        ctx.lineWidth = 1.4 / state.zoom;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 2.6 / state.zoom, 0, 6.2832);
        ctx.stroke();
        ctx.restore();
      }
      if (showLabels && !dim) {
        const weight = (n.in_degree || 0) + (n.out_degree || 0) > 18 || state.zoom > 1.3;
        if (weight) {
          ctx.fillStyle = "rgba(230,237,247,.92)";
          ctx.fillText(n.name, n.x, n.y + r + 2 / state.zoom);
        }
      }
    }
    ctx.globalAlpha = 1;
    ctx.restore();
  }

  function frame() {
    step();
    draw();
    requestAnimationFrame(frame);
  }

  /* ---------------------------- interaction ------------------------- */
  let dragging = null, panning = null;

  function pick(mx, my) {
    const wx = (mx - cv.width / (2 * dpr) - state.panX) / state.zoom;
    const wy = (my - cv.height / (2 * dpr) - state.panY) / state.zoom;
    let best = null, bestD = Infinity;
    for (const n of state.visible) {
      const d = (n.x - wx) ** 2 + (n.y - wy) ** 2;
      const rr = radius(n) + 6;
      if (d < rr * rr && d < bestD) { bestD = d; best = n; }
    }
    return best;
  }

  cv.addEventListener("mousedown", ev => {
    const rect = cv.getBoundingClientRect();
    const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
    const n = pick(mx, my);
    if (n) { dragging = { n: n, ox: n.x, oy: n.y, sx: mx, sy: my }; n.fixed = true; cv.classList.add("dragging"); }
    else { panning = { sx: mx, sy: my, px: state.panX, py: state.panY }; cv.classList.add("dragging"); }
  });

  window.addEventListener("mousemove", ev => {
    const rect = cv.getBoundingClientRect();
    const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
    if (dragging) {
      dragging.n.x = dragging.ox + (mx - dragging.sx) / state.zoom;
      dragging.n.y = dragging.oy + (my - dragging.sy) / state.zoom;
      reheat(0.35);
      return;
    }
    if (panning) {
      state.panX = panning.px + (mx - panning.sx);
      state.panY = panning.py + (my - panning.sy);
      return;
    }
    if (mx < 0 || my < 0 || mx > rect.width || my > rect.height) return;
    const n = pick(mx, my);
    if (n !== state.hovered) { state.hovered = n; document.getElementById("stage").style.cursor = n ? "pointer" : "grab"; }
  });

  window.addEventListener("mouseup", () => {
    if (dragging) dragging.n.fixed = false;
    dragging = null; panning = null; cv.classList.remove("dragging");
  });

  cv.addEventListener("click", ev => {
    const rect = cv.getBoundingClientRect();
    const n = pick(ev.clientX - rect.left, ev.clientY - rect.top);
    select(n ? n.id : null);
  });

  cv.addEventListener("dblclick", ev => {
    const rect = cv.getBoundingClientRect();
    const n = pick(ev.clientX - rect.left, ev.clientY - rect.top);
    if (!n) return;
    state.isolate = true;
    document.getElementById("opt-isolate").checked = true;
    select(n.id);
  });

  cv.addEventListener("wheel", ev => {
    ev.preventDefault();
    const rect = cv.getBoundingClientRect();
    const mx = ev.clientX - rect.left - cv.width / (2 * dpr);
    const my = ev.clientY - rect.top - cv.height / (2 * dpr);
    const k = Math.exp(-ev.deltaY * 0.0014);
    const nz = Math.min(9, Math.max(0.12, state.zoom * k));
    const ratio = nz / state.zoom;
    state.panX = mx - (mx - state.panX) * ratio;
    state.panY = my - (my - state.panY) * ratio;
    state.zoom = nz;
  }, { passive: false });

  /* ------------------------------ panels ---------------------------- */
  function chip(label, count, color, on, handler) {
    const el = document.createElement("div");
    el.className = "chip" + (on ? " on" : " off");
    el.innerHTML = '<span class="dot" style="background:' + color + ';color:' + color + '"></span>' +
                   '<span>' + label + '</span><span class="n">' + count + "</span>";
    el.onclick = () => { handler(); el.className = "chip" + (el.classList.contains("on") ? " off" : " on"); };
    return el;
  }

  function buildChips() {
    const tc = document.getElementById("type-chips");
    tc.innerHTML = "";
    Object.keys(TYPE_LABELS).forEach(t => {
      if (!typeCounts[t]) return;
      const on = !DEFAULT_HIDDEN.has(t);
      if (!on) state.types.delete(t);
      tc.appendChild(chip(TYPE_LABELS[t], typeCounts[t], TYPE_COLORS[t] || "#94a3b8", on, () => {
        if (state.types.has(t)) state.types.delete(t); else state.types.add(t);
        computeVisible();
      }));
    });
    document.getElementById("cnt-entities").textContent = nodes.length;

    const rc = document.getElementById("rel-chips");
    rc.innerHTML = "";
    Object.keys(REL_LABELS).forEach(r => {
      if (!relCounts[r]) return;
      rc.appendChild(chip(REL_LABELS[r], relCounts[r], REL_COLORS[r] || "#64748b", true, () => {
        if (state.rels.has(r)) state.rels.delete(r); else state.rels.add(r);
        computeVisible();
      }));
    });
    document.getElementById("cnt-relations").textContent = validEdges.length;
  }

  const PRESETS = __PRESETS__;

  function applyPreset(key) {
    const p = PRESETS[key];
    state.types = new Set(p.types);
    state.rels = new Set(p.rels);
    state.isolate = !!p.isolate;
    document.getElementById("opt-isolate").checked = state.isolate;
    document.querySelectorAll(".preset").forEach(el => el.classList.toggle("active", el.dataset.key === key));
    // rebuild chip states
    document.querySelectorAll("#type-chips .chip").forEach((el, i) => {});
    rebuildChipStates();
    computeVisible();
    relayout(key === "calls" ? "hub" : "force");
  }

  function rebuildChipStates() {
    const tEls = document.querySelectorAll("#type-chips .chip");
    let i = 0;
    Object.keys(TYPE_LABELS).forEach(t => {
      if (!typeCounts[t]) return;
      tEls[i].className = "chip " + (state.types.has(t) ? "on" : "off");
      i++;
    });
    const rEls = document.querySelectorAll("#rel-chips .chip");
    i = 0;
    Object.keys(REL_LABELS).forEach(r => {
      if (!relCounts[r]) return;
      rEls[i].className = "chip " + (state.rels.has(r) ? "on" : "off");
      i++;
    });
  }

  function buildPresets() {
    const box = document.getElementById("presets");
    Object.keys(PRESETS).forEach(key => {
      const p = PRESETS[key];
      const b = document.createElement("button");
      b.className = "preset";
      b.dataset.key = key;
      b.innerHTML = p.label + "<small>" + p.hint + "</small>";
      b.onclick = () => applyPreset(key);
      box.appendChild(b);
    });
  }

  function relayout(kind) {
    state.layout = kind;
    if (kind === "grid") {
      const cols = Math.ceil(Math.sqrt(state.visible.length));
      state.visible.forEach((n, i) => {
        n.x = (i % cols) * 46 - cols * 23;
        n.y = Math.floor(i / cols) * 46 - cols * 23;
        n.vx = n.vy = 0;
      });
      state.alpha = 0.001;
    } else if (kind === "cluster") {
      const files = [...new Set(state.visible.map(n => n.file || "?"))].sort();
      const centers = new Map();
      const R = Math.max(220, files.length * 26);
      files.forEach((f, i) => {
        const a = (i / files.length) * Math.PI * 2;
        centers.set(f, { x: Math.cos(a) * R, y: Math.sin(a) * R });
      });
      state.visible.forEach(n => {
        const c = centers.get(n.file || "?"); if (!c) return;
        n.x = c.x + (Math.random() - 0.5) * 90;
        n.y = c.y + (Math.random() - 0.5) * 90;
        n.vx = n.vy = 0;
      });
      state.alpha = 0.8;
    } else if (kind === "hub") {
      const ranked = [...state.visible].sort((a, b) =>
        ((b.in_degree || 0) + (b.out_degree || 0)) - ((a.in_degree || 0) + (a.out_degree || 0)));
      ranked.forEach((n, i) => {
        const ring = Math.floor(Math.sqrt(i));
        const r = ring * 58;
        const a = i * 2.399;
        n.x = Math.cos(a) * r; n.y = Math.sin(a) * r;
        n.vx = n.vy = 0;
      });
      state.alpha = 0.7;
    } else {
      reheat(0.9);
    }
  }

  function fit() {
    if (!state.visible.length) return;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of state.visible) {
      minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
      minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y);
    }
    const W = cv.width / dpr, H = cv.height / dpr;
    const sx = W / Math.max(1, maxX - minX + 120);
    const sy = H / Math.max(1, maxY - minY + 120);
    state.zoom = Math.min(2.4, Math.max(0.15, Math.min(sx, sy)));
    state.panX = -((minX + maxX) / 2) * state.zoom;
    state.panY = -((minY + maxY) / 2) * state.zoom;
  }

  /* ----------------------------- inspector -------------------------- */
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, c =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  function select(id) {
    state.selected = id ? byId.get(id) : null;
    renderInspector();
    if (state.isolate) computeVisible();
  }

  function renderInspector() {
    const box = document.getElementById("inspector-content");
    const n = state.selected;
    if (!n) {
      box.innerHTML = '<div class="empty">点击任意节点查看详情：源码位置、宏展开后的真实代码、上下游关系。</div>';
      return;
    }
    const color = TYPE_COLORS[n.type] || "#94a3b8";
    const links = (adj.get(n.id) || []);
    const out = links.filter(l => l.dir === "out");
    const inc = links.filter(l => l.dir === "in");
    const group = (list, dir) => {
      const byRel = {};
      list.forEach(l => (byRel[l.rel] = byRel[l.rel] || []).push(l));
      return Object.keys(byRel).map(rel => {
        const items = byRel[rel].sort((a, b) =>
          ((byId.get(b.other) || {}).degree || 0) - ((byId.get(a.other) || {}).degree || 0)).slice(0, 60);
        return '<div class="relgrp"><div class="h" style="color:' + (REL_COLORS[rel] || "#8b9ac0") + '">' +
          (dir === "out" ? "→ " : "← ") + (REL_LABELS[rel] || rel) + " · " + byRel[rel].length + "</div>" +
          items.map(l => {
            const o = byId.get(l.other) || { name: l.other, type: "?" };
            const via = l.edge && l.edge.via_macro
              ? '<span class="badge2" style="color:#f9a03f;border-color:#6b4a1c">经宏 ' + esc(l.edge.via_macro) + "</span>"
              : "";
            return '<div class="rel" data-go="' + esc(o.id) + '"><span class="arrow">' + (dir === "out" ? "→" : "←") +
              '</span><span class="nm">' + esc(o.name) + '</span><span class="badge2">' +
              (TYPE_LABELS[o.type] || o.type) + "</span>" + via + "</div>";
          }).join("") + "</div>";
      }).join("");
    };

    let html = "";
    if (n.external) html += '<div class="warnbox">该符号不在本工程源码内（驱动层 / 标准库 / 缺失头文件桩）。</div>';
    if (n.dead_code) html += '<div class="warnbox">这段代码在文本中存在，但预处理器没有编译它（位于 <code>#if 0</code> 或未启用分支中），因此编译器看不到它。</div>';
    else if (n.unmatched_by_clang) html += '<div class="warnbox">clang 已编译此行，但没有产出对应实体；该节点来自 tree-sitter 的文本解析，建议人工确认。</div>';
    html += '<h2>' + esc(n.name) + "</h2>";
    html += '<div class="tagline"><span class="tag type" style="background:' + color + '">' +
            (TYPE_LABELS[n.type] || n.type) + "</span>" +
            (n.is_static ? '<span class="tag">static</span>' : "") +
            (n.is_function_like ? '<span class="tag">函数宏</span>' : "") +
            (n.is_pointer ? '<span class="tag">指针</span>' : "") +
            (n.dead_code ? '<span class="tag" style="color:#f87171;border-color:#7f2b2b">未参与编译</span>' : "") +
            '<span class="tag">度 ' + ((n.in_degree || 0) + (n.out_degree || 0)) + "</span>" +
            (n.expansion_count ? '<span class="tag">展开 ' + n.expansion_count + " 处</span>" : "") +
            "</div>";
    html += '<div class="kv">';
    if (n.file) html += "<b>位置</b> " + esc(n.file) + (n.line ? ":" + n.line : "") + "<br>";
    if (n.signature) html += "<b>签名</b> <code>" + esc(n.signature) + "</code><br>";
    if (n.return_type) html += "<b>返回</b> <code>" + esc(n.return_type) + "</code><br>";
    if (n.type_spelling) html += "<b>类型</b> <code>" + esc(n.type_spelling) + "</code><br>";
    if (n.storage_class) html += "<b>存储类</b> " + esc(n.storage_class) + "<br>";
    if (n.scope) html += "<b>作用域</b> " + esc(n.scope) + "<br>";
    if (n.value !== undefined && n.type === "ENUM_CONST") html += "<b>取值</b> " + n.value + "<br>";
    if (n.metrics) html += "<b>度量</b> " + n.metrics.lines + " 行 · 圈复杂度 " + n.metrics.cyclomatic +
                           " · 调用 " + n.metrics.calls + " 次<br>";
    if (n.generated_by_macro && n.generated_by_macro.length)
      html += "<b>由宏生成</b> " + esc(n.generated_by_macro.join(", ")) + "<br>";
    html += "</div>";

    if (n.snippet) {
      html += '<div class="sec"><h3>源码（未展开）</h3><pre>' + esc(n.snippet) + "</pre></div>";
    }
    if (n.body) {
      html += '<div class="sec"><h3>宏定义体</h3><pre>' + esc("#define " + n.name + (n.params && n.params.length ? "(" + n.params.join(", ") + ")" : "") + " " + n.body) + "</pre></div>";
    }
    if (n.expanded) {
      html += '<div class="sec"><h3>宏展开结果</h3><pre>' + esc(n.expanded) + "</pre></div>";
    }
    if (n.expanded_code) {
      html += '<div class="sec"><h3>预处理后的真实代码（clang -E）</h3><pre>' + esc(n.expanded_code) + "</pre></div>";
    }
    if (out.length) html += '<div class="sec"><h3>出边</h3>' + group(out, "out") + "</div>";
    if (inc.length) html += '<div class="sec"><h3>入边</h3>' + group(inc, "in") + "</div>";
    box.innerHTML = html;
    box.querySelectorAll("[data-go]").forEach(el => {
      el.onclick = () => { select(el.dataset.go); focusNode(byId.get(el.dataset.go)); };
    });
  }

  function focusNode(n) {
    if (!n) return;
    state.panX = -n.x * state.zoom;
    state.panY = -n.y * state.zoom;
  }

  /* ------------------------------- misc ----------------------------- */
  function buildLegend() {
    const parts = [];
    Object.keys(TYPE_LABELS).forEach(t => {
      if (!typeCounts[t]) return;
      parts.push('<span class="k"><span class="dot" style="width:9px;height:9px;border-radius:50%;background:' +
        TYPE_COLORS[t] + ';display:inline-block"></span>' + TYPE_LABELS[t] + "</span>");
    });
    const rel = [];
    Object.keys(REL_LABELS).forEach(r => {
      if (!relCounts[r]) return;
      rel.push('<span class="k"><span class="sw" style="background:' + REL_COLORS[r] + '"></span>' +
        REL_LABELS[r] + "</span>");
    });
    document.getElementById("legend").innerHTML =
      '<div class="row">' + parts.join("") + "</div>" +
      '<div class="row" style="margin-top:6px">' + rel.join("") + "</div>";
  }

  function buildSearch() {
    const list = document.getElementById("search-list");
    const top = [...nodes].sort((a, b) => (b.degree || 0) - (a.degree || 0)).slice(0, 800);
    list.innerHTML = top.map(n => "<option value='" + n.name.replace(/'/g, "") + "'>" +
      (TYPE_LABELS[n.type] || n.type) + " · " + (n.file || "") + "</option>").join("");
    const input = document.getElementById("search");
    let timer = null;
    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const q = input.value.trim().toLowerCase();
        if (!q) return;
        const hit = nodes.find(n => n.name.toLowerCase() === q) ||
                    nodes.find(n => (n.name || "").toLowerCase().includes(q));
        if (hit) { select(hit.id); focusNode(hit); highlightPulse(hit); }
      }, 180);
    });
    input.addEventListener("keydown", ev => {
      if (ev.key === "Enter") {
        const q = input.value.trim().toLowerCase();
        const hits = nodes.filter(n => (n.name || "").toLowerCase().includes(q)).slice(0, 12);
        if (hits.length) { select(hits[0].id); focusNode(hits[0]); highlightPulse(hits[0]); }
      }
    });
  }

  function highlightPulse(n) {
    reheat(0.5);
  }

  function breakdown() {
    const el = document.getElementById("breakdown");
    const rows = Object.keys(typeCounts).sort((a, b) => typeCounts[b] - typeCounts[a])
      .map(t => '<span class="pill"><span class="dot" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' +
        (TYPE_COLORS[t] || "#888") + '"></span>' + (TYPE_LABELS[t] || t) + " <b>" + typeCounts[t] + "</b></span>");
    el.innerHTML = rows.join('<br>');
  }

  function wireControls() {
    document.getElementById("opt-external").onchange = e => { state.showExternal = e.target.checked; computeVisible(); };
    document.getElementById("opt-isolate").onchange = e => { state.isolate = e.target.checked; computeVisible(); };
    document.getElementById("opt-labels").onchange = e => { state.labels = e.target.checked; };
    document.getElementById("opt-curves").onchange = e => { state.curves = e.target.checked; };
    document.getElementById("opt-depth").oninput = e => {
      state.depth = +e.target.value; document.getElementById("depth-val").textContent = state.depth;
      if (state.isolate) computeVisible();
    };
    document.getElementById("opt-rep").oninput = e => {
      state.repulsion = +e.target.value;
      document.getElementById("rep-val").textContent = (state.repulsion / 120).toFixed(1);
      reheat(0.6);
    };
    document.getElementById("btn-fit").onclick = fit;
    document.getElementById("btn-expand").onclick = () => reheat(1);
    document.getElementById("layout").onchange = e => relayout(e.target.value);
    window.addEventListener("keydown", ev => {
      if (ev.key === "/" && document.activeElement !== document.getElementById("search")) {
        ev.preventDefault(); document.getElementById("search").focus();
      } else if (ev.key === "Escape") {
        select(null);
      }
    });
  }

  /* ------------------------------- boot ----------------------------- */
  resize();
  buildChips();
  buildPresets();
  buildLegend();
  buildSearch();
  breakdown();
  wireControls();
  computeVisible();
  applyPreset("overview");
  setTimeout(fit, 1500);
  let ticks = 0;
  const boot = setInterval(() => {
    ticks++;
    if (ticks === 12) fit();
    if (ticks > 60) clearInterval(boot);
  }, 500);
  frame();

  document.getElementById("stat-badge").textContent =
    DATA.meta.entities + " 实体 · " + DATA.meta.relations + " 关系 · " +
    DATA.meta.files + " 文件 · " + DATA.meta.macros + " 宏";
  document.getElementById("foot-left").textContent = DATA.meta.generator;
  document.getElementById("foot-right").textContent =
    "宏展开点 " + DATA.meta.expansions + " 处 · 预处理代码 " + DATA.meta.preprocessed_files + " 个 .i 文件";
  window.ckgSelect = select;
})();
