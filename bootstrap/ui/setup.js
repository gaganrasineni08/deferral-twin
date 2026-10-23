/* First-run setup screens (HANDOFF.md section 9.3): welcome, machine check,
   per-component download progress, self-test, then the twin's own window.
   Also: the splash on later launches, installs the twin asks for (#install=row),
   and the last screen after "Remove everything" (#goodbye). */
(function () {
  "use strict";

  // The same screens run in the Windows version: name the machine the way its owner would.
  const IS_WIN = /Windows/i.test(navigator.userAgent);
  const DEVICE = IS_WIN ? "PC" : "Mac";
  const KEYSTORE = IS_WIN ? "Windows Credential Manager" : "your Mac's Keychain";
  const $ = (s) => document.querySelector(s);
  function h(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") e.className = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const k of kids.flat(9)) if (k != null && k !== false) e.append(k.nodeType ? k : String(k));
    return e;
  }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function api(path, body) {
    const r = await fetch("api/" + path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    return d;
  }
  function bytes(n) {
    if (n == null) return "…";
    if (n >= 1e9) return (n / 1e9).toFixed(1) + " GB";
    if (n >= 1e6) return (n / 1e6).toFixed(0) + " MB";
    if (n >= 1e3) return (n / 1e3).toFixed(0) + " KB";
    return n + " B";
  }
  function dur(s) {
    if (s == null || !isFinite(s)) return null;
    if (s < 90) return "under 2 minutes";
    if (s < 3600) return "about " + Math.round(s / 60) + " minutes";
    return "about " + (s / 3600).toFixed(1) + " hours";
  }
  const MARK = () => {
    const ns = "http://www.w3.org/2000/svg", s = document.createElementNS(ns, "svg");
    s.setAttribute("viewBox", "0 0 26 26"); s.setAttribute("class", "mark"); s.setAttribute("aria-hidden", "true");
    s.innerHTML = '<circle cx="9.5" cy="13" r="7.5" fill="none" stroke="var(--accent)" stroke-width="2"/><circle cx="16.5" cy="13" r="7.5" fill="none" stroke="#6e37c8" stroke-width="2" stroke-dasharray="3 2.4"/>';
    return s;
  };
  const view = $("#view");
  function show(...nodes) { view.textContent = ""; view.append(...nodes); view.focus({ preventScroll: true }); window.scrollTo(0, 0); }

  let S = null;
  const only = (() => { const m = /install=([a-z,]+)/.exec(location.hash); return m ? m[1].split(",") : null; })();

  // ---------- welcome ----------
  async function welcome() {
    const size = h("b", { class: "num" }, "measuring…");
    const note = h("p", { class: "small muted" }, "Asking the download servers for the exact sizes.");
    const go = h("button", { class: "btn", type: "button", disabled: true, onclick: () => check(true) }, `Check this ${DEVICE}`);
    show(MARK(),
      h("div", null, h("p", { class: "eyebrow" }, "First run"), h("h1", null, "Set up your Deferral Twin")),
      h("p", { class: "muted" }, `Your twin runs entirely on this ${DEVICE}. Before it can, the app needs to download its models and prepare three small Python environments. You won’t need a terminal, and nothing is installed outside the app’s own folder.`),
      h("section", { class: "panel" },
        h("ul", { class: "facts" },
          h("li", null, h("span", { class: "ic" }, "1"), h("span", null, h("b", null, `Check this ${DEVICE}. `), `${IS_WIN ? "64-bit Windows, memory" : "Apple Silicon, memory, macOS version"} and free disk, before anything is downloaded.`)),
          h("li", null, h("span", { class: "ic" }, "2"), h("span", null, h("b", null, "Download and prepare. "), "Seven models and three environments, each with its own progress bar. If you quit or lose the connection, it picks up where it stopped.")),
          h("li", null, h("span", { class: "ic" }, "3"), h("span", null, h("b", null, "Test it. "), "One answer, one spoken sentence and a short lip-sync clip, so you know everything works before you start."))),
        h("div", { class: "kv" }, h("span", null, "Disk space"), size, h("span", null, "Internet"), h("b", null, "needed once, for setup only. After that the twin works offline.")),
        note),
      h("div", { class: "row" }, go));
    try {
      const c = await api("check", {});
      const need = c.measure.needed_bytes;
      size.textContent = need != null ? bytes(need + 3e9) + " (" + bytes(c.measure.download_bytes) + " of downloads, " + bytes(c.measure.env_bytes) + " of environments, plus working space)" : "unknown";
      note.textContent = c.measure.ok ? "Measured from the download servers just now." : "Some sizes couldn’t be measured: " + c.measure.errors.join("; ");
      if (!c.measure.ok && c.measure.download_bytes == null) {
        note.textContent = "The download servers can’t be reached. Connect to the internet, then try again.";
        go.textContent = "Try again"; go.onclick = () => welcome();
      }
      go.disabled = false;
      welcome.check = c;
    } catch (e) { note.textContent = "Couldn’t measure the downloads: " + e.message; go.disabled = false; go.textContent = "Try again"; go.onclick = () => welcome(); }
  }

  // ---------- machine check ----------
  async function check(fromWelcome) {
    const c = fromWelcome && welcome.check ? welcome.check : await api("check", { force: false });
    const m = c.machine;
    const list = h("div", { class: "checks" }, m.checks.map((x) => h("div", { class: "check" }, h("span", { class: "tick " + (x.ok ? "ok" : "bad") }, x.ok ? "✓" : "✕"),
      h("div", null, h("b", null, x.label), h("p", { class: "small " + (x.ok ? "muted" : "") }, x.detail)))));
    const blocked = !m.ok || c.measure.download_bytes == null;
    show(h("div", null, h("p", { class: "eyebrow" }, "Step 1 of 3"), h("h1", null, m.ok ? `This ${DEVICE} is ready` : `This ${DEVICE} can’t run the twin yet`)),
      h("section", { class: "panel" }, list),
      blocked ? h("div", { class: "banner bad" }, m.ok ? "The download sizes couldn’t be measured, so setup won’t start. Check the internet connection." : "Nothing has been downloaded. Fix what’s marked above, then check again.") : null,
      h("div", { class: "row" },
        blocked ? h("button", { class: "btn", type: "button", onclick: () => { welcome.check = null; welcome(); } }, "Check again")
          : h("button", { class: "btn", type: "button", onclick: start }, "Start setup"),
        h("span", { class: "spacer" }), h("button", { class: "btn quiet", type: "button", onclick: () => api("quit", {}) }, "Quit")));
  }

  // ---------- setup progress ----------
  async function start() { await api("start", only ? { only } : {}); progress(); }

  async function progress() {
    const head = h("div", null, h("p", { class: "eyebrow" }, only ? "Installing" : "Step 2 of 3"), h("h1", null, only ? "Installing what’s missing" : "Downloading and preparing"));
    const resume = h("div");
    const overall = h("section", { class: "panel overall" });
    const comps = h("section", { class: "panel" });
    const err = h("div");
    const pause = h("button", { class: "btn quiet", type: "button", onclick: async () => { await api("pause", {}); } }, "Pause");
    show(head, resume, overall, comps, err, h("div", { class: "row" }, h("p", { class: "small muted" }, "You can quit at any time. Finished parts are kept, and a half-finished download continues from where it stopped."), h("span", { class: "spacer" }), pause));
    for (;;) {
      try { S = await api("state"); } catch (e) { await sleep(1000); continue; }
      const pct = S.total_bytes ? Math.min(100, (100 * S.done_bytes) / S.total_bytes) : 0;
      overall.textContent = "";
      const eta = dur(S.eta_s);
      overall.append(h("div", { class: "row" }, h("b", null, Math.floor(pct) + "%"), h("span", { class: "muted num small" }, bytes(S.done_bytes) + " of " + bytes(S.total_bytes)),
        h("span", { class: "spacer" }), h("span", { class: "muted small" }, S.speed > 1000 ? bytes(S.speed) + "/s" + (eta ? " · " + eta + " left" : "") : S.phase === "running" ? "measuring speed…" : "")),
        h("div", { class: "bar" }, h("i", { style: `width:${pct}%` })));
      if (S.resuming && S.resuming.length && !resume.childNodes.length)
        resume.append(h("div", { class: "banner ok" }, h("b", null, "Resuming. "), S.resuming.map((r) => `${r.label}: ${bytes(r.done_bytes)} of ${bytes(r.total_bytes)} already here`).join(" · ")));
      comps.textContent = "";
      S.rows.filter((r) => !only || only.includes(r.id) || r.id === "envs").forEach((r) => {
        const p = r.total_bytes ? Math.min(100, (100 * r.done_bytes) / r.total_bytes) : r.state === "done" ? 100 : 0;
        comps.append(h("div", { class: "comp " + r.state },
          h("div", null, h("div", { class: "name" }, r.label, h("span", { class: "state " + r.state }, { done: "done", active: "now", error: "stopped", waiting: "waiting" }[r.state])),
            h("p", { class: "small muted" }, r.step && r.state === "active" ? r.step + "…" : r.detail || "")),
          h("div", { class: "size" }, r.state === "done" ? bytes(r.total_bytes) : bytes(r.done_bytes) + " / " + bytes(r.total_bytes)),
          h("div", { class: "bar" }, h("i", { style: `width:${p}%` }))));
      });
      err.textContent = "";
      if (S.phase === "error") {
        err.append(h("div", { class: "banner bad" }, h("p", null, h("b", null, "Setup stopped. "), S.error),
          h("div", { class: "row", style: "margin-top:10px" }, h("button", { class: "btn", type: "button", onclick: start }, "Retry"))));
        pause.hidden = true;
      } else if (S.phase === "paused") {
        err.append(h("div", { class: "banner" }, h("p", null, h("b", null, "Paused. "), "Everything so far is kept."),
          h("div", { class: "row", style: "margin-top:10px" }, h("button", { class: "btn", type: "button", onclick: start }, "Continue"))));
        pause.hidden = true;
      } else pause.hidden = false;
      if (S.phase === "done" && S.complete) return selftest(only ? onlyTests(only) : null);
      await sleep(S.phase === "running" ? 700 : 1500);
    }
  }
  const TEST_FOR = { voice: "voice", hindi: "voice", lipsync: "lipsync", facedet: "lipsync", sharpen: "lipsync", llm: "llm", embedder: "llm" };
  const onlyTests = (rows) => [...new Set(rows.map((r) => TEST_FOR[r]).filter(Boolean))];

  // ---------- self-test ----------
  const TESTS = { llm: ["One answer", "The language model loads and answers a question."], voice: ["One spoken sentence", `The voice model speaks a sentence on this ${DEVICE}’s processor.`], lipsync: ["A short lip-sync clip", "Wav2Lip animates a test face for three seconds."] };
  async function selftest(items) {
    items = items && items.length ? items : ["llm", "voice", "lipsync"];
    await api("selftest", { items });
    const list = h("div", { class: "checks" });
    const foot = h("div");
    show(h("div", null, h("p", { class: "eyebrow" }, only ? "Checking" : "Step 3 of 3"), h("h1", null, "Testing everything works")),
      h("p", { class: "muted" }, `Each part runs once for real. The times are kept and used for the estimates you’ll see later, so they come from this ${DEVICE} rather than a guess.`),
      h("section", { class: "panel" }, list), foot);
    for (;;) {
      await sleep(900);
      try { S = await api("state"); } catch (e) { continue; }
      const st = S.selftest || {};
      list.textContent = "";
      items.forEach((k) => {
        const r = st[k] || {};
        const cls = r.running || r.ok == null ? "run" : r.ok ? "ok" : "bad";
        list.append(h("div", { class: "check" }, h("span", { class: "tick " + cls }, cls === "ok" ? "✓" : cls === "bad" ? "✕" : ""),
          h("div", null, h("b", null, TESTS[k][0]), h("p", { class: "small " + (cls === "bad" ? "" : "muted") },
            cls === "bad" ? r.error : cls === "ok" ? detail(k, r) : TESTS[k][1]))));
      });
      const running = items.some((k) => !st[k] || st[k].running);
      if (running) continue;
      const failed = items.filter((k) => !st[k].ok);
      foot.textContent = "";
      const retry = h("button", { class: "btn" + (failed.includes("llm") ? "" : " quiet"), type: "button", onclick: () => selftest(failed) }, "Retry " + (failed.length === 1 ? "this test" : "these tests"));
      const cont = h("button", { class: "btn", type: "button", onclick: launch }, only ? "Back to your twin" : "Create your twin");
      if (!failed.length) foot.append(h("div", { class: "banner ok" }, "Everything works. From now on the app opens straight away and never needs the internet."), h("div", { class: "row" }, cont));
      else if (failed.includes("llm") && (S.selftest.llm || {}).ok === false) foot.append(h("div", { class: "banner bad" }, "The language model has to work before you can continue. Retry, and if it fails again, the log is in ", h("code", null, S.home + "/logs/setup.log"), "."), h("div", { class: "row" }, retry));
      else foot.append(h("div", { class: "banner" }, "You can continue: the twin will answer in text, and the parts that failed stay switched off until a retry works."), h("div", { class: "row" }, retry, cont));
      return;
    }
  }
  function detail(k, r) {
    if (k === "llm") return `Loaded in ${r.load_s} s, answered in ${r.first_answer_s} s.`;
    if (k === "voice") return `${r.audio_seconds} s of speech in ${r.seconds} s.`;
    if (k === "lipsync") return `${r.audio_seconds} s clip in ${r.seconds} s (${r.per_audio_second} s per second of speech).`;
    return "";
  }

  // ---------- launch / splash ----------
  async function launch() {
    show(h("div", { class: "splash" }, h("div", { class: "pulse" }, MARK()), h("h2", null, "Opening your twin"), h("p", { class: "muted small", id: "sp" }, "Starting…")));
    let l;
    try { l = await api("launch", {}); } catch (e) { return crashed(e.message, []); }
    for (let i = 0; ; i++) {
      if (l.state === "ready") { location.replace(l.url + "/"); return; }
      if (l.state === "crashed" || l.state === "error") return crashed(l.error, l.log || []);
      await sleep(400);
      try { l = await api("launch"); } catch (e) {}
      const sp = $("#sp"); if (sp && i > 10) sp.textContent = "Loading the search model…";
    }
  }
  function crashed(msg, log) {
    show(h("div", null, h("p", { class: "eyebrow" }, "Something went wrong"), h("h1", null, "The twin didn’t start")),
      h("div", { class: "banner bad" }, msg || "It stopped without saying why."),
      log && log.length ? h("pre", { class: "log" }, log.join("\n")) : null,
      h("div", { class: "row" }, h("button", { class: "btn", type: "button", onclick: launch }, "Try again"), h("span", { class: "spacer" }),
        h("button", { class: "btn quiet", type: "button", onclick: () => selftest() }, "Run the self-test")));
  }

  // ---------- goodbye ----------
  async function goodbye() {
    let g = null;
    for (let i = 0; i < 40 && !g; i++) { try { S = await api("state"); g = S.goodbye; } catch (e) {} if (!g) await sleep(300); }
    show(MARK(), h("div", null, h("p", { class: "eyebrow" }, "Removed"), h("h1", null, `Everything is gone from this ${DEVICE}`)),
      h("p", null, g ? `The app’s folder, ${g.home}, was deleted: your twin, its models and environments, ${g.freed} in all.` : "The app’s folder is being deleted."),
      h("section", { class: "panel" }, h("h2", null, "One last step"), h("p", null, "Quit this window, then drag ", h("b", null, "DeferralTwin"), " from your Applications folder to the Bin. Removing the app alone would have left the downloads behind, which is why this screen deleted them first.")),
      h("div", { class: "row" }, h("button", { class: "btn", type: "button", onclick: () => api("quit", {}) }, "Quit")));
  }

  // ---------- route ----------
  async function boot() {
    if (location.hash === "#goodbye") return goodbye();
    try { S = await api("state"); } catch (e) { show(h("p", { class: "banner bad" }, "The setup helper isn’t answering. Quit and open the app again.")); return; }
    if (S.goodbye) return goodbye();
    if (only) {
      if (S.phase === "running") return progress();
      const missing = only.filter((r) => !S.installed[r]);
      return missing.length ? (await api("start", { only: missing }), progress()) : selftest(onlyTests(only));
    }
    if (S.ready) return launch();
    if (S.phase === "running") return progress();
    if (S.complete) return selftest();
    if (S.done_bytes > 0) {                        // an earlier setup was interrupted
      await api("check", {});
      return check(false);
    }
    welcome();
  }
  window.addEventListener("hashchange", () => location.reload());
  boot();
})();
