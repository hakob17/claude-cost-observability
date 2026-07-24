/* Cost Observatory — dashboard logic (no dependencies) */
(() => {
  const $ = (sel) => document.querySelector(sel);
  const state = {
    days: 30,
    user: null,
    group: "user",
    groupSort: { col: "cost", dir: "desc" },
    rowsSort: { col: "ts", dir: "desc" },
    stats: null,
  };
  const GROUP_KEY = { user: "by_user", project: "by_project", model: "by_model", day: "by_day" };

  const fmtUSD = (v) => "$" + (v || 0).toLocaleString("en-US", { maximumFractionDigits: 2 });
  const fmtNum = (v) => {
    v = v || 0;
    if (v >= 1e9) return (v / 1e9).toFixed(2) + "B";
    if (v >= 1e6) return (v / 1e6).toFixed(1) + "M";
    if (v >= 1e3) return (v / 1e3).toFixed(1) + "K";
    return String(v);
  };

  async function api(path, opts = {}) {
    const res = await fetch(path, { credentials: "same-origin", ...opts });
    if (res.status === 401) { showLogin(); throw new Error("unauthorized"); }
    if (!res.ok) throw new Error("http " + res.status);
    return res.json();
  }

  // ------------------------------------------------------------- views

  function showLogin() {
    $("#login-view").classList.remove("hidden");
    $("#dash-view").classList.add("hidden");
    $("#username").focus();
  }

  function showDash() {
    $("#login-view").classList.add("hidden");
    $("#dash-view").classList.remove("hidden");
    refresh();
  }

  // ------------------------------------------------------------- render

  function renderTiles(t) {
    $("#t-cost").textContent = fmtUSD(t.cost);
    $("#t-in").textContent = fmtNum(t.tokens_in);
    $("#t-out").textContent = fmtNum(t.tokens_out);
    $("#t-sessions").textContent = fmtNum(t.sessions);
    $("#t-users").textContent = fmtNum(t.users);
  }

  function renderDays(byDay) {
    const el = $("#chart-days");
    el.innerHTML = "";
    if (!byDay.length) {
      el.innerHTML = '<p class="daychart-empty">── NO DATA IN RANGE ──</p>';
      return;
    }
    const max = Math.max(...byDay.map((d) => d.cost || 0), 0.0001);
    byDay.forEach((d, i) => {
      const bar = document.createElement("div");
      bar.className = "daybar";
      bar.dataset.tip = `${d.key} · ${fmtUSD(d.cost)}`;
      const fill = document.createElement("i");
      fill.style.height = Math.max(2, Math.round(((d.cost || 0) / max) * 100)) + "%";
      fill.style.animationDelay = Math.min(i * 18, 500) + "ms";
      bar.appendChild(fill);
      el.appendChild(bar);
    });
  }

  function renderGroup() {
    if (!state.stats) return;
    const items = (state.stats[GROUP_KEY[state.group]] || []).slice();
    const { col, dir } = state.groupSort;
    const day = state.group === "day";
    items.sort((a, b) => {
      let av = col === "key" ? (a.key || "") : (a[col] || 0);
      let bv = col === "key" ? (b.key || "") : (b[col] || 0);
      const cmp = typeof av === "string" ? av.localeCompare(bv) : av - bv;
      return dir === "asc" ? cmp : -cmp;
    });
    const max = Math.max(...items.map((r) => r.cost || 0), 0.0001);
    const tb = $("#group-table tbody");
    tb.innerHTML = "";
    if (!items.length) {
      tb.innerHTML = '<tr><td colspan="5" style="color:var(--faint)">── NO DATA IN RANGE ──</td></tr>';
    }
    items.forEach((r, i) => {
      const tr = document.createElement("tr");
      const pct = Math.max(1.5, ((r.cost || 0) / max) * 100);
      tr.innerHTML =
        `<td class="gname" title="${r.key || "?"}">${r.key || "?"}</td>` +
        `<td class="r"><span class="cost">${fmtUSD(r.cost)}</span>` +
        `<span class="minibar" style="width:${pct}%;animation-delay:${i * 25}ms"></span></td>` +
        `<td class="r">${day ? "—" : fmtNum(r.tokens_in)}</td>` +
        `<td class="r">${day ? "—" : fmtNum(r.tokens_out)}</td>` +
        `<td class="r">${day ? "—" : fmtNum(r.sessions)}</td>`;
      tb.appendChild(tr);
    });
    // reflect active sort on headers
    document.querySelectorAll("#group-table th").forEach((th) => {
      th.classList.toggle("on", th.dataset.sort === col);
      th.classList.toggle("asc", th.dataset.sort === col && dir === "asc");
      th.classList.toggle("desc", th.dataset.sort === col && dir === "desc");
    });
  }

  function renderTable(rows) {
    const tb = $("#rows-table tbody");
    tb.innerHTML = "";
    document.querySelectorAll("#rows-table th").forEach((th) => {
      const on = th.dataset.sort === state.rowsSort.col;
      th.classList.toggle("on", on);
      th.classList.toggle("asc", on && state.rowsSort.dir === "asc");
      th.classList.toggle("desc", on && state.rowsSort.dir === "desc");
    });
    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="7" style="color:var(--faint)">── NO TURNS IN RANGE ──</td></tr>';
    }
    rows.slice(0, 60).forEach((r) => {
      const tr = document.createElement("tr");
      const tin = (r.input_tokens || 0) + (r.cache_read_tokens || 0) + (r.cache_write_tokens || 0);
      tr.innerHTML =
        `<td>${(r.ts || "").slice(0, 19).replace("T", " ")}</td>` +
        `<td>${r.user_email || "?"}</td>` +
        `<td>${r.project || "?"}</td>` +
        `<td><span class="model">${r.model || "?"}</span></td>` +
        `<td class="r">${fmtNum(tin)}</td>` +
        `<td class="r">${fmtNum(r.output_tokens)}</td>` +
        `<td class="r"><span class="cost">${fmtUSD(r.cost_usd)}</span></td>`;
      tb.appendChild(tr);
    });
  }

  function renderTicker(s) {
    const parts = [
      `WINDOW ${state.days}D`,
      `ROWS ${fmtNum(s.totals.rows)}`,
      `MODELS ${s.by_model.length}`,
      `PROJECTS ${s.by_project.length}`,
      `SYNCED ${new Date().toISOString().slice(11, 19)}Z`,
    ];
    $("#ticker").textContent = "── " + parts.join(" ── ") + " ──";
  }

  // ------------------------------------------------------------- tokens

  async function loadTokens() {
    const data = await api("/api/tokens");
    const tb = $("#tokens-table tbody");
    tb.innerHTML = "";
    data.tokens.forEach((t) => {
      const tr = document.createElement("tr");
      const created = new Date(t.created_at * 1000).toISOString().slice(0, 10);
      tr.innerHTML =
        `<td>${t.name}</td><td><span class="model">${t.role}</span></td>` +
        `<td><span class="model">${t.prefix}…</span></td><td>${created}</td>` +
        `<td class="r"><button class="token-revoke" data-prefix="${t.prefix}">REVOKE</button></td>`;
      tb.appendChild(tr);
    });
  }

  $("#token-gen").addEventListener("click", async () => {
    const name = $("#token-name").value.trim();
    if (!name) { $("#token-name").focus(); return; }
    const data = await api("/api/tokens", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    $("#token-value").textContent = data.token;
    $("#token-result").hidden = false;
    $("#token-name").value = "";
    loadTokens();
  });

  $("#token-copy").addEventListener("click", async () => {
    await navigator.clipboard.writeText($("#token-value").textContent);
    $("#token-copy").textContent = "COPIED ✓";
    setTimeout(() => ($("#token-copy").textContent = "COPY"), 1500);
  });

  $("#tokens-table").addEventListener("click", async (e) => {
    const btn = e.target.closest(".token-revoke");
    if (!btn) return;
    if (!confirm(`Revoke token ${btn.dataset.prefix}…? Plugins using it will stop syncing.`)) return;
    await api(`/api/tokens/${btn.dataset.prefix}`, { method: "DELETE" });
    loadTokens();
  });

  // ------------------------------------------------------------- data

  async function loadRows() {
    const { col, dir } = state.rowsSort;
    const rows = await api(`/api/rows?days=${state.days}&limit=60&order_by=${col}&order=${dir}`);
    renderTable(rows.rows);
  }

  async function refresh() {
    const stats = await api(`/api/stats?days=${state.days}`);
    state.stats = stats;
    renderTiles(stats.totals);
    renderDays(stats.by_day);
    renderGroup();
    renderTicker(stats);
    await loadRows();
    loadTokens().catch(() => {});
  }

  // ------------------------------------------------------------- events

  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#login-error").hidden = true;
    try {
      const body = JSON.stringify({
        username: $("#username").value,
        password: $("#password").value,
      });
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body,
      });
      if (!res.ok) throw new Error();
      const data = await res.json();
      state.user = data.user;
      $("#who").textContent = "operator: " + data.user;
      $("#password").value = "";
      showDash();
    } catch {
      $("#login-error").hidden = false;
    }
  });

  $("#days-seg").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-days]");
    if (!btn) return;
    state.days = Number(btn.dataset.days);
    document.querySelectorAll("#days-seg button").forEach((b) => b.classList.toggle("on", b === btn));
    refresh().catch(() => {});
  });

  $("#group-seg").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-group]");
    if (!btn) return;
    state.group = btn.dataset.group;
    document.querySelectorAll("#group-seg button").forEach((b) => b.classList.toggle("on", b === btn));
    // day has no per-key tokens/sessions; default its sort to the day name
    if (state.group === "day") state.groupSort = { col: "key", dir: "asc" };
    renderGroup();
  });

  $("#group-table thead").addEventListener("click", (e) => {
    const th = e.target.closest("th[data-sort]");
    if (!th) return;
    const col = th.dataset.sort;
    state.groupSort = state.groupSort.col === col
      ? { col, dir: state.groupSort.dir === "asc" ? "desc" : "asc" }
      : { col, dir: col === "key" ? "asc" : "desc" };
    renderGroup();
  });

  $("#rows-table thead").addEventListener("click", (e) => {
    const th = e.target.closest("th[data-sort]");
    if (!th) return;
    const col = th.dataset.sort;
    state.rowsSort = state.rowsSort.col === col
      ? { col, dir: state.rowsSort.dir === "asc" ? "desc" : "asc" }
      : { col, dir: col === "ts" || col === "cost" || col === "in" || col === "out" ? "desc" : "asc" };
    loadRows().catch(() => {});
  });

  $("#logout").addEventListener("click", async () => {
    await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
    showLogin();
  });

  $("#export-btn").addEventListener("click", () => {
    const fmt = $("#export-format").value;
    window.location.href = `/api/export?format=${fmt}&days=${state.days}`;
  });

  // ------------------------------------------------------------- boot

  api("/api/me")
    .then((d) => {
      state.user = d.user;
      $("#who").textContent = "operator: " + d.user;
      showDash();
    })
    .catch(() => showLogin());
})();
