/* Cost Observatory — dashboard logic (no dependencies) */
(() => {
  const $ = (sel) => document.querySelector(sel);
  const state = { days: 30, user: null };

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

  function renderBars(sel, items, labelFn) {
    const el = $(sel);
    el.innerHTML = "";
    const top = items.slice(0, 8);
    const max = Math.max(...top.map((r) => r.cost || 0), 0.0001);
    if (!top.length) {
      el.innerHTML = '<p class="daychart-empty">── NO DATA ──</p>';
      return;
    }
    top.forEach((r, i) => {
      const row = document.createElement("div");
      row.className = "brow";
      const pct = Math.max(1.5, ((r.cost || 0) / max) * 100);
      row.innerHTML =
        `<span class="bl" title="${labelFn(r)}">${labelFn(r)}</span>` +
        `<span class="bv">${fmtUSD(r.cost)}</span>` +
        `<span class="bt"><i style="width:${pct}%;animation-delay:${i * 45}ms"></i></span>`;
      el.appendChild(row);
    });
  }

  function renderTable(rows) {
    const tb = $("#rows-table tbody");
    tb.innerHTML = "";
    rows.slice(0, 40).forEach((r) => {
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

  // ------------------------------------------------------------- data

  async function refresh() {
    const [stats, rows] = await Promise.all([
      api(`/api/stats?days=${state.days}`),
      api(`/api/rows?days=${state.days}&limit=40`),
    ]);
    renderTiles(stats.totals);
    renderDays(stats.by_day);
    renderBars("#chart-model", stats.by_model, (r) => r.key || "?");
    renderBars("#chart-user", stats.by_user, (r) => r.key || "?");
    renderBars("#chart-project", stats.by_project, (r) => r.key || "?");
    renderTable(rows.rows);
    renderTicker(stats);
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
