/* Deferral-Aware Twin — the app's window (HANDOFF.md sections 9.4 to 9.7).
   Onboarding: consent → interview → voice → photo → build. Then the main window:
   Ask, Tell it about you, Profile, Results, Settings.
   Live mode talks to app/server.py. When no server answers (the published
   preview), a clearly-labelled simulation runs instead so every screen can be
   clicked through. */
(function () {
  "use strict";

  // The same screens run in the Windows version: name the machine the way its owner would.
  const IS_WIN = /Windows/i.test(navigator.userAgent);
  const DEVICE = IS_WIN ? "PC" : "Mac";
  const KEYSTORE = IS_WIN ? "Windows Credential Manager" : "your Mac's Keychain";

  // ---------- tiny DOM helpers ----------
  const $ = (s, r) => (r || document).querySelector(s);
  function h(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") e.className = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else if (k === "html") e.innerHTML = v;
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const k of kids.flat(9)) if (k != null && k !== false) e.append(k.nodeType ? k : String(k));
    return e;
  }
  const fmt = (x, d = 2) => (x == null ? "n/a" : Number(x).toFixed(d));
  const pct = (x) => (x == null ? "n/a" : Math.round(x * 100) + "%");
  function toast(msg) {
    const t = $("#toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), 3600);
  }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const store = {
    get(k) { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
    del(k) { try { localStorage.removeItem(k); } catch (e) {} },
  };
  function minutes(s) {
    if (s == null) return null;
    if (s < 60) return Math.max(5, Math.round(s / 5) * 5) + " s";
    return "about " + Math.max(1, Math.round(s / 60)) + " min";
  }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  /* Minimal Markdown for Profile.as_markdown(): headings, emphasis, bullets, tables. */
  function markdown(md) {
    const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/\*(.+?)\*/g, "<em>$1</em>").replace(/`(.+?)`/g, "<code>$1</code>");
    const out = []; const lines = md.split("\n"); let i = 0;
    while (i < lines.length) {
      const l = lines[i];
      if (/^#{1,4} /.test(l)) { const n = l.match(/^#+/)[0].length; out.push(`<h${n + 1}>${inline(l.slice(n + 1))}</h${n + 1}>`); i++; }
      else if (/^\|/.test(l)) {
        const rows = []; while (i < lines.length && /^\|/.test(lines[i])) rows.push(lines[i++]);
        const cells = (r) => r.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        const body = rows.filter((r, k) => k !== 1 || !/^\|[\s|:-]+\|$/.test(r));
        out.push('<div class="tbl-wrap"><table><thead><tr>' + cells(body[0]).map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>" +
          body.slice(1).map((r) => "<tr>" + cells(r).map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") + "</tbody></table></div>");
      } else if (/^- /.test(l)) { const items = []; while (i < lines.length && /^- /.test(lines[i])) items.push(lines[i++].slice(2)); out.push("<ul class=\"plain\">" + items.map((x) => `<li>${inline(x)}</li>`).join("") + "</ul>"); }
      else if (l.trim()) { out.push(`<p>${inline(l)}</p>`); i++; }
      else i++;
    }
    return out.join("\n");
  }

  const TIER = {
    confident_answer: { cls: "conf", label: "You said this" },
    soft_answer: { cls: "inf", label: "You never answered this · reasoning from your profile" },
    hard_defer: { cls: "def", label: "Refused · nothing close enough" },
    about_twin: { cls: "self", label: "About your twin · not from your memories" },
    field_knowledge: { cls: "fld", label: "Expert knowledge" },
  };
  const HARD = 0.15, SOFT = 0.35, GMAX = 0.6;
  const CONSENT_TEXT = [
    "My answers will be used to build a digital twin of me for this course project and to test how well it answers.",
    "My own voice recording and photo may be used only to make the twin speak and appear in the project demo.",
    "I understand I can withdraw at any time and ask for my data to be deleted.",
  ];
  const CONSENT_SAVED = [
    "answers used to build a digital twin for the course project and test it",
    "voice recording and photo used only for the project demo",
    "can withdraw and request deletion at any time",
  ];
  const READ_ALOUD = "Every morning I try to start the day slowly. I make a cup of tea, open the window, and listen to the street for a minute before I look at my phone. Some days are busy, with classes, meetings and work that runs late into the evening. Other days are quiet, and I spend them reading, cooking, or walking without any plan at all. I like learning how things work, and I enjoy explaining them to friends, even when I get the details a little wrong. If you asked me what matters most, I would probably say the people around me, a good night’s sleep, and always having something to look forward to.";

  // ---------- state ----------
  const S = {
    mode: null, status: null, Q: null, view: null, sec: 0,
    draft: { consent: null, answers: {}, pos: null },
    chat: [], turns: [], settings: { speak: false, video: false, lang: "en", sharpen: false },
  };
  const DKEY = "twin-app-draft-v2";

  // ---------- API ----------
  async function req(method, path, body, isForm) {
    const init = { method, headers: {} };
    if (body !== undefined) {
      if (isForm) init.body = body;
      else { init.body = JSON.stringify(body); init.headers["Content-Type"] = "application/json"; }
    }
    const r = await fetch(path, init);
    const ct = r.headers.get("content-type") || "";
    const data = ct.includes("json") ? await r.json() : null;
    if (!r.ok) throw new Error((data && data.detail) || `${r.status} ${r.statusText}`);
    return data;
  }
  const live = {
    status: () => req("GET", "api/status"),
    getDraft: () => req("GET", "api/draft"),
    putDraft: (d) => req("PUT", "api/draft", d),
    upload: (kind, file) => { const f = new FormData(); f.append("file", file, file.name || kind); return req("POST", "api/upload/" + kind, f, true); },
    captureVoiceStart: () => req("POST", "api/capture/voice/start", {}),
    captureVoiceStop: () => req("POST", "api/capture/voice/stop", {}),
    capturePhoto: () => req("POST", "api/capture/photo", {}),
    build: (p) => req("POST", "api/build", p),
    buildStatus: () => req("GET", "api/build"),
    async askStream(q, onToken, history) {
      const r = await fetch("api/ask/stream", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q, history: history || [] }) });
      if (!r.ok) { let d = null; try { d = await r.json(); } catch (e) {} throw new Error((d && d.detail) || r.statusText); }
      const reader = r.body.getReader(), dec = new TextDecoder(); let buf = "", done = null;
      for (;;) {
        const { value, done: end } = await reader.read();
        if (end) break;
        buf += dec.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl); buf = buf.slice(nl + 1);
          if (!line.trim()) continue;
          const m = JSON.parse(line);
          if (m.type === "token") onToken(m.text);
          else if (m.type === "done") done = m;
          else if (m.type === "error") throw new Error(m.detail);
        }
      }
      if (!done) throw new Error("The answer was cut off.");
      return done;
    },
    heldout: () => req("GET", "api/heldout"),
    heldoutAll: () => req("GET", "api/heldout/all"),
    correct: (b) => req("POST", "api/correct", b),
    game: () => req("GET", "api/game"),
    bigfive: () => req("GET", "api/bigfive"),
    decide: (situation, options) => req("POST", "api/decide", { situation, options }),
    transcribe: (file, lang) => { const f = new FormData(); f.append("file", file, file.name || "speech.webm"); return req("POST", "api/transcribe" + (lang ? "?lang=" + lang : ""), f, true); },
    gameGuess: (question) => req("POST", "api/game/guess", { question }),
    gameAnswer: (question, answer) => req("POST", "api/game/answer", { question, answer }),
    gameReset: () => req("POST", "api/game/reset"),
    note: (t) => req("POST", "api/note", { text: t }),
    memory: () => req("GET", "api/memory"),
    gaps: () => req("GET", "api/gaps"),
    online: () => req("GET", "api/online"),
    onlineKey: (key) => req("POST", "api/online/key", { key }),
    onlineKeyDelete: () => req("DELETE", "api/online/key"),
    onlineProfile: () => req("POST", "api/online/profile"),
    work: () => req("GET", "api/work"),
    workSave: (b) => req("POST", "api/work", b),
    workDelete: () => req("DELETE", "api/work"),
    workUpload: (file) => { const f = new FormData(); f.append("file", file, file.name); return req("POST", "api/work/upload", f, true); },
    profile: () => req("GET", "api/profile"),
    media: (b) => req("POST", "api/media", b),
    mediaStatus: (id) => req("GET", "api/media/" + id),
    mediaUrl: (id, kind) => "api/media/" + id + "/" + kind,
    welcome: () => req("GET", "api/welcome"),
    results: () => req("GET", "api/results"),
    runEval: () => req("POST", "api/eval"),
    putSettings: (s) => req("PUT", "api/settings", s),
    demo: (b) => req("POST", "api/demo", b),
    demoStatus: () => req("GET", "api/demo"),
    exportData: () => req("POST", "api/export"),
    reveal: (p) => req("POST", "api/reveal", { path: p }),
    redo: (keep) => req("POST", "api/redo", { keep_memory: keep }),
    del: (name) => req("POST", "api/delete", { name }),
    removeAll: (name) => req("POST", "api/remove-everything", { name }),
  };
  let API = live;

  // ---------- simulation for the preview (no server) ----------
  const Sim = (function () {
    const STOP = new Set("a an the and or but if of to in on at for with is are was were be been do does did you your i me my we our it its this that what which who how why when where would could should will can have has had about from as by not no so than then there they them their just very really more most some any".split(" "));
    const tok = (s) => (s || "").toLowerCase().replace(/[^a-z0-9 ]+/g, " ").split(/\s+/).filter((w) => w.length > 2 && !STOP.has(w)).map((w) => w.replace(/(ing|ed|es|s)$/, ""));
    function sim(a, b) {
      const A = tok(a), B = tok(b); if (!A.length || !B.length) return 0;
      const ca = {}, cb = {}; A.forEach((w) => (ca[w] = (ca[w] || 0) + 1)); B.forEach((w) => (cb[w] = (cb[w] || 0) + 1));
      let dot = 0; for (const w in ca) if (cb[w]) dot += ca[w] * cb[w];
      const n = (c) => Math.sqrt(Object.values(c).reduce((s, x) => s + x * x, 0));
      return dot / (n(ca) * n(cb));
    }
    let facts = [], test = [], profile = null, built = false, notes = 0, corr = 0, voice = false, photo = false;
    function trainFrom(payload) {
      const ans = payload.answers.filter((a) => !a.repeat_of);
      const held = new Set(); let k = 0;
      ans.forEach((a) => { if (["What would you do?", "Either or", "In your own words", "Everyday preferences"].includes(a.section) && (k++ % 4 === 3)) held.add(a.id); });
      test = ans.filter((a) => held.has(a.id));
      facts = ans.filter((a) => !held.has(a.id)).map((a) => ({ text: "Q: " + a.question + "\nA: " + a.answer, source: "interview", q: a.question, a: a.answer }));
      profile = computeProfile(ans, firstName(payload.consent.name));
    }
    function retrieve(q) {
      return facts.map((f) => {
        const raw = Math.max(sim(q, f.q || f.text) * 0.75, sim(q, f.text) * 0.6);
        return { raw, rank: raw + (f.source === "interview" ? 0 : 0.05), f };
      }).sort((x, y) => y.rank - x.rank).slice(0, 8);
    }
    const first = (s) => s.replace(/\(\d of 5\)/, "").trim();
    function ask(q) {
      const r = retrieve(q), conf = r.length ? Math.max(...r.map((x) => x.raw)) : 0;
      const ev = r.map((x) => ({ score: +x.raw.toFixed(3), source: x.f.source === "interview" ? "interview" : x.f.source, text: x.f.text.replace("\n", "  ") }));
      let tier, answer;
      if (conf < HARD) { tier = "hard_defer"; answer = "I don't think I've shared my view on that, so I can't answer as myself here."; }
      else if (conf < SOFT) {
        tier = "soft_answer";
        const p = profile.situations.map((x) => [sim(q, x[0] + " " + x[1]), x]).sort((a, b) => b[0] - a[0])[0];
        answer = p ? `I haven't been asked that exactly. The closest situation I described was “${p[1][0]}”, and there I said: ${p[1][1]}` : "I haven't been asked that, so I can only guess from how I usually am.";
      } else { tier = "confident_answer"; answer = first(r[0].f.a || r[0].f.text); }
      return { answer, tier, confidence: conf, evidence: ev, seconds: 0.4, simulated: true };
    }
    function computeProfile(ans, name) {
      const ITEMS = { "start conversations with new people": ["extraversion", 1], "long social events leave me drained": ["extraversion", -1], "tend to stay quiet in groups": ["extraversion", -1], "centre of attention": ["extraversion", 1], "other people's point of view": ["agreeableness", 1], "irritated quickly by other people": ["agreeableness", -1], "avoid arguments to keep the peace": ["agreeableness", 1], "help others even when it is inconvenient": ["agreeableness", 1], "keep my things organised": ["conscientiousness", 1], "leave tasks until the last minute": ["conscientiousness", -1], "follow through on what i promise": ["conscientiousness", 1], "set goals and track my progress": ["conscientiousness", 1], "deadlines stress me out": ["emotional_steadiness", -1], "stay calm when things go wrong": ["emotional_steadiness", 1], "worry about things i cannot control": ["emotional_steadiness", -1], "mood changes quickly": ["emotional_steadiness", -1], "trying unfamiliar things": ["openness", 1], "abstract or theoretical ideas": ["openness", 1], "routine bores me": ["openness", 1], "curious about how things work": ["openness", 1] };
      const b = {}, P = { name, traits: {}, style: [], values: [], precedents: [], situations: [], basics: [], n_answers: ans.length };
      ans.forEach((a) => {
        const qn = a.question.toLowerCase(), m = /\((\d) of 5\)/.exec(a.answer);
        if (m) {
          const v = +m[1], hit = Object.keys(ITEMS).find((f) => qn.includes(f));
          if (hit) (b[ITEMS[hit][0]] = b[ITEMS[hit][0]] || []).push(ITEMS[hit][1] > 0 ? v : 6 - v);
          else if (a.section === "What you believe" && (v === 1 || v === 5)) P.values.push((v === 5 ? "agrees" : "disagrees") + " that " + a.question.replace(/\.$/, "").toLowerCase());
          return;
        }
        if (a.section === "Either or") P.style.push(a.question.replace(/[?.]$/, "") + " -> " + a.answer);
        else if (a.section === "What would you do?" || a.section === "In your own words") P.precedents.push([a.question, a.answer]);
        if (a.section === "What would you do?") P.situations.push([a.question, a.answer]);
      });
      const band = (s) => s >= 70 ? "high" : s >= 55 ? "moderately high" : s > 45 ? "mixed" : s > 30 ? "moderately low" : "low";
      ["extraversion", "agreeableness", "conscientiousness", "emotional_steadiness", "openness"].forEach((t) => {
        if (!b[t]) return; const s = Math.round(100 * (b[t].reduce((x, y) => x + y, 0) / b[t].length - 1) / 4);
        P.traits[t] = { score: s, band: band(s), n_items: b[t].length };
      });
      P.poles = { extraversion: ["reserved, keeps to a small circle", "outgoing, energised by people"], agreeableness: ["blunt, will push back", "accommodating, keeps the peace"], conscientiousness: ["flexible, works in bursts", "organised, plans and follows through"], emotional_steadiness: ["reacts strongly under pressure", "steady under pressure"], openness: ["prefers the familiar and concrete", "drawn to new and abstract things"] };
      const tl = Object.entries(P.traits).map(([t, d]) => `| ${t.replace("_", " ").replace(/^./, (c) => c.toUpperCase())} | **${d.score}**/100 (${d.band}) | ${P.poles[t][d.score >= 50 ? 1 : 0]} |`);
      P.markdown = [`### Behaviour profile — ${name}`, `*Derived from ${ans.length} interview answers. Scores are computed, not guessed.*`, "", "| Trait | Score | Reads as |", "|---|---|---|", ...tl, "",
        P.style.length ? "**Decision style:** " + P.style.join("; ") : "", "", "**Reaction precedents used for unseen situations:**", "", ...P.precedents.slice(0, 8).map((x) => `- *${x[0]}* → ${x[1]}`)].join("\n");
      return P;
    }
    let buildState = { state: "idle", stage: 0, stages: ["Saving your interview", "Splitting answers into memory and held-out questions", "Preparing your voice and photo", "Connecting voice and avatar", "Saving everything to disk", "Embedding the twin's memory", "Loading the language model"], log: [], error: null };
    const notInPreview = async () => { throw new Error("That works in the desktop app, not in this preview."); };
    return {
      status: async () => ({ mode: "preview", llm: { state: "ready", name: "Simulated", stand_in: true }, built, subject: S.draft.consent && S.draft.consent.name,
        build: buildState, has_voice: voice, has_photo: photo, settings: S.settings, components: {}, video_jobs: [],
        media: { voice: false, video: false, consented: true, installed: {}, why: "Voice and avatar run in the desktop app.", estimates: { measured: false } },
        memory: built ? { corrections: corr, notes, facts: facts.length, heldout: test.length } : null }),
      getDraft: async () => ({}),
      putDraft: async () => ({ ok: true }),
      upload: async (kind, file) => {
        if (kind === "voice") {
          const secs = await new Promise((res) => { const a = new Audio(); a.preload = "metadata"; a.onloadedmetadata = () => res(a.duration); a.onerror = () => res(null); a.src = URL.createObjectURL(file); });
          const s = isFinite(secs) ? secs : null, problems = [];
          if (s != null && s < 15) problems.push(`Too short: ${Math.round(s)} seconds. Read the whole paragraph, about 45 seconds.`);
          voice = voice || !problems.length;
          return { ok: !problems.length, seconds: s, problems, warnings: [] };
        }
        const dims = await new Promise((res) => { const i = new Image(); i.onload = () => res([i.naturalWidth, i.naturalHeight]); i.onerror = () => res([0, 0]); i.src = URL.createObjectURL(file); });
        const w = []; if (Math.min(...dims) < 360) w.push("The photo is small. Use one at least 512 pixels on each side.");
        photo = true;
        return { ok: true, width: dims[0], height: dims[1], problems: [], warnings: w.concat(["The face check runs in the desktop app."]), face: null };
      },
      captureVoiceStart: notInPreview, captureVoiceStop: notInPreview, capturePhoto: notInPreview,
      build: async (p) => {
        buildState = Object.assign(buildState, { state: "running", stage: 0, log: [], error: null });
        (async () => {
          for (let i = 1; i <= 7; i++) { await sleep(i === 6 ? 900 : 450); buildState.stage = i; if (i === 1) trainFrom(p); }
          buildState.state = "done"; built = true;
        })();
        return buildState;
      },
      buildStatus: async () => buildState,
      askStream: async (q, onToken) => {
        await sleep(250); const out = ask(q);
        if (out.tier !== "hard_defer") for (const w of out.answer.split(/(?<= )/)) { onToken(w); await sleep(18); }
        const held = test.find((t) => t.question === q);
        if (held) { out.truth = held.answer; out.truth_similarity = out.tier === "hard_defer" ? null : +sim(out.answer, held.answer).toFixed(3); }
        return out;
      },
      heldout: async () => { const r = test[Math.floor(Math.random() * test.length)]; return { question: r.question, section: r.section }; },
      heldoutAll: async () => ({ questions: test.map((t) => t.question) }),
      correct: async (b) => { facts.push({ text: "Q: " + b.question + "\nA: " + b.expected, source: "user_correction", q: b.question, a: b.expected }); corr++; return { ok: true, corrections: corr }; },
      note: async (t) => { const lines = t.split(/\n+/).map((x) => x.trim().replace(/^[-*•]\s*/, "")).filter(Boolean); lines.forEach((l) => facts.push({ text: l, source: "user_note", q: l, a: l })); notes += lines.length; return { ok: true, added: lines, notes }; },
      memory: async () => ({ items: facts.filter((f) => f.source !== "interview").map((f) => ({ kind: f.source === "user_correction" ? "correction" : "note", text: f.text })).reverse(), interview_facts: facts.filter((f) => f.source === "interview").length }),
      profile: async () => profile,
      media: notInPreview, mediaStatus: notInPreview,
      results: async () => ({ phase1: PHASE1, phase2: null, eval: { state: "idle" }, figures: [], benchmark: null }),
      runEval: notInPreview,
      putSettings: async (s) => Object.assign(S.settings, s),
      welcome: async () => ({ state: "off" }),
      demo: notInPreview, demoStatus: async () => ({ state: "idle", items: [] }),
      exportData: notInPreview, reveal: notInPreview, redo: async () => { built = false; return { ok: true }; },
      del: async () => { built = false; facts = []; voice = photo = false; return { ok: true }; }, removeAll: notInPreview,
    };
  })();
  const PHASE1 = { participants: 100, heldout_questions: 9564, twin_mean: 0.332, twin_ci: [0.325, 0.339], human_ceiling: 0.892,
    tiers: [{ tier: "hard_defer", share: 0.01, score: null }, { tier: "soft_answer", share: 0.194, score: 0.16 }, { tier: "confident_answer", share: 0.796, score: 0.373 }],
    selective: { score: 0.373, coverage: 0.8, gain: 0.042 }, feedback: { participants: 9, before: 0.35, after5: 0.563, control: 0.57, personal20: 0.023 } };

  // ---------- draft: every answer is saved as it is typed ----------
  let draftTimer = null;
  function saveDraft() {
    store.set(DKEY, S.draft);
    clearTimeout(draftTimer);
    if (S.mode === "live") draftTimer = setTimeout(() => { draftTimer = null; API.putDraft(S.draft).catch(() => {}); }, 500);
  }
  // Closing the window with a save still pending: send it now.
  window.addEventListener("pagehide", () => {
    if (S.mode !== "live" || !draftTimer) return;
    clearTimeout(draftTimer); draftTimer = null;
    fetch("api/draft", { method: "PUT", keepalive: true, headers: { "Content-Type": "application/json" }, body: JSON.stringify(S.draft) }).catch(() => {});
  });
  const answered = (q) => { const a = S.draft.answers[q.id]; return !!(a && String(a.answer || "").trim()); };
  const nAnswered = () => S.Q.questions.filter(answered).length;
  function payload() {
    return {
      version: 1, created: new Date().toISOString(), consent: S.draft.consent,
      answers: S.Q.questions.filter(answered).map((q) => {
        const o = { id: q.id, section: q.section, type: q.type, question: q.question, answer: String(S.draft.answers[q.id].answer).trim() };
        if (q.repeat_of) o.repeat_of = q.repeat_of;
        return o;
      }),
    };
  }
  function importAnswers(blob) {
    if (!blob || !Array.isArray(blob.answers)) throw new Error("That file has no answers list.");
    const byId = {}; S.Q.questions.forEach((q) => (byId[q.id] = q));
    let n = 0;
    blob.answers.forEach((a) => {
      const q = byId[a.id]; if (!q || !String(a.answer || "").trim()) return;
      let raw = null;
      if (q.type === "likert") { const m = /\((\d) of 5\)/.exec(a.answer); raw = m ? +m[1] : null; }
      if (q.type === "choice") raw = (q.options || []).indexOf(a.answer);
      S.draft.answers[q.id] = { answer: a.answer, raw }; n++;
    });
    if (blob.consent && blob.consent.name) S.draft.consent = blob.consent;
    saveDraft();
    return n;
  }

  // ---------- shell ----------
  const STEPS = [["consent", "Consent"], ["interview", "Interview"], ["voice", "Voice"], ["photo", "Photo"], ["build", "Build"]];
  const TABS = [["home", "Home"], ["ask", "Ask"], ["guess", "Guess me"], ["decide", "What would I do?"], ["teach", "Tell it about you"], ["profile", "Profile"], ["results", "Results"], ["settings", "Settings"]];
  const hasVoice = () => !!(S.status && S.status.has_voice);
  const hasPhoto = () => !!(S.status && S.status.has_photo);
  function stepDone(v) {
    if (v === "consent") return !!S.draft.consent;
    if (v === "interview") return nAnswered() >= 20;
    if (v === "voice") return hasVoice();
    if (v === "photo") return hasPhoto();
    if (v === "build") return !!(S.status && S.status.built);
    return false;
  }
  function renderRail() {
    const rail = $("#rail"); rail.textContent = "";
    const built = S.status && S.status.built;
    const list = built ? TABS : STEPS;
    list.forEach(([v, label], i) => {
      const locked = !built && ((v !== "consent" && !S.draft.consent) || (v === "build" && nAnswered() < 20));
      rail.append(h("button", { type: "button", "aria-current": S.view === v ? "page" : null, disabled: locked, onclick: () => go(v) },
        built ? null : h("span", { class: "step-n" + (stepDone(v) ? " ok" : "") }, stepDone(v) ? "✓" : i + 1), label));
    });
    const chip = $("#modelChip"), txt = $("#modelText");
    chip.className = "chip";
    if (S.mode === "preview") { chip.classList.add("preview"); txt.textContent = "Preview · simulated"; }
    else if (!S.status) txt.textContent = "Connecting";
    else if (S.status.llm.state !== "ready") { chip.classList.add("loading"); txt.textContent = "Loading model"; }
    else if (S.status.llm.stand_in) { chip.classList.add("preview"); txt.textContent = S.status.llm.name; chip.title = S.status.llm.error || ""; }
    else if (S.status.online) { chip.classList.add("live", "online"); txt.textContent = "Online · Gemini"; chip.title = `Answers come from Google Gemini. If it can't answer, ${S.status.llm.name} answers on this ${DEVICE}.`; }
    else { chip.classList.add("live"); txt.textContent = S.status.llm.name; }
    paintJobs();
  }
  function go(v) {
    S.view = v; renderRail();
    document.body.classList.toggle("on-home", v === "home");
    document.querySelectorAll("#view video").forEach((x) => x.pause());   // nothing keeps talking from a page you left
    const el = $("#view"); el.textContent = "";
    (VIEWS[v] || VIEWS.consent)(el);
    window.scrollTo({ top: 0 });
    if (location.hash.slice(1) !== v) history.replaceState(null, "", "#" + v);
  }
  function installLink(row, label) {
    const u = S.status && S.status.bootstrap_url;
    if (!u) return null;
    return h("a", { class: "btn small quiet", href: u + "/#install=" + row }, label || "Install");
  }

  // ---------- views ----------
  const VIEWS = {};

  VIEWS.consent = function (el) {
    const wrap = h("div", { class: "narrow stack" });
    wrap.append(
      h("section", { class: "hero" },
        h("p", { class: "eyebrow" }, "Deferral-aware digital twin"),
        h("h1", null, "A twin that answers like you, and says “I don’t know” when it should."),
        h("p", null, "Answer about 110 questions, record your voice, take a photo, and press build. The twin answers only from what you told it. When nothing you said is close enough, it refuses instead of inventing an answer.")),
      h("div", { class: "tiers-key" },
        h("div", { class: "tk conf" }, h("b", null, "Answers"), "Close to something you said (similarity ≥ 0.35)."),
        h("div", { class: "tk inf" }, h("b", null, "Infers"), "Reasons from your profile, and says it is guessing (0.15 to 0.35)."),
        h("div", { class: "tk def" }, h("b", null, "Refuses"), "Nothing close (below 0.15). It says “I don’t know” and never calls the model.")));
    const p = h("section", { class: "panel stack", "aria-labelledby": "cons-h" }, h("h2", { id: "cons-h" }, "Consent"),
      h("p", { class: "muted small" }, `Nothing goes further without all three. Everything stays on this ${DEVICE}.`));
    const boxes = CONSENT_TEXT.map((t, i) => { const c = h("input", { type: "checkbox", id: "c" + i }); c.checked = !!S.draft.consent; return [c, h("label", { class: "check", for: "c" + i }, c, h("span", null, t))]; });
    const name = h("input", { type: "text", id: "cname", autocomplete: "name", placeholder: "Your full name" });
    name.value = (S.draft.consent && S.draft.consent.name) || "";
    const start = h("button", { class: "btn", type: "button" }, S.draft.consent ? "Continue the interview" : "Agree and start");
    const ready = () => (start.disabled = !(boxes.every((b) => b[0].checked) && name.value.trim().length > 1));
    boxes.forEach((b) => { b[0].addEventListener("change", ready); p.append(b[1]); });
    name.addEventListener("input", ready);
    start.addEventListener("click", () => {
      S.draft.consent = { name: name.value.trim(), agreed: CONSENT_SAVED.slice(), timestamp: (S.draft.consent && S.draft.consent.timestamp) || new Date().toISOString() };
      saveDraft(); go("interview");
    });
    ready();
    p.append(h("label", { class: "field", for: "cname" }, "Your full name", name),
      h("div", { class: "row" }, start, importButton()),
      h("p", { class: "muted small" }, "Don’t include passwords, ID numbers, health details or private information about other people. Your voice and face are used only for your own twin, and Settings can delete everything at any time."),
      h("p", { class: "muted small" }, ONLINE_CONSENT));
    wrap.append(p);
    if (S.mode === "preview") wrap.append(previewNote());
    el.append(wrap);
  };

  function previewNote() {
    return h("div", { class: "banner" }, h("b", null, "Preview. "), "No twin is connected, so answers here are simulated with simple word matching in your browser. The desktop app runs the real twin with Phi-3 on MLX, your cloned voice and the avatar.");
  }

  function importButton() {
    const inp = h("input", { type: "file", accept: ".json,application/json", hidden: true, id: "importFile" });
    inp.addEventListener("change", async () => {
      try { const n = importAnswers(JSON.parse(await inp.files[0].text())); toast(`Imported ${n} answers.`); go(S.draft.consent ? "interview" : "consent"); }
      catch (e) { toast("Couldn’t import: " + e.message); }
    });
    return h("span", null, inp, h("button", { class: "btn ghost", type: "button", onclick: () => inp.click() }, "Import saved answers"));
  }

  VIEWS.interview = function (el) {
    if (!S.draft.consent) return go("consent");
    const secs = S.Q.sections, Qs = S.Q.questions;
    const inSec = (i) => Qs.filter((q) => q.section === secs[i].title);
    const nav = h("nav", { class: "secnav", "aria-label": "Sections" });
    const overall = h("div", { class: "overall" });
    function paintNav() {
      nav.textContent = "";
      const d = nAnswered();
      overall.textContent = "";
      overall.append(h("span", { class: "eyebrow" }, "Answered"), h("span", { class: "big num" }, d, h("span", { class: "muted", style: "font-size:1rem" }, " / " + Qs.length)),
        h("div", { class: "meter" }, h("i", { style: `width:${(100 * d) / Qs.length}%` })),
        h("span", { class: "muted small" }, d >= 20 ? "Enough to build. More answers make a better twin." : `${20 - d} more before you can build.`));
      nav.append(overall);
      secs.forEach((s, i) => {
        const qs = inSec(i), n = qs.filter(answered).length;
        nav.append(h("button", { type: "button", "aria-current": i === S.sec ? "true" : "false", onclick: () => { S.sec = i; setPos(null); go("interview"); } },
          h("span", { class: "sn-top" }, h("span", null, s.title), h("span", { class: "num muted small" }, n + "/" + qs.length)),
          h("span", { class: "meter" }, h("i", { style: `width:${(100 * n) / qs.length}%` }))));
      });
      renderRail();
    }
    const body = h("div", { class: "stack" });
    const sec = secs[S.sec];
    body.append(h("div", { class: "sec-head" }, h("span", { class: "eyebrow" }, `Section ${S.sec + 1} of ${secs.length}`), h("h2", null, sec.title), h("p", { class: "muted" }, sec.note)));
    inSec(S.sec).forEach((q) => body.append(renderQ(q, () => { card(q); paintNav(); })));
    function card(q) { const c = body.querySelector(`[data-id="${q.id}"]`); if (c) c.classList.toggle("done", answered(q)); }
    const last = S.sec === secs.length - 1;
    const built = S.status && S.status.built;
    body.append(h("div", { class: "row" },
      S.sec > 0 ? h("button", { class: "btn quiet", type: "button", onclick: () => { S.sec--; setPos(null); go("interview"); } }, "Previous section") : null,
      h("span", { class: "spacer" }),
      last ? h("button", { class: "btn", type: "button", onclick: () => (nAnswered() >= 20 ? go(built ? "build" : "voice") : toast("Answer at least 20 questions first.")) }, built ? "Rebuild the twin" : "Next: your voice")
           : h("button", { class: "btn", type: "button", onclick: () => { S.sec++; setPos(null); go("interview"); } }, "Next section")));
    const tools = h("div", { class: "row small muted" }, "Every answer is saved as you type. Close the app at any time and you come back to this question.", importButton());
    if (S.mode === "preview") tools.append(h("button", { class: "btn ghost small", type: "button", onclick: fillSample }, "Fill with sample answers"));
    body.append(tools);
    el.append(h("div", { class: "iv" }, nav, body));
    paintNav();
    const pos = S.draft.pos;
    if (pos && pos.q && pos.sec === S.sec) {
      const c = body.querySelector(`[data-id="${pos.q}"]`);
      if (c) requestAnimationFrame(() => { c.scrollIntoView({ block: "center" }); const f = c.querySelector("textarea, input:checked, input"); if (f) f.focus({ preventScroll: true }); });
    }
  };
  function setPos(qid) { S.draft.pos = { sec: S.sec, q: qid }; saveDraft(); }

  function renderQ(q, onChange) {
    const cur = S.draft.answers[q.id];
    const c = h("div", { class: "q" + (answered(q) ? " done" : ""), "data-id": q.id },
      h("div", { class: "q-top" }, h("span", { class: "q-id" }, q.id), h("span", { class: "q-text" }, q.question)));
    const set = (answer, raw) => { S.draft.answers[q.id] = { answer, raw }; S.draft.pos = { sec: S.sec, q: q.id }; saveDraft(); onChange(); };
    c.addEventListener("focusin", () => { if (!S.draft.pos || S.draft.pos.q !== q.id) setPos(q.id); });
    if (q.type === "text") {
      const ta = h("textarea", { id: "in-" + q.id, "aria-label": q.question, rows: 3 });
      ta.value = cur ? cur.answer : "";
      ta.addEventListener("input", () => set(ta.value, null));
      c.append(ta);
    } else if (q.type === "choice") {
      const box = h("div", { class: "opts", role: "radiogroup", "aria-label": q.question });
      q.options.forEach((o, i) => {
        const inp = h("input", { type: "radio", name: "n-" + q.id, id: `in-${q.id}-${i}` });
        inp.checked = !!cur && cur.answer === o;
        inp.addEventListener("change", () => set(o, i));
        box.append(h("label", { class: "opt" }, inp, h("span", null, o)));
      });
      c.append(box);
    } else {
      const L = S.Q.likert_labels, row = h("div", { class: "opts", role: "radiogroup", "aria-label": q.question });
      for (let v = 1; v <= 5; v++) {
        const inp = h("input", { type: "radio", name: "n-" + q.id, id: `in-${q.id}-${v}`, "aria-label": L[v - 1] });
        inp.checked = !!cur && cur.raw === v;
        inp.addEventListener("change", () => set(`${L[v - 1]} (${v} of 5)`, v));
        row.append(h("label", { class: "opt", title: L[v - 1] }, inp, h("span", null, v)));
      }
      c.append(h("div", { class: "likert" }, row, h("div", { class: "ends" }, h("span", null, "Strongly disagree"), h("span", null, "Strongly agree"))));
    }
    return c;
  }

  function fillSample() {
    const T = {
      q001: "Hyderabad, though I've lived in Bengaluru for college.", q002: "AI and Data Science. I like building things that learn from data, and it felt like the most open field.",
      q003: "Telugu, Hindi and English. I think in Telugu, but anything technical I think in English.", q004: "Check my phone, then drink water and make tea.",
      q005: "Classes in the morning, lab work after lunch, then a few hours coding on my project in the evening. I usually sleep after midnight.",
      q006: "Hyderabadi biryani. I never eat bitter gourd.", q007: "Telugu film songs and lo-fi while I work.", q008: "Interstellar, because the science and the father-daughter story both hit hard.",
      q009: "I play badminton and follow cricket.", q010: "Long walks with music, or cooking something new.", q011: "Japan, for the trains, food and how organised everything is.", q012: "Docker and deploying models properly.",
      q021: "I usually learn by building something myself.",
      q026: "I'd be a bit annoyed but I'd just reschedule and use the time to work on my project.",
      q027: "I'd message them once to ask what's done, then just do their part myself so the team doesn't lose marks. I'd talk to them after.",
      q028: "I'd go to the teacher after class with my answer sheet and ask politely what I missed.",
      q029: "I'd fix it straight away and tell whoever is using it, even if it's embarrassing.",
      q035: "I'd ask to talk after the group and calmly say it was my idea. I don't like scenes.",
      q034: "I'd fix it the same night, even if it means no sleep.",
      q038: "I'd take a break, go for a walk, then explain the problem to a friend or search for a similar case.",
      q036: "I'd say I'd help them understand it instead, but I won't give my file.",
      q088: "Late breakfast, badminton with friends, then a few quiet hours building something.",
      q089: "Getting my digital twin project working end to end, with voice and avatar.",
      q090: "People who don't reply for days and then expect things immediately.",
      q091: "I say I don't know and then go and find out.", q093: "Machine learning and how models actually learn.",
    };
    const LV = [4, 3, 5, 2, 4, 4, 3, 2, 4, 5, 3, 4];
    S.Q.questions.forEach((q) => {
      if (answered(q)) return;
      if (q.type === "text" && T[q.id]) S.draft.answers[q.id] = { answer: T[q.id], raw: null };
      const n = parseInt(q.id.slice(1), 10);
      if (q.type === "likert" && q.id[0] === "q") { const v = LV[n % LV.length]; S.draft.answers[q.id] = { answer: `${S.Q.likert_labels[v - 1]} (${v} of 5)`, raw: v }; }
      if (q.type === "choice" && q.id[0] === "q") { const i = n % q.options.length; S.draft.answers[q.id] = { answer: q.options[i], raw: i }; }
    });
    saveDraft(); toast("Sample answers filled in. They are examples, not yours."); go("interview");
  }

  // ---------- voice ----------
  function resultLines(res, okText) {
    const out = [];
    (res.problems || []).forEach((w) => out.push(h("p", { class: "bad-line" }, "✕ " + w)));
    (res.warnings || []).forEach((w) => out.push(h("p", { class: "warn" }, w)));
    if (res.ok && okText) out.unshift(h("p", { class: "okline" }, "✓ " + okText));
    return out;
  }

  VIEWS.voice = function (el) {
    const wrap = h("div", { class: "narrow stack" });
    const result = h("div", { class: "stack" });
    const saved = h("div", { class: "stack" });
    const recBtn = h("button", { class: "btn", type: "button" }, "● Record");
    const timer = h("span", { class: "num muted" }, "0:00");
    const level = h("div", { class: "level", "aria-hidden": "true" }, h("i"));
    const changing = !!(S.status && S.status.built);                                           // after the build: Settings → Change voice
    const nextBtn = h("button", { class: "btn", type: "button", onclick: () => go(changing ? "settings" : "photo") });
    const fileInp = h("input", { type: "file", accept: "audio/*,video/*,.m4a,.aac,.ogg,.wav,.mp3,.caf", hidden: true });
    wrap.append(
      h("div", { class: "sec-head" }, h("span", { class: "eyebrow" }, changing ? "Change your voice" : "Step 3 of 5 · optional"), h("h2", null, "Your voice"),
        h("p", { class: "muted" }, "Read the paragraph below aloud, at your normal pace. It takes about 45 seconds. The twin uses it only to speak in your voice; it doesn’t change what it knows.")),
      h("section", { class: "panel stack" },
        h("blockquote", { class: "read" }, READ_ALOUD),
        h("ul", { class: "guide" }, h("li", null, "A quiet room, about 20 cm from the microphone"), h("li", null, "Your own voice only, no music or TV")),
        h("div", { class: "row" }, recBtn, level, timer, h("span", { class: "spacer" }), fileInp, h("button", { class: "btn ghost", type: "button", onclick: () => fileInp.click() }, "Choose a file instead")),
        result),
      saved,
      changing ? h("p", { class: "small muted" }, "A new clip replaces the old one only if it passes the checks. Cached spoken answers and videos, and the greeting, are made again in the new voice.") : null,
      h("div", { class: "row" }, h("button", { class: "btn quiet", type: "button", onclick: () => go(changing ? "settings" : "interview") }, "Back"), h("span", { class: "spacer" }),
        nextBtn));
    el.append(wrap);
    paintSaved();
    function paintSaved() {
      nextBtn.textContent = changing ? "Done" : hasVoice() ? "Next: your photo" : "Skip: a twin without a voice";
      saved.textContent = "";
      if (hasVoice() && S.mode === "live") saved.append(h("section", { class: "panel stack" }, h("p", { class: "okline" }, "✓ Your voice clip is saved. Play it back:"),
        h("audio", { controls: true, src: "api/upload/voice?t=" + Date.now() })));
    }
    async function send(file) {
      result.textContent = ""; result.append(h("p", { class: "muted small" }, "Checking the recording…"));
      try {
        const res = await API.upload("voice", file);
        result.textContent = "";
        result.append(...resultLines(res, `${Math.round(res.seconds)} seconds, clear and at a good level.`));
        if (!res.ok) result.append(h("p", { class: "small muted" }, "Not saved. Record again, or choose another file."));
        await refresh(); paintSaved();
      } catch (e) { result.textContent = ""; result.append(h("p", { class: "bad-line" }, e.message)); }
    }
    fileInp.addEventListener("change", () => fileInp.files[0] && send(fileInp.files[0]));

    let rec = null;
    recBtn.addEventListener("click", async () => {
      if (rec) return rec.stop();
      if (S.mode === "live" && !(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder)) return pythonRecord();
      try { rec = await startRecording(); } catch (e) {
        if (S.mode === "live") return pythonRecord(e);
        return toast("The microphone isn’t available here: " + e.message);
      }
    });
    async function startRecording() {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
      const type = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"].find((t) => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t)) || "";
      const mr = new MediaRecorder(stream, type ? { mimeType: type } : undefined), chunks = [];
      const ctx = new (window.AudioContext || window.webkitAudioContext)(), an = ctx.createAnalyser();
      ctx.createMediaStreamSource(stream).connect(an); an.fftSize = 512; const buf = new Uint8Array(an.fftSize);
      const t0 = Date.now(); let raf;
      const tick = () => { an.getByteTimeDomainData(buf); let m = 0; for (const v of buf) m = Math.max(m, Math.abs(v - 128)); level.firstChild.style.width = Math.min(100, (m / 128) * 140) + "%";
        level.classList.toggle("hot", m > 120); const s = Math.floor((Date.now() - t0) / 1000); timer.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; if (s >= 120) mr.stop(); raf = requestAnimationFrame(tick); };
      mr.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      mr.onstop = () => {
        cancelAnimationFrame(raf); stream.getTracks().forEach((t) => t.stop()); ctx.close(); rec = null;
        recBtn.textContent = "● Record again"; recBtn.classList.remove("rec"); level.firstChild.style.width = "0";
        const ext = (mr.mimeType || type).includes("mp4") ? "m4a" : "webm";
        send(new File([new Blob(chunks, { type: mr.mimeType || type })], "recording." + ext));
      };
      mr.start(500); tick();
      recBtn.textContent = "■ Stop"; recBtn.classList.add("rec");
      return mr;
    }
    async function pythonRecord(why) {
      // The window couldn't reach the microphone; record through the app itself instead.
      try {
        await API.captureVoiceStart();
        const t0 = Date.now();
        recBtn.textContent = "■ Stop"; recBtn.classList.add("rec");
        const iv = setInterval(() => { const s = Math.floor((Date.now() - t0) / 1000); timer.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; }, 250);
        rec = { stop: async () => { clearInterval(iv); rec = null; recBtn.textContent = "● Record again"; recBtn.classList.remove("rec");
          result.textContent = ""; result.append(h("p", { class: "muted small" }, "Checking the recording…"));
          try { const res = await API.captureVoiceStop(); result.textContent = ""; result.append(...resultLines(res, `${Math.round(res.seconds)} seconds, clear and at a good level.`)); await refresh(); paintSaved(); }
          catch (e) { result.textContent = ""; result.append(h("p", { class: "bad-line" }, e.message)); } } };
      } catch (e) { toast((why ? "The microphone isn’t available in this window. " : "") + e.message); }
    }
  };

  // ---------- photo ----------
  VIEWS.photo = function (el) {
    const wrap = h("div", { class: "narrow stack" });
    const stage = h("div", { class: "cam" });
    const result = h("div", { class: "stack" });
    const fileInp = h("input", { type: "file", accept: "image/*,.heic,.heif", hidden: true });
    const camBtn = h("button", { class: "btn", type: "button" }, "Use the camera");
    const changing = !!(S.status && S.status.built);                                           // after the build: Settings → Change photo
    const nextBtn = h("button", { class: "btn", type: "button", onclick: () => { stopCam(); go(changing ? "settings" : "build"); } });
    const paintNext = () => (nextBtn.textContent = changing ? "Done" : hasPhoto() ? "Next: build" : "Skip: a twin without a face");
    paintNext();
    const snapBtn = h("button", { class: "btn", type: "button", hidden: true }, "Take the photo");
    wrap.append(
      h("div", { class: "sec-head" }, h("span", { class: "eyebrow" }, changing ? "Change your photo" : "Step 4 of 5 · optional"), h("h2", null, "Your photo"),
        h("p", { class: "muted" }, "The avatar is animated from one photo. It is checked straight away, because a photo with the mouth cut off produces no lip movement at all.")),
      h("section", { class: "panel stack" },
        h("ul", { class: "guide" }, h("li", null, "Face the camera, whole face in the frame, with space below your chin"), h("li", null, "Mouth closed and relaxed, nothing covering it"), h("li", null, "Even light from the front, no strong shadows")),
        stage,
        h("div", { class: "row" }, camBtn, snapBtn, h("span", { class: "spacer" }), fileInp, h("button", { class: "btn ghost", type: "button", onclick: () => fileInp.click() }, "Choose a file instead")),
        result),
      changing ? h("p", { class: "small muted" }, "A new photo replaces the old one only if it passes the checks. Cached avatar videos and the greeting are made again with the new face.") : null,
      h("div", { class: "row" }, h("button", { class: "btn quiet", type: "button", onclick: () => { stopCam(); go(changing ? "settings" : "voice"); } }, "Back"), h("span", { class: "spacer" }),
        nextBtn));
    el.append(wrap);
    if (hasPhoto() && S.mode === "live") showImage("api/upload/photo?t=" + Date.now(), null, "Your saved photo");

    function showImage(src, res, alt) {
      stage.textContent = "";
      const box = h("div", { class: "photo-box" }, h("img", { src, alt: alt || "Your photo" }));
      const f = res && res.face;
      if (f && res.width) box.append(h("span", { class: "facebox" + (res.ok ? "" : " bad"), style: `left:${(100 * f[0]) / res.width}%;top:${(100 * f[1]) / res.height}%;width:${(100 * f[2]) / res.width}%;height:${(100 * f[3]) / res.height}%` }));
      stage.append(box);
    }
    async function send(file) {
      result.textContent = ""; result.append(h("p", { class: "muted small" }, "Checking the photo…"));
      try { show(await API.upload("photo", file), file); } catch (e) { result.textContent = ""; result.append(h("p", { class: "bad-line" }, e.message)); }
    }
    async function show(res, file) {
      showImage(S.mode === "live" ? "api/upload/photo?candidate=1&t=" + Date.now() : URL.createObjectURL(file), res);
      result.textContent = "";
      result.append(...resultLines(res, "One face, the whole face in frame, mouth visible."));
      if (!res.ok) result.append(h("p", { class: "small muted" }, "Not saved. Take another, or choose a different photo."));
      await refresh(); paintNext();
    }
    fileInp.addEventListener("change", () => fileInp.files[0] && send(fileInp.files[0]));

    let stream = null;
    function stopCam() { if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; } }
    camBtn.addEventListener("click", async () => {
      if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)) return pythonPhoto();
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 960 } } });
      } catch (e) { return S.mode === "live" ? pythonPhoto(e) : toast("The camera isn’t available here: " + e.message); }
      const v = h("video", { autoplay: true, playsinline: true, muted: true, class: "mirror" }); v.srcObject = stream;
      stage.textContent = ""; stage.append(h("div", { class: "photo-box live" }, v, h("span", { class: "oval", "aria-hidden": "true" })));
      camBtn.hidden = true; snapBtn.hidden = false;
      snapBtn.onclick = () => {
        const c = document.createElement("canvas"); c.width = v.videoWidth; c.height = v.videoHeight;
        c.getContext("2d").drawImage(v, 0, 0);
        stopCam(); snapBtn.hidden = true; camBtn.hidden = false; camBtn.textContent = "Take another";
        c.toBlob((b) => send(new File([b], "camera.jpg", { type: "image/jpeg" })), "image/jpeg", 0.93);
      };
    });
    async function pythonPhoto(why) {
      result.textContent = ""; result.append(h("p", { class: "muted small" }, `Taking a photo with the ${DEVICE}’s camera…`));
      try { show(await API.capturePhoto()); } catch (e) { result.textContent = ""; result.append(h("p", { class: "bad-line" }, (why ? "The camera isn’t available in this window. " : "") + e.message)); }
    }
  };

  // ---------- build ----------
  VIEWS.build = function (el) {
    const wrap = h("div", { class: "narrow stack" });
    const d = nAnswered(), b = S.status && S.status.build;
    wrap.append(h("div", { class: "sec-head" }, h("span", { class: "eyebrow" }, "Step 5 of 5"), h("h2", null, "Build your twin"),
      h("p", { class: "muted" }, `${d} answers${hasVoice() ? ", your voice" : ""}${hasPhoto() ? ", your photo" : ""}. Nothing is trained: your answers become the twin’s memory, and a profile of how you tend to be is computed from them.`)));
    const list = h("ol", { class: "stages" });
    const panel = h("div", { class: "panel stack" }, list);
    const btn = h("button", { class: "btn", type: "button" }, "Build my twin");
    const err = h("div");
    const clock = h("span", { class: "muted small num" });
    panel.append(err, h("div", { class: "row" }, btn, clock));
    wrap.append(panel);
    el.append(wrap);
    function paint(bs) {
      list.textContent = "";
      (bs.stages || []).forEach((label, i) => {
        const n = i + 1;
        const state = bs.state === "done" || n <= bs.stage ? "ok" : bs.state === "running" && n === bs.stage + 1 ? "now" : "";
        list.append(h("li", { class: state }, h("span", { class: "ic" }, state === "ok" ? "✓" : ""), label));
      });
      err.textContent = "";
      if (bs.state === "error") err.append(h("div", { class: "stack" }, h("div", { class: "banner bad" }, h("b", null, "The build stopped. "), bs.error), bs.log && bs.log.length ? h("pre", { class: "log" }, bs.log.slice(-12).join("\n")) : null));
      btn.disabled = bs.state === "running";
      btn.textContent = bs.state === "running" ? "Building…" : bs.state === "done" ? "Talk to your twin" : bs.state === "error" ? "Try again" : "Build my twin";
      clock.textContent = bs.started ? `${Math.round(((bs.finished || Date.now() / 1000) - bs.started))} s` : "";
    }
    paint(b && b.stages ? b : { state: "idle", stage: 0, stages: ["Saving your interview", "Splitting answers into memory and held-out questions", "Preparing your voice and photo", "Connecting voice and avatar", "Saving everything to disk", "Embedding the twin's memory", "Loading the language model"] });
    async function poll() {
      for (;;) {
        let bs; try { bs = await API.buildStatus(); } catch (e) { await sleep(1500); continue; }
        if (S.view !== "build") return;
        paint(bs);
        if (bs.state !== "running") { await refresh(); if (bs.state === "done") { toast("Your twin is ready."); go("ask"); } return; }
        await sleep(500);
      }
    }
    btn.addEventListener("click", async () => {
      if (btn.textContent === "Talk to your twin") return go("ask");
      if (nAnswered() < 20) return toast("Answer at least 20 questions first.");
      try { paint(await API.build(payload())); poll(); } catch (e) { toast(e.message); }
    });
    if (b && b.state === "running") poll();
  };

  // ---------- home: the opening page ----------
  const HOME_THEMES = [["red", "Signal red"], ["cobalt", "Cobalt"], ["emerald", "Emerald"], ["night", "Midnight"]];
  const HOME_ASKS = ["What do you do to relax?", "How do you handle stress?", "What is your favourite food?", "What can you do?",
    "Your train is cancelled an hour before an exam. What do you do?", "What would you do if you won the lottery?",
    "What time is it?", "What is your passport number?", "I am feeling sad now"];
  const firstName = (n) => { const w = (n || "").split(/\s+/).filter(Boolean), r = w.filter((x) => x.replace(/\./g, "").length > 1); const f = (r[0] || w[0] || ""); return f ? f[0].toUpperCase() + f.slice(1) : ""; };   // skips initials
  const titleCase = (n) => (n || "").split(/\s+/).filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");

  function wipeTo(view, from) {                                         // a circle of the next page grows from the button
    const r = from ? from.getBoundingClientRect() : { left: innerWidth / 2, top: innerHeight / 2, width: 0, height: 0 };
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return go(view);
    const wipe = h("div", { class: "wipe", style: `--x:${r.left + r.width / 2}px;--y:${r.top + r.height / 2}px` });
    document.body.append(wipe);
    setTimeout(() => { go(view); wipe.classList.add("done"); setTimeout(() => wipe.remove(), 450); }, 520);
  }

  // The landing page's character: real head turns rendered offline by LivePortrait from the reference
  // reel's front-facing frame (tools/liveportrait/render_grid.py, finish_frames.py). Yaw -20..+20 in 1
  // degree steps x pitch -10..+10 in 2.5 degree steps, plus half-closed and closed eyes on a 5 degree grid.
  // A spring-smoothed cursor picks the pose; neighbouring yaw frames cross-fade on a canvas.
  const LP_DIR = "character/lp/";
  let lpManifest = null;
  const lpName = (yaw, pitch, eyes) => `y${yaw < 0 ? "-" : "+"}${String(Math.abs(yaw)).padStart(2, "0")}_p${pitch < 0 ? "-" : "+"}${Math.abs(pitch).toFixed(1).padStart(4, "0")}_${eyes || "open"}`;

  // Frames are fetched once, kept compressed, and decoded off the main thread by a small pool of
  // workers (createImageBitmap there), so nothing decodes on the main thread during motion.
  const LP_WORKERS = 3;
  let lpPool = null;
  const lpPending = new Map();
  function lpDecoder() {
    if (lpPool !== null) return lpPool.length > 0;
    lpPool = [];
    try {
      const src = `const blobs = new Map();
        self.onmessage = async (e) => { const { id, url } = e.data;
          try { let b = blobs.get(url); if (!b) { const r = await fetch(url); if (!r.ok) throw new Error(r.status); b = await r.blob(); blobs.set(url, b); }
            const bm = await createImageBitmap(b); self.postMessage({ id, bm }, [bm]); }
          catch (err) { self.postMessage({ id, err: String(err) }); } };`;
      const url = URL.createObjectURL(new Blob([src], { type: "text/javascript" }));
      for (let i = 0; i < LP_WORKERS; i++) {
        const w = new Worker(url); w.busy = 0;
        w.onmessage = (e) => { w.busy--; const p = lpPending.get(e.data.id); if (p) { lpPending.delete(e.data.id); e.data.bm ? p.ok(e.data.bm) : p.fail(e.data.err); } };
        lpPool.push(w);
      }
    } catch (e) { lpPool = []; }
    return lpPool.length > 0;
  }
  let lpSeq = 0;
  async function lpBitmap(path) {
    const url = new URL(path, location.href).href;
    if (lpDecoder()) return new Promise((ok, fail) => { const id = ++lpSeq; lpPending.set(id, { ok, fail });
      const w = lpPool.reduce((a, b) => (b.busy < a.busy ? b : a)); w.busy++; w.postMessage({ id, url }); });
    return createImageBitmap(await (await fetch(url)).blob());                              // fallback: no worker
  }

  function character(stage, opts) {
    const canvas = h("canvas", { "aria-hidden": "true" });
    const wrap = h("div", { class: "ch chr", role: "img", "aria-label": "Cartoon character" }, h("div", { class: "chr-in" }, canvas));
    stage.append(wrap);
    const ctx = canvas.getContext("2d");
    const bitmaps = new Map(), decoding = new Set(), arrived = new Map();
    const small = new Map();                                                                   // every open pose, one-third size, always decoded
    const MAX_DECODED = 64;
    let M = null, rest = null, iris = null, dirty = true;
    let tx = 0, ty = 0, x = 0, y = 0, vx = 0, vy = 0;                                          // head: spring state
    let gtx = 0, gty = 0, gx = 0, gy = 0, gvx = 0, gvy = 0;                                    // eyes: a faster spring
    let last = performance.now(), lastMove = performance.now(), cursor = null, lastSig = "";
    let blink = null, nextBlink = performance.now() + 2500;
    const decode = (n) => {
      if (!M || bitmaps.has(n) || decoding.has(n) || !(n in M.eyes) || decoding.size >= 9) return;   // a few in flight at a time
      decoding.add(n); const t0 = performance.now();
      lpBitmap(LP_DIR + "f/" + n + ".webp").then((bm) => {
        bitmaps.set(n, bm); arrived.set(n, performance.now()); dirty = true;
        wrap.dataset.dec = String((+wrap.dataset.dec || 0) + 1); wrap.dataset.decms = String(Math.round(performance.now() - t0));
        while (bitmaps.size > MAX_DECODED) { const old = bitmaps.keys().next().value; bitmaps.get(old).close?.(); bitmaps.delete(old); }
      }).catch(() => {}).finally(() => decoding.delete(n));
    };
    const use = (n) => { const bm = bitmaps.get(n); if (bm) { bitmaps.delete(n); bitmaps.set(n, bm); } else decode(n); return bm || null; };
    // The cursor across the whole window maps to the full range, eased so small moves near the face show.
    const ease = (v) => Math.sign(v) * (1 - Math.pow(1 - Math.min(1, Math.abs(v)), 2.2));
    const look = (cx, cy) => {
      const r = wrap.getBoundingClientRect(), hx = r.left + r.width * 0.5, hy = r.top + r.height * 0.40;
      const dx = cx - hx, dy = cy - hy;
      tx = ease(dx / Math.max(40, dx >= 0 ? innerWidth - hx : hx)); ty = ease(dy / Math.max(40, dy >= 0 ? innerHeight - hy : hy));
      cursor = { x: cx, y: cy }; lastMove = performance.now();
    };
    // Critically damped spring, solved exactly (stable at any frame rate). w = 5.8 / settle time.
    const spring = (pos, vel, target, w, dt) => {
      const d = pos - target, c2 = vel + w * d, e = Math.exp(-w * dt);
      return [target + (d + c2 * dt) * e, (c2 - w * (d + c2 * dt)) * e];
    };

    (async () => {
      try { M = lpManifest || (lpManifest = await (await fetch(LP_DIR + "frames.json")).json()); } catch (e) { return; }
      const [W, H] = M.size;
      canvas.width = W; canvas.height = H;
      wrap.style.aspectRatio = `${W} / ${H}`;
      wrap.style.maxHeight = (H / (window.devicePixelRatio || 1)) + "px";                    // never larger than native on Retina
      try { [rest, iris] = await Promise.all([lpBitmap(LP_DIR + "rest.webp"), lpBitmap(LP_DIR + M.iris.file)]); } catch (e) { return; }
      dirty = true;
      M.yaws.forEach((yw) => Math.abs(yw) <= 4 && [0, 2.5, -2.5].forEach((p) => decode(lpName(yw, p))));   // around rest first
      if (M.small && !opts.lite) {                                                            // then the small copies, nearest rest first
        const names = Object.keys(M.eyes).filter((n) => n.endsWith("_open"))
          .sort((a, b) => { const k = (n) => { const m = /^y([+-]\d+)_p([+-][\d.]+)/.exec(n); return Math.abs(+m[1]) + Math.abs(+m[2]); }; return k(a) - k(b); });
        let i = 0;
        const next = () => { if (i >= names.length || !wrap.isConnected) return; const n = names[i++];
          lpBitmap(LP_DIR + M.small.dir + n + ".webp").then((bm) => { small.set(n, bm); dirty = true; }).catch(() => {}).finally(next); };
        for (let k = 0; k < 3; k++) next();
      }
    })();

    const nearest = (arr, v) => arr.reduce((a, b) => (Math.abs(b - v) < Math.abs(a - v) ? b : a));
    const bracket = (arr, v) => { let i = 0; while (i < arr.length - 2 && arr[i + 1] <= v) i++; const t = (v - arr[i]) / (arr[i + 1] - arr[i]); return [arr[i], arr[i + 1], Math.max(0, Math.min(1, t))]; };
    const blendEyes = (parts) => {                                                             // [[name, weight]] -> two eyes or []
      const out = [];
      for (let k = 0; k < 2; k++) {
        let wsum = 0, cx = 0, cy = 0, ir = 0; const r = new Array(32).fill(0);
        for (const [n, w] of parts) { const e = (M.eyes[n] || [])[k]; if (!e || !e.ir || w <= 0) continue;
          wsum += w; cx += e.cx * w; cy += e.cy * w; ir += e.ir * w; e.r.forEach((v, i) => (r[i] += v * w)); }
        if (wsum < 0.25) continue;
        out.push({ cx: cx / wsum, cy: cy / wsum, ir: ir / wsum, r: r.map((v) => v / wsum) });
      }
      return out;
    };
    const drawIrises = (eyes, scale) => {
      if (!iris) return;
      for (const e of eyes) {
        const rx = (e.r[0] + e.r[16]) / 2, ry = (e.r[8] + e.r[24]) / 2;                         // opening half-widths
        const ir = e.ir * scale;
        const lx = Math.max(0, rx - ir * 0.92), ly = Math.max(0, ry - ir * 0.85);
        let ox = gx, oy = gy; const len = Math.hypot(ox, oy); if (len > 1) { ox /= len; oy /= len; }   // a natural limit
        const ix = e.cx + ox * lx, iy = e.cy + oy * ly;
        ctx.save(); ctx.beginPath();
        e.r.forEach((rad, i) => { const a = (i / 32) * Math.PI * 2, px = e.cx + Math.cos(a) * rad * scale, py = e.cy + Math.sin(a) * rad * scale; i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); });
        ctx.closePath(); ctx.clip();
        const size = M.iris.size * (ir / M.iris.r);
        ctx.drawImage(iris, ix - size / 2, iy - size / 2, size, size);
        ctx.restore();
      }
    };
    const draw = (now) => {
      if (!wrap.isConnected) return;
      const dt = Math.min(0.05, (now - last) / 1000); last = now;
      if (now - lastMove > 4000 && !opts.reduce) { tx = Math.sin(now / 2300) * 0.55; ty = Math.sin(now / 3100) * 0.3; cursor = null; }   // looks around when left alone
      [x, vx] = spring(x, vx, tx, 38.7, dt); [y, vy] = spring(y, vy, ty, 38.7, dt);           // head settles in ~150 ms
      // eyes: toward the cursor from the eyes themselves (or follow the head when idle); ~100 ms, so they lead
      if (cursor && M) {
        const r = wrap.getBoundingClientRect(), k = r.width / M.size[0];
        const ex = r.left + M.size[0] * 0.5 * k, ey = r.top + M.size[1] * 0.49 * k;
        gtx = Math.tanh((cursor.x - ex) / (innerWidth * 0.12)); gty = Math.tanh((cursor.y - ey) / (innerHeight * 0.14));
      } else { gtx = tx; gty = ty; }
      [gx, gvx] = spring(gx, gvx, gtx, 58, dt); [gy, gvy] = spring(gy, gvy, gty, 58, dt);
      const moving = Math.abs(vx) + Math.abs(vy) + Math.abs(gvx) + Math.abs(gvy) > 1e-3 || Math.abs(tx - x) + Math.abs(ty - y) + Math.abs(gtx - gx) + Math.abs(gty - gy) > 1e-3;
      if (moving) dirty = true;
      const yawF = Math.max(-20, Math.min(20, -x * 20)), pitchF = Math.max(-10, Math.min(10, y * 10));
      if (M && !opts.reduce && !blink) {
        const by = nearest(M.blink_yaws, yawF), bp = nearest(M.blink_pitches, pitchF);
        if (now > nextBlink - 400) M.eyes && ["half", "closed"].forEach((e) => decode(lpName(by, bp, e)));
        if (now > nextBlink) { blink = now; nextBlink = now + 2600 + Math.random() * 3200; }
      }
      if (M && rest && (dirty || blink)) {
        const [bx0, by0, bx1, by1] = M.box, bw = bx1 - bx0, bh = by1 - by0;
        let parts = null;
        if (blink) {                                                                            // half, closed, half: ~200 ms
          const e = now - blink, eyes = e < 60 ? "half" : e < 140 ? "closed" : e < 200 ? "half" : null;
          if (!eyes) blink = null;
          else { const n = lpName(nearest(M.blink_yaws, yawF), nearest(M.blink_pitches, pitchF), eyes); if (bitmaps.get(n)) parts = [[n, 1]]; }
        }
        if (!parts) {                                                                           // the 4 nearest poses, bilinear
          const y0 = Math.max(-20, Math.min(19, Math.floor(yawF))), ty_ = Math.max(0, Math.min(1, yawF - y0));
          const [p0, p1, tp] = bracket(M.pitches, pitchF);
          parts = [[lpName(y0, p0), (1 - ty_) * (1 - tp)], [lpName(y0 + 1, p0), ty_ * (1 - tp)], [lpName(y0, p1), (1 - ty_) * tp], [lpName(y0 + 1, p1), ty_ * tp]];
          // decode ahead of the movement: every pose on the way from here to where the cursor points, nearest first
          const gyaw = Math.max(-20, Math.min(20, -tx * 20)), gpit = Math.max(-10, Math.min(10, ty * 10));
          const steps = Math.max(1, Math.ceil(Math.max(Math.abs(gyaw - yawF), Math.abs(gpit - pitchF) / 2.5 * 2)));
          for (let i = 0; i <= steps && decoding.size < 9; i++) {
            const fy = yawF + (gyaw - yawF) * i / steps, fp = pitchF + (gpit - pitchF) * i / steps;
            const [q0, q1] = bracket(M.pitches, fp), yy = Math.max(-20, Math.min(19, Math.floor(fy)));
            [[yy, q0], [yy + 1, q0], [yy, q1], [yy + 1, q1]].forEach(([a, b]) => decode(lpName(a, b)));
          }
        }
        // a pose not decoded yet is stood in for by the nearest decoded one, so the face never freezes
        const stand = (n) => {
          const m = /^y([+-]\d+)_p([+-][\d.]+)_open$/.exec(n); if (!m) return null;
          const yw = +m[1], pt = +m[2]; let best = null, bd = 1e9;
          for (const k of bitmaps.keys()) { const q = /^y([+-]\d+)_p([+-][\d.]+)_open$/.exec(k); if (!q) continue;
            const d = Math.abs(+q[1] - yw) + Math.abs(+q[2] - pt) * 0.8; if (d < bd) { bd = d; best = k; } }
          return best;
        };
        // full size if decoded (fading in over 120 ms from its small copy), else the small copy of the same
        // pose, else the nearest decoded pose
        let fading = false;
        const have = [];
        for (const [n, w] of parts) {
          if (w <= 0.001) continue;
          const bm = use(n), sm = small.get(n);
          if (bm) {
            const a = sm ? Math.min(1, (now - (arrived.get(n) || 0)) / 120) : 1;
            if (a < 1) { fading = true; have.push([sm, w * (1 - a), n]); }
            have.push([bm, w * a, n]);
          } else if (sm) have.push([sm, w, n]);
          else { const alt = stand(n); if (alt) have.push([bitmaps.get(alt), w, alt]); }
        }
        if (have.length) {
          const tDraw = performance.now();
          wrap.dataset.yaw = yawF.toFixed(2); wrap.dataset.pitch = pitchF.toFixed(2); wrap.dataset.gx = gx.toFixed(3); wrap.dataset.gy = gy.toFixed(3);
          ctx.clearRect(0, 0, canvas.width, canvas.height);
          ctx.drawImage(rest, 0, 0);
          ctx.clearRect(bx0, by0, bw, bh);
          let cum = 0;
          for (const [bm, w] of have) { if (w <= 0) continue; cum += w; ctx.globalAlpha = w / cum; ctx.drawImage(bm, bx0, by0, bw, bh); }   // exact weighted blend
          ctx.globalAlpha = 1;
          drawIrises(blendEyes(have.map(([, w, n]) => [n, w])), 1);
          const sig = have.map(([, w, n]) => n + ":" + w.toFixed(3)).join("|") + `|${gx.toFixed(3)},${gy.toFixed(3)}`;
          if (sig !== lastSig) { wrap.dataset.frames = String((+wrap.dataset.frames || 0) + 1); lastSig = sig; }
          const exact = parts.filter(([n, w]) => w > 0.001 && bitmaps.has(n)).length, needed = parts.filter(([, w]) => w > 0.001).length;
          wrap.dataset.missing = String(needed - exact);
          wrap.dataset.wrong = String(have.filter(([, , n]) => !parts.some(([q]) => q === n)).length);   // poses stood in by another pose
          wrap.dataset.drawms = (performance.now() - tDraw).toFixed(2);                         // the main-thread cost of a frame
          dirty = exact < needed || fading;
        }
      }
      requestAnimationFrame(draw);
    };
    requestAnimationFrame(draw);
    return { look, el: wrap };
  }

  // The greeting that pops up when the app opens: the person's own avatar, lip-synced, in their
  // cloned voice. Skip it, or let it finish, and the opening page comes in behind it.
  async function welcomeIntro(onDone) {
    let w;
    try { w = await API.welcome(); } catch (e) { return; }
    if (!w || w.state === "off" || w.state === "error") return;
    const away = () => S.view !== "home";                              // don't pop up once you've moved on
    for (let i = 0; w.state !== "done" && i < 120; i++) {               // first time only: it is being made (about a minute)
      await sleep(3000);
      if (away()) return;
      try { w = { ...w, ...(await API.mediaStatus(w.id)) }; } catch (e) { return; }
      if (w.state === "error") return;
    }
    if (w.state !== "done" || away() || document.getElementById("welcome")) return;
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const words = (w.full_text || "").split(" ");
    const cap = h("p", { class: "wl-cap", "aria-live": "polite" }, ...words.map((t) => h("span", null, t + " ")));
    const vid = h("video", { playsinline: true, preload: "auto", src: API.mediaUrl(w.id, "video") });
    const halo = h("div", { class: "wl-halo" });
    let ctx;
    const close = () => {
      if (!ov.isConnected || ov.classList.contains("out")) return;
      ov.classList.add("out"); try { vid.pause(); } catch (e) {}
      setTimeout(() => { ov.remove(); if (ctx) ctx.close().catch(() => {}); onDone && onDone(); }, reduce ? 0 : 700);
    };
    const tap = h("button", { class: "btn wl-tap", type: "button", hidden: true }, "▶  Meet your twin");
    const ov = h("div", { id: "welcome", class: "welcome" + (reduce ? " still" : ""), role: "dialog", "aria-label": "Your twin says hello" },
      h("div", { class: "wl-stage" },
        h("div", { class: "wl-orb" }, halo, h("div", { class: "wl-ring" }), h("div", { class: "wl-face" }, vid)),
        h("p", { class: "eyebrow wl-name" }, (w.name || "") + " · digital twin"),
        cap, tap),
      h("button", { class: "btn ghost small wl-skip", type: "button", onclick: close }, "Skip"));
    document.body.append(ov);
    addEventListener("keydown", function esc(e) { if (e.key === "Escape") { close(); removeEventListener("keydown", esc); } });
    const pulse = () => {                                               // the halo breathes with the voice
      try {
        ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
        const an = ctx.createAnalyser(); an.fftSize = 256;
        ctx.createMediaElementSource(vid).connect(an); an.connect(ctx.destination);
        const buf = new Uint8Array(an.frequencyBinCount);
        const tick = () => { if (!ov.isConnected) return;
          an.getByteFrequencyData(buf); const lvl = buf.reduce((a, b) => a + b, 0) / buf.length / 255;
          halo.style.setProperty("--lvl", Math.min(1, lvl * 2.2).toFixed(3)); requestAnimationFrame(tick); };
        tick();
      } catch (e) { /* no Web Audio: the halo keeps its gentle default motion */ }
    };
    const spans = cap.querySelectorAll("span");
    vid.addEventListener("timeupdate", () => {                          // captions follow the speech
      const n = vid.duration ? Math.ceil((vid.currentTime / vid.duration) * spans.length * 1.04) : 0;
      spans.forEach((sp, i) => sp.classList.toggle("on", i < n));
    });
    vid.addEventListener("ended", () => { spans.forEach((sp) => sp.classList.add("on")); setTimeout(close, 1400); });
    let started = false;
    const start = async () => { tap.hidden = true; if (!started) { started = true; pulse(); } if (ctx && ctx.state === "suspended") await ctx.resume(); await vid.play(); };
    setTimeout(async () => {
      try { await start(); }
      catch (e) { tap.hidden = false; tap.onclick = () => start().catch(close); }   // the window wants a click before sound
    }, reduce ? 0 : 900);
  }

  VIEWS.home = function (el) {
    if (!S.greeted && S.mode === "live") {                              // once per launch, before the opening page
      S.greeted = true;
      welcomeIntro(() => { if (S.view === "home") go("home"); });       // the page plays its entrance as the greeting leaves
    }
    const st = S.status || {}, caps = st.media || {};
    const name = titleCase(st.subject) || "Your", first = firstName(name) || "Your";
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    let theme = store.get("dt-home-theme") || "red";
    const root = h("section", { class: "home theme-" + theme + (reduce ? " still" : ""), "aria-label": `${name}'s digital twin` });

    // top bar: live clock, brand, navigation
    const time = h("b", { class: "num" }), date = h("span");
    let shown = false;
    const tick = () => { if (shown && !root.isConnected) return clearInterval(clock); const d = new Date();
      time.textContent = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
      date.textContent = d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" }); };
    const clock = setInterval(tick, 1000); tick();
    const nav = h("nav", { class: "home-nav", "aria-label": "Sections" },
      [["ask", "Chat"], ["teach", "Teach"], ["profile", "Profile"], ["results", "Results"], ["settings", "Settings"]]
        .map(([v, l]) => h("button", { type: "button", onclick: (e) => (v === "ask" ? wipeTo(v, e.currentTarget) : go(v)) }, l)));
    const top = h("header", { class: "home-top" }, h("div", { class: "home-clock" }, time, date),
      h("div", { class: "home-brand" }, h("span", { class: "home-mark", "aria-hidden": "true" }, h("i"), h("i")), "Deferral Twin"), nav);

    const stage = h("div", { class: "home-stage" }, h("div", { class: "av-shadow" }));
    const line = (t, i) => h("span", { class: "hl" }, h("span", { style: `--i:${i}` }, t));
    const headline = h("h1", { class: "home-h" }, line("Hi, I'm", 0), line(`${first}'s`, 1), line("digital twin.", 2));
    const chat = h("button", { class: "home-cta", type: "button", onclick: (e) => wipeTo("ask", e.currentTarget) }, "Chat with my digital twin", h("span", { "aria-hidden": "true" }, "→"));
    const facts = st.memory ? st.memory.facts : null;
    const left = h("div", { class: "home-left" }, h("span", { class: "home-live" }, h("i"), `Online · runs only on this ${DEVICE}`), headline);
    const right = h("div", { class: "home-right" },
      h("p", null, `Ask what I think, what I'd do, or how I spend my days. I answer the way ${first} would, and when I don't know, I say so.`),
      h("div", { class: "home-btns" }, chat),
      h("div", { class: "home-stats" }, facts != null ? h("span", null, h("b", { class: "num" }, facts), " memories") : null,
        h("span", null, h("b", null, "3"), " honesty tiers"), h("span", null, h("b", null, "0"), " bytes to the cloud")));
    const chips = HOME_ASKS.map((q) => h("button", { type: "button", onclick: (e) => { S.pendingAsk = q; wipeTo("ask", e.currentTarget); } }, q));
    const marquee = h("div", { class: "home-marquee", "aria-label": "Questions to try" }, h("div", { class: "mq-track" }, ...chips, ...chips.map((c) => { const k = c.cloneNode(true); k.onclick = c.onclick; k.tabIndex = -1; k.setAttribute("aria-hidden", "true"); return k; })));
    const themes = h("div", { class: "home-themes", role: "radiogroup", "aria-label": "Colour" }, HOME_THEMES.map(([t, label]) =>
      h("button", { type: "button", class: "sw-" + t, role: "radio", "aria-checked": String(t === theme), title: label, "aria-label": label,
        onclick: (e) => { root.classList.replace("theme-" + theme, "theme-" + t); theme = t; store.set("dt-home-theme", t);
          themes.querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", String(b === e.currentTarget))); } })));
    root.append(h("div", { class: "home-bg", "aria-hidden": "true" }, h("span", { class: "home-glow" })), top, stage, left, right, marquee, themes);
    el.append(root); shown = true;

    const ch = character(stage, { reduce });
    root.addEventListener("pointermove", (e) => { ch.look(e.clientX, e.clientY);
      root.style.setProperty("--gx", (e.clientX / innerWidth * 100).toFixed(1) + "%"); root.style.setProperty("--gy", (e.clientY / innerHeight * 100).toFixed(1) + "%"); });
  };

  // ---------- ask ----------
  VIEWS.ask = function (el) {
    const thread = h("div", { class: "thread", "aria-live": "polite" });
    const input = h("input", { type: "text", id: "askBox", placeholder: "Ask your twin anything — English, తెలుగు or हिन्दी", autocomplete: "off", "aria-label": "Question" });
    const send = h("button", { class: "btn", type: "submit" }, "Ask");
    // Talk mode: speak the question (Whisper on this Mac), hear the answer in your voice
    const talkOk = S.mode === "live" && S.status && S.status.talk;
    const mic = h("button", { class: "btn quiet mic", type: "button", title: talkOk ? "Talk: press, speak, press again" : "Talk mode: install speech recognition (once)", "aria-label": "Talk", disabled: S.mode !== "live" }, "🎙");
    const talkLang = h("select", { class: "btn quiet small talk-lang", title: "The language you speak in", "aria-label": "Speech language", hidden: !talkOk },
      h("option", { value: "" }, "Auto"), h("option", { value: "en" }, "English"), h("option", { value: "hi" }, "हिन्दी"), h("option", { value: "te" }, "తెలుగు"));
    talkLang.value = store.get("dt-talk-lang") || "";
    talkLang.addEventListener("change", () => store.set("dt-talk-lang", talkLang.value));
    const form = h("form", null, input, talkLang, mic, send);
    let talking = null;
    mic.addEventListener("click", async () => {
      if (talking) return talking.stop();
      if (!(S.status && S.status.talk)) {                                                      // first time: download Whisper, once
        if (!confirm(`Talk mode needs speech recognition (Whisper, about 970 MB), downloaded once and run on this ${DEVICE}. Download it now?`)) return;
        try { await req("POST", "api/talk/install", {}); } catch (e) { return toast(e.message); }
        mic.disabled = true; mic.textContent = "⏳"; toast("Downloading speech recognition… keep using the app.");
        for (;;) { await sleep(4000); await refresh(); const j = S.status.talk_job || {};
          if (S.status.talk) { mic.disabled = false; mic.textContent = "🎙"; talkLang.hidden = false; return toast("Talk mode is ready: press 🎙 and speak."); }
          if (j.state === "error") { mic.disabled = false; mic.textContent = "🎙"; return toast("Download failed: " + j.error); } }
      }
      const done = async (job) => {
        mic.classList.remove("rec"); mic.textContent = "…"; input.placeholder = "Understanding what you said…";
        try { const r = await job; input.placeholder = "Ask your twin anything — English, తెలుగు or हिन्दी"; mic.textContent = "🎙"; ask(r.text, false, { speak: true }); }
        catch (e) { mic.textContent = "🎙"; input.placeholder = "Ask your twin anything — English, తెలుగు or हिन्दी"; toast(e.message); }
      };
      try {
        if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder)) throw new Error("no recorder");
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const type = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"].find((t) => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t)) || "";
        const mr = new MediaRecorder(stream, type ? { mimeType: type } : undefined), chunks = [];
        mr.ondataavailable = (e) => e.data.size && chunks.push(e.data);
        mr.onstop = () => { stream.getTracks().forEach((t) => t.stop()); talking = null;
          const ext = (mr.mimeType || type).includes("mp4") ? "m4a" : "webm";
          done(API.transcribe(new File([new Blob(chunks, { type: mr.mimeType || type })], "speech." + ext), talkLang.value)); };
        mr.start(250); talking = mr; setTimeout(() => talking === mr && mr.stop(), 30000);
      } catch (e) {                                                                           // the window can't reach the mic: record through the app
        try { await req("POST", "api/capture/talk/start", {}); talking = { stop: () => { talking = null; done(req("POST", "api/capture/talk/stop" + (talkLang.value ? "?lang=" + talkLang.value : ""), {})); } }; }
        catch (e2) { return toast("The microphone isn’t available: " + e2.message); }
      }
      mic.classList.add("rec"); mic.textContent = "■"; input.placeholder = "Listening… press ■ when you’re done";
    });
    form.addEventListener("submit", (e) => { e.preventDefault(); const q = input.value.trim(); if (q) { input.value = ""; ask(q); } });
    const caps = (S.status && S.status.media) || {};
    const est = caps.estimates || {};
    const tg = (key, label, ok, hint) => {
      const c = h("input", { type: "checkbox", id: "opt-" + key, disabled: !ok }); c.checked = ok && !!S.settings[key];
      c.addEventListener("change", () => { S.settings[key] = c.checked; API.putSettings({ [key]: c.checked }).catch(() => {}); });
      return h("label", { for: "opt-" + key, title: ok ? hint || "" : caps.why || "" }, c, label, hint ? h("span", { class: "hint" }, hint) : null);
    };
    const lang = h("select", { id: "opt-lang", "aria-label": "Voice language", disabled: !caps.voice, class: "btn quiet small" }, h("option", { value: "en" }, "English"), h("option", { value: "hi", disabled: !caps.hindi }, "Hindi"));
    lang.value = S.settings.lang || "en"; lang.addEventListener("change", () => { S.settings.lang = lang.value; API.putSettings({ lang: lang.value }).catch(() => {}); });
    const videoHint = est.video_seconds != null ? `${minutes(est.video_seconds)} per answer on this ${DEVICE}` : `slow on this ${DEVICE}; not measured yet`;
    const toggles = h("div", { class: "toggles" }, tg("speak", "Speak in my voice", caps.voice), tg("video", "Avatar video", caps.video, videoHint), lang);
    if (S.mode === "live" && !caps.voice) toggles.append(h("span", { class: "row small" }, caps.why || "Voice isn’t available.", caps.installed && !caps.installed.voice ? installLink("voice") : null,
      caps.consented && !hasVoice() ? h("button", { class: "btn small quiet", type: "button", onclick: () => go("voice") }, "Add your voice") : null));
    else if (S.mode === "live" && caps.voice && !caps.video) toggles.append(h("span", { class: "row small" }, !hasPhoto() ? "Add a photo for the avatar." : "The lip-sync model isn’t installed.",
      !hasPhoto() ? h("button", { class: "btn small quiet", type: "button", onclick: () => go("photo") }, "Add a photo") : installLink("lipsync")));
    const suggest = h("div", { class: "suggest" },
      h("button", { type: "button", class: "new-chat", title: "Start over: the twin forgets this conversation (not its memory)", onclick: () => { S.chat = []; S.turns = []; go("ask"); } }, "↺ New chat"),
      h("button", { type: "button", onclick: heldout }, "A question it has never seen"),
      ["What do you do to relax?", "Your train is cancelled an hour before an exam. What do you do?", "What is your passport number?"].map((q) => h("button", { type: "button", onclick: () => ask(q) }, q)));
    const main = h("div", { class: "stack" }, S.mode === "preview" ? previewNote() : null, thread, h("div", { class: "composer" }, suggest, form, toggles));
    el.append(h("div", { class: "ask-wrap" }, main, side()));
    // the landing page's character, out of focus behind the conversation, still following the cursor
    const ghost = h("div", { class: "ask-ghost", "aria-hidden": "true" });
    el.append(ghost);
    const ch = character(ghost, { reduce: matchMedia("(prefers-reduced-motion: reduce)").matches, lite: true });
    const follow = (e) => (ghost.isConnected ? ch.look(e.clientX, e.clientY) : removeEventListener("pointermove", follow));
    addEventListener("pointermove", follow);
    if (!S.chat.length) thread.append(h("div", { class: "empty" }, h("h2", null, `Ask ${S.status && S.status.subject ? firstName(S.status.subject) + "’s" : "your"} twin something`),
      h("p", null, "Try one thing it knows, one new situation, and one thing you never told it. The three answers look different on purpose.")));
    S.chat.forEach((node) => thread.append(node));
    input.focus();
    if (S.pendingAsk) { const q = S.pendingAsk; S.pendingAsk = null; ask(q); }   // a question tapped on the opening page

    async function heldout() { try { const r = await API.heldout(); ask(r.question, true); } catch (e) { toast(e.message); } }
    async function ask(q, isHeld, opts = {}) {
      const empty = $(".empty", thread); if (empty) empty.remove();
      const qn = h("div", { class: "msg-q" }, q, isHeld ? h("div", { class: "small", style: "opacity:.8" }, "held out: the twin never saw your answer to this") : null);
      const text = h("p", { class: "ans-text" });
      const pending = h("article", { class: "ans pending" }, h("span", { class: "muted small" }, "Looking through what you told it…"), text);
      thread.append(qn, pending); S.chat.push(qn); qn.scrollIntoView({ block: "nearest", behavior: "smooth" });
      let out, streamed = "";
      try { out = await API.askStream(q, (t) => { streamed += t; text.textContent = streamed; pending.firstChild.textContent = "Answering…"; }, S.turns.slice(-6)); }
      catch (e) { pending.textContent = ""; pending.append(h("p", { class: "bad-line" }, e.message)); return; }
      S.turns.push({ q, a: out.answer_en || out.answer });                                       // remembered for follow-ups in this chat
      const node = answerCard(q, out); pending.replaceWith(node); S.chat.push(node); refreshStats();
      node.scrollIntoView({ block: "nearest", behavior: "smooth" });
      if (caps.voice && (S.settings.speak || opts.speak)) speak(node, out.answer, S.settings.video && caps.video, out.lang);
      return out;
    }
  };

  function gauge(conf) {
    const pos = (v) => Math.min(100, (100 * v) / GMAX);
    const color = conf < HARD ? "var(--def)" : conf < SOFT ? "var(--inf)" : "var(--conf)";
    return h("div", { class: "gauge", title: "Best match between the question and anything in memory" },
      h("div", { class: "gauge-bar" }, h("span", { class: "tick", style: `left:${pos(HARD)}%` }), h("span", { class: "tick", style: `left:${pos(SOFT)}%` }),
        h("span", { class: "pin", style: `left:${pos(conf)}%;background:${color}` })),
      h("div", { class: "gauge-scale" }, h("span", { style: "left:0;transform:none" }, "0"), h("span", { style: `left:${pos(HARD)}%` }, "0.15"), h("span", { style: `left:${pos(SOFT)}%` }, "0.35"),
        h("span", { style: "right:0;left:auto;transform:none" }, "best match " + fmt(conf))));
  }
  function parseQA(t) { const m = /^Q: (.*?)\s{2}A: (.*)$/s.exec(t || ""); return m ? { q: m[1], a: m[2] } : null; }

  function answerCard(q, out) {
    const t = TIER[out.tier] || TIER.confident_answer;
    const card = h("article", { class: "ans " + t.cls });
    const top = out.evidence && out.evidence[0];
    const icon = { conf: "“", inf: "≈", def: "∅", self: "i" }[t.cls];
    card.append(h("div", { class: "row" }, h("span", { class: "tier " + t.cls }, h("span", { class: "tier-ic", "aria-hidden": "true" }, icon), out.tier === "field_knowledge" ? `Expert knowledge · ${out.field || "your field"}` : t.label),
      out.mode ? h("span", { class: "mode-badge " + out.mode, title: out.mode === "online" ? `Answered by ${out.model || "Gemini"}, thinking from your profile` : (out.online_note ? "Online mode couldn't answer (" + out.online_note + "), so this is the offline answer" : "The offline answer") },
        out.mode === "online" ? "online · " + (out.model || "gemini").replace(/^gemini-/, "gemini ").replace(/-preview$/, "") : "offline · this mac") : null,
      h("span", { class: "spacer" }),
      h("span", { class: "muted small num" }, out.tier === "hard_defer" ? "no model call" : out.seconds != null ? out.seconds + " s" : "")));
    if (out.asked_as) card.append(h("p", { class: "small muted asked-as" }, (out.lang && out.lang !== "en" ? `Asked in ${{ hi: "Hindi", te: "Telugu" }[out.lang]} · ` : "Follow-up · ") + "understood as: “" + out.asked_as + "”"));
    if (out.lang_note) card.append(h("p", { class: "small mode-note" }, out.lang_note));
    if (out.mode === "offline" && out.online_note)                                             // say why, not just in a tooltip
      card.append(h("p", { class: "small mode-note" }, "Gemini couldn't answer this time (" + out.online_note + `), so this answer comes from the model on your ${DEVICE}.`));
    if (out.tier === "hard_defer") {
      card.append(h("p", { class: "ans-text" }, "I don’t know."),
        h("p", { class: "small" }, "Nothing you’ve told me is close enough to answer this, so I won’t guess. ", h("span", { class: "muted" }, `The closest memory scored ${fmt(out.confidence)}; answering needs ${HARD}.`)));
    } else if (out.tier === "about_twin") {
      card.append(h("p", { class: "ans-text" }, out.answer),
        h("p", { class: "small muted" }, "A question about the twin itself or the clock, so it skips the memory check: nothing here is a guess about you."));
    } else if (out.tier === "field_knowledge") {
      card.append(h("p", { class: "ans-text" }, out.answer),
        h("p", { class: "small muted" }, `Gemini’s knowledge of ${out.field ? "a " + out.field.toLowerCase() + "’s" : "your"} field, explained the way you would. It isn’t something you told your twin, and nothing in it is a claim about your own life.`));
    } else if (out.tier === "soft_answer") {
      card.append(h("p", { class: "ans-text" }, out.answer),
        h("p", { class: "small muted" }, "A guess from your profile and similar situations you described, not something you said. The closest thing you did say scored ", h("b", { class: "num" }, fmt(out.confidence)), "."));
    } else {
      card.append(h("p", { class: "ans-text" }, out.answer));
      const qa = top && parseQA(top.text);
      if (qa) card.append(h("div", { class: "source" }, h("span", { class: "eyebrow" }, top.source === "user_correction" ? "Your correction" : top.source === "user_note" ? "You told it" : "Your answer to"),
        h("span", null, h("i", null, qa.q), " — ", qa.a)));
      else if (top) card.append(h("div", { class: "source" }, h("span", { class: "eyebrow" }, srcLabel(top.source)), h("span", null, top.text)));
    }
    if (out.truth != null) card.append(h("div", { class: "truth" }, h("span", { class: "eyebrow" }, "What you actually answered"), h("span", null, out.truth),
      out.truth_similarity != null ? h("span", { class: "small muted" }, "Similarity of the twin’s answer to yours: ", h("b", { class: "num" }, fmt(out.truth_similarity))) : h("span", { class: "small muted" }, "Refused, so not scored.")));
    const why = h("details", { class: "ev" }, h("summary", null, "Why this answer"),
      h("div", { class: "stack", style: "gap:10px;margin-top:8px" }, gauge(out.confidence),
        h("p", { class: "small muted" }, out.tier === "field_knowledge" ? "None of your own answers was close enough, so this comes from expert knowledge of your field. The closest of your answers are below."
          : out.tier === "hard_defer" ? `Nothing you said reaches ${HARD}, so it refused. The closest of your answers are below.`
          : `These are your real answers it used, best match first. The bar is how close each is to the question; the marks are ${HARD} (below: “I don’t know”) and ${SOFT} (above: it answers from what you said). Things you taught it rank higher but never raise this score.`),
        h("div", { class: "evl" }, out.evidence.filter((e, i) => i < 6 && (i < 3 || e.score >= HARD)).map((e) => {
          const qa = parseQA(e.text), used = e.score >= HARD && out.tier !== "hard_defer" && out.tier !== "field_knowledge";
          return h("div", { class: "evi2" + (used ? " used" : "") },
            h("div", { class: "evbar", title: "Similarity " + fmt(e.score, 3) }, h("i", { style: `width:${Math.min(100, (100 * Math.max(0, e.score)) / GMAX)}%;background:${e.score < HARD ? "var(--def)" : e.score < SOFT ? "var(--inf)" : "var(--conf)"}` }),
              h("span", { class: "tick", style: `left:${(100 * HARD) / GMAX}%` }), h("span", { class: "tick", style: `left:${(100 * SOFT) / GMAX}%` })),
            h("div", { class: "evt" }, h("span", { class: "src" }, srcLabel(e.source) + " · " + fmt(e.score, 2) + (used ? " · used" : "")),
              qa ? h("span", null, h("i", null, qa.q), " — ", qa.a) : h("span", null, e.text)));
        }))));
    card.append(why);
    const fixBox = h("div", { class: "fix", hidden: true });
    const ta = h("textarea", { rows: 2, placeholder: "What would you actually say?", "aria-label": "Your answer" });
    fixBox.append(ta, h("div", { class: "row" }, h("button", { class: "btn small", type: "button", onclick: async () => {
      if (!ta.value.trim()) return toast("Type what you would say.");
      try { const r = await API.correct({ question: q, twin_answer: out.answer, expected: ta.value.trim(), tier: out.tier, confidence: out.confidence });
        fixBox.textContent = ""; fixBox.append(h("p", { class: "okline" }, `✓ Learned. It holds ${r.corrections} correction${r.corrections === 1 ? "" : "s"}, kept across restarts. Ask again to see the change.`));
        refreshStats(); } catch (e) { toast(e.message); }
    } }, "Teach the twin"), h("button", { class: "btn ghost small", type: "button", onclick: () => (fixBox.hidden = true) }, "Cancel")));
    const actions = h("div", { class: "row" }, h("button", { class: "btn ghost small", type: "button", onclick: () => { fixBox.hidden = false; ta.focus(); } }, "That’s not me"));
    if (out.tier !== "hard_defer" && out.tier !== "about_twin" && S.mode === "live")    // a good answer becomes something you said
      actions.prepend(h("button", { class: "btn ghost small", type: "button", onclick: async (e) => {
        try { await API.correct({ question: q, twin_answer: out.answer, expected: out.answer, tier: out.tier, confidence: out.confidence });
          e.target.replaceWith(h("span", { class: "okline small" }, "✓ Kept as yours")); refreshStats(); } catch (err) { toast(err.message); }
      } }, "That’s me ✓"));
    const caps = (S.status && S.status.media) || {};
    if (caps.voice && !S.settings.speak) actions.append(h("button", { class: "btn ghost small", type: "button", onclick: (e) => { e.target.remove(); speak(card, out.answer, false); } }, "Speak it"));
    if (caps.video && !(S.settings.speak && S.settings.video)) actions.append(h("button", { class: "btn ghost small", type: "button", onclick: (e) => { e.target.remove(); speak(card, out.answer, true); } }, "Make the video"));
    card.append(actions, fixBox);
    return card;
  }
  const srcLabel = (s) => ({ user_correction: "your correction", user_note: "you told it", twin2k500_train: "interview", interview: "interview" }[s] || s);

  async function speak(card, text, video, lang) {
    if (lang === "te") { card.append(h("p", { class: "small muted" }, "Your cloned voice can’t speak Telugu yet, so this answer is text only.")); return; }
    const st = h("div", { class: "media-st" }, h("span", { class: "spin" }), "Generating your voice…"); card.append(st);
    let job;
    try { job = await API.media({ text, lang: lang === "hi" ? "hi" : S.settings.lang || "en", video: !!video }); } catch (e) { st.textContent = e.message; return; }
    let haveAudio = false;
    for (let i = 0; ; i++) {
      let j = job;
      if (i) { await sleep(j.state === "video" || j.state === "video_queued" ? 3000 : 900); try { j = job = await API.mediaStatus(job.id); } catch (e) { continue; } }
      if (j.audio && !haveAudio) { haveAudio = true; const a = h("audio", { controls: true, src: API.mediaUrl(job.id, "audio") }); st.before(a); a.play().catch(() => {}); }
      if (j.state === "video_queued") { st.lastChild.textContent = "Video queued behind another render. Keep asking; it appears here when ready."; paintJobs(true); }
      if (j.state === "video") { st.lastChild.textContent = j.remaining_s ? `Animating the avatar, ${minutes(j.remaining_s)} left. Keep asking meanwhile.` : `Animating the avatar (${Math.round(j.elapsed_s)} s so far). Keep asking meanwhile.`; paintJobs(true); }
      if (j.state === "error") { st.textContent = "Voice or video failed: " + j.error; return; }
      if (j.state === "done") {
        if (j.video) { st.replaceWith(h("video", { controls: true, playsinline: true, autoplay: !haveAudio || j.cached, src: API.mediaUrl(job.id, "video") })); if (j.cached) toast("Played from the clip cache."); }
        else st.remove();
        paintJobs(true); return;
      }
    }
  }

  // ---------- background video indicator ----------
  let jobsTimer = null;
  async function paintJobs(force) {
    const box = $("#jobs"); if (!box) return;
    let jobs = (S.status && S.status.video_jobs) || [];
    if (force && S.mode === "live") { try { jobs = (await req("GET", "api/media")).jobs; } catch (e) {} }
    box.textContent = ""; box.hidden = !jobs.length;
    jobs.forEach((j) => box.append(h("div", { class: "job" }, h("span", { class: "spin" }), h("span", null, h("b", null, j.state === "video" ? "Animating" : "Queued"), " · “", j.text, "…” ",
      h("span", { class: "muted" }, j.state === "video" ? (j.remaining_s ? minutes(j.remaining_s) + " left" : Math.round(j.elapsed_s) + " s") : "")))));
    clearTimeout(jobsTimer);
    if (jobs.length) jobsTimer = setTimeout(() => paintJobs(true), 4000);
  }

  function side() {
    const s = h("aside", { class: "side", id: "side" });
    paintSide(s); return s;
  }
  function paintSide(s) {
    s = s || $("#side"); if (!s) return;
    const m = (S.status && S.status.memory) || {};
    s.textContent = "";
    s.append(h("div", { class: "panel stack" }, h("span", { class: "eyebrow" }, "Memory"),
      h("div", { class: "stat-row" }, h("div", { class: "stat" }, h("b", null, m.facts ?? "–"), h("span", null, "facts")),
        h("div", { class: "stat" }, h("b", null, m.corrections ?? 0), h("span", null, "corrections")), h("div", { class: "stat" }, h("b", null, m.notes ?? 0), h("span", null, "told directly"))),
      h("p", { class: "small muted" }, `${m.heldout ?? "–"} answers are held back so the twin can be tested on questions it has never seen.`)),
      h("div", { class: "panel stack small" }, h("span", { class: "eyebrow" }, "How it decides"),
        h("p", null, "It finds the stored answer closest to your question."),
        h("p", null, h("b", { style: "color:var(--conf)" }, "≥ 0.35"), " answers from it, and shows you which answer."),
        h("p", null, h("b", { style: "color:var(--inf)" }, "0.15 to 0.35"), " reasons from your profile and says it is guessing."),
        h("p", null, h("b", { style: "color:var(--def)" }, "< 0.15"), " says it doesn’t know, without calling the model.")));
  }
  async function refreshStats() { await refresh(); paintSide(); }

  // ---------- teach ----------
  // ---------- "Guess me": the twin locks a guess, you answer, the match is scored and remembered ----------
  VIEWS.guess = function (el) {
    const wrap = h("div", { class: "narrow stack" });
    const statsEl = h("div", { class: "game-stats" });
    const body = h("section", { class: "panel stack game" });
    wrap.append(h("div", { class: "sec-head" }, h("span", { class: "eyebrow" }, "A game · and a live accuracy test"), h("h2", null, "Can your twin guess you?"),
      h("p", { class: "muted" }, "A new question you were never asked. Your twin answers first and locks its guess before you type. Then you answer, both are revealed, and the match is scored. Your answer is kept as memory, so watch the score climb.")),
      statsEl, body);
    el.append(wrap);
    if (S.mode !== "live") { body.append(h("p", { class: "muted" }, "Available in the desktop app.")); return; }
    const pct = (v) => v == null ? "–" : Math.round(v * 100) + "%";
    const verdict = (m) => m == null ? ["It said it doesn’t know", "def"] : m >= 0.6 ? ["Nailed it", "conf"] : m >= 0.4 ? ["Close", "inf"] : ["Missed", "def"];
    function paintStats(st) {
      statsEl.textContent = "";
      if (!st || !st.rounds) return;
      const trend = st.first != null && st.latest != null ? Math.round((st.latest - st.first) * 100) : null;
      statsEl.append(
        h("div", { class: "kpi" }, h("span", { class: "eyebrow" }, "Rounds"), h("b", null, String(st.rounds))),
        h("div", { class: "kpi" }, h("span", { class: "eyebrow" }, "Average match"), h("b", null, pct(st.average))),
        h("div", { class: "kpi" }, h("span", { class: "eyebrow" }, "First rounds → latest"), h("b", null, `${pct(st.first)} → ${pct(st.latest)}`),
          trend != null ? h("span", { class: "small " + (trend >= 0 ? "up" : "down") }, (trend >= 0 ? "▲ " : "▼ ") + Math.abs(trend) + " points") : null));
    }
    async function round() {
      body.textContent = ""; body.append(h("p", { class: "muted" }, "Picking a question…"));
      let g; try { g = await API.game(); } catch (e) { body.textContent = ""; body.append(h("p", { class: "bad-line" }, e.message)); return; }
      paintStats(g.stats);
      body.textContent = "";
      if (!g.question) {
        body.append(h("p", null, "You’ve played every question. Your answers stay in the twin’s memory."),
          h("button", { class: "btn quiet", type: "button", onclick: async () => { await API.gameReset(); round(); } }, "Play them again"));
        return;
      }
      const lock = h("p", { class: "game-lock" }, h("span", { class: "spin", "aria-hidden": "true" }), "Your twin is thinking…");
      const ta = h("textarea", { rows: 3, placeholder: "What would you actually do? Two or three sentences.", "aria-label": "Your answer" });
      const go_ = h("button", { class: "btn", type: "button", disabled: true }, "Reveal");
      body.append(h("p", { class: "game-q" }, g.question), lock, ta, h("div", { class: "row" }, go_, h("span", { class: "spacer" }),
        h("button", { class: "btn quiet small", type: "button", onclick: round }, "Skip")));
      ta.focus();
      let locked = false;
      const ready = () => (go_.disabled = !(locked && ta.value.trim()));
      ta.addEventListener("input", ready);
      API.gameGuess(g.question).then((r) => { locked = true; lock.textContent = "🔒 Your twin has locked in its guess" + (r.mode ? ` (${r.mode})` : "") + ". Now you."; ready(); })
        .catch((e) => { lock.textContent = ""; lock.append(h("span", { class: "bad-line" }, e.message)); });
      go_.addEventListener("click", async () => {
        go_.disabled = true; go_.textContent = "Scoring…";
        let r; try { r = await API.gameAnswer(g.question, ta.value); } catch (e) { toast(e.message); go_.textContent = "Reveal"; ready(); return; }
        const [label, cls] = verdict(r.match);
        body.textContent = "";
        body.append(h("p", { class: "game-q" }, g.question),
          h("div", { class: "game-reveal" },
            h("div", { class: "game-side" }, h("span", { class: "eyebrow" }, "Your twin guessed"), h("p", null, r.tier === "hard_defer" ? "I don’t know." : r.guess),
              r.mode ? h("span", { class: "mode-badge " + r.mode }, r.mode) : null),
            h("div", { class: "game-side" }, h("span", { class: "eyebrow" }, "You said"), h("p", null, r.answer))),
          h("div", { class: "row game-score" }, h("span", { class: "tier " + cls }, label), h("b", { class: "num" }, r.match == null ? "" : pct(r.match) + " match"),
            h("span", { class: "spacer" }), h("span", { class: "small muted" }, "✓ Remembered: next time it knows this.")),
          h("div", { class: "row" }, h("button", { class: "btn", type: "button", onclick: round }, "Next question")));
        paintStats(r.stats);
        refresh();
      });
    }
    round();
  };

  // Personality snapshot: the Big Five from the "How you tend to be" statements, as a bar chart
  function bigFivePanel() {
    const box = h("section", { class: "panel stack bigfive" }, h("h2", null, "Personality snapshot"));
    if (S.mode !== "live") { box.append(h("p", { class: "muted" }, "Available in the desktop app.")); return box; }
    API.bigfive().then((r) => {
      const W = 560, rowH = 46, lab = 150, bw = W - lab - 50, H = r.traits.length * rowH + 26;
      const ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns, "svg");
      const el = (tag, at, txt) => { const e = document.createElementNS(ns, tag); for (const k in at) e.setAttribute(k, at[k]); if (txt != null) e.textContent = txt; return e; };
      svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("role", "img"); svg.setAttribute("class", "b5");
      svg.setAttribute("aria-label", "Big Five scores: " + r.traits.map((t) => `${t.trait} ${t.score ?? "n/a"} of 100`).join(", "));
      const x = (v) => lab + (bw * v) / 100;
      svg.append(el("line", { x1: x(50), x2: x(50), y1: 4, y2: H - 22, class: "b5-mid" }), el("text", { x: x(50), y: H - 6, class: "b5-axis", "text-anchor": "middle" }, "50 · middle"),
        el("text", { x: x(0), y: H - 6, class: "b5-axis" }, "0"), el("text", { x: x(100), y: H - 6, class: "b5-axis", "text-anchor": "end" }, "100"));
      const tip = h("div", { class: "b5-tip", hidden: true });
      r.traits.forEach((t, i) => {
        const y = 8 + i * rowH, v = t.score ?? 0;
        svg.append(el("text", { x: 0, y: y + 15, class: "b5-label" }, t.trait));
        svg.append(el("rect", { x: x(0), y: y + 4, width: bw, height: 14, rx: 4, class: "b5-track" }));
        if (t.score != null) svg.append(el("rect", { x: x(0), y: y + 4, width: Math.max(4, (bw * v) / 100), height: 14, rx: 4, class: "b5-bar" }));
        svg.append(el("text", { x: x(Math.max(v, 0)) + 8, y: y + 16, class: "b5-val" }, t.score == null ? "n/a" : String(t.score)));
        const hit = el("rect", { x: 0, y, width: W, height: rowH - 6, fill: "transparent", tabindex: 0 });
        const show = (e) => { tip.hidden = false; tip.textContent = `${t.trait}: ${t.score ?? "n/a"} of 100 · ${t.about} · from ${t.items} answers`;
          const b = box.getBoundingClientRect(); tip.style.left = Math.min(b.width - 260, (e.clientX || b.left + 200) - b.left + 12) + "px"; tip.style.top = ((e.clientY || b.top + 60) - b.top + 12) + "px"; };
        hit.addEventListener("pointermove", show); hit.addEventListener("focus", show);
        hit.addEventListener("pointerleave", () => (tip.hidden = true)); hit.addEventListener("blur", () => (tip.hidden = true));
        svg.append(hit);
      });
      const table = h("details", { class: "small" }, h("summary", null, "Show as a table"),
        h("table", { class: "b5-table" }, h("tr", null, h("th", null, "Trait"), h("th", null, "Score (0–100)"), h("th", null, "What it means")),
          ...r.traits.map((t) => h("tr", null, h("td", null, t.trait), h("td", { class: "num" }, t.score ?? "n/a"), h("td", null, t.about)))));
      box.append(h("p", { class: "muted small" }, r.source + " 50 is the middle of the scale, not an average of other people."), svg, tip, table);
    }).catch((e) => box.append(h("p", { class: "muted small" }, e.message)));
    return box;
  }

  // ---------- "What would I do?": a situation and options; the twin picks one, with reasons and certainty ----------
  VIEWS.decide = function (el) {
    const wrap = h("div", { class: "narrow stack" });
    const sit = h("textarea", { id: "dec-sit", rows: 3, placeholder: "e.g. I got two internship offers: one pays more in Bangalore, the other is research work at my college." });
    const list = h("div", { class: "stack dec-opts" });
    const addOpt = (v = "") => { const i = h("input", { type: "text", value: v, placeholder: `Option ${list.children.length + 1}`, "aria-label": `Option ${list.children.length + 1}` }); list.append(i); return i; };
    addOpt(); addOpt();
    const out = h("div", { class: "stack" });
    const go_ = h("button", { class: "btn", type: "button" }, "What would I do?");
    wrap.append(h("div", { class: "sec-head" }, h("span", { class: "eyebrow" }, "Decision helper"), h("h2", null, "What would I do?"),
      h("p", { class: "muted" }, "Describe a real choice and the options. Your twin picks the one you would, explains why using things you actually said, and says how sure it is. If nothing you told it is related, it says it doesn’t know.")),
      h("section", { class: "panel stack" }, h("label", { class: "field", for: "dec-sit" }, "The situation", sit), h("span", { class: "field" }, "The options"), list,
        h("div", { class: "row" }, h("button", { class: "btn quiet small", type: "button", onclick: () => list.children.length < 6 && addOpt().focus() }, "+ Add an option"), h("span", { class: "spacer" }), go_)),
      out);
    el.append(wrap);
    go_.addEventListener("click", async () => {
      const options = [...list.querySelectorAll("input")].map((i) => i.value.trim()).filter(Boolean);
      if (!sit.value.trim() || options.length < 2) return toast("Describe the situation and give at least two options.");
      if (S.mode !== "live") return toast("Available in the desktop app.");
      go_.disabled = true; go_.textContent = "Thinking…"; out.textContent = "";
      try {
        const r = await API.decide(sit.value.trim(), options);
        const cert = { high: "Quite sure", medium: "Fairly sure", low: "A guess", none: "Doesn’t know" }[r.certainty] || r.certainty;
        const maxS = Math.max(...r.support.map((s) => s.score), 0.01);
        out.append(h("article", { class: "ans " + (r.choice == null ? "def" : r.tier === "confident_answer" ? "conf" : "inf") },
          h("div", { class: "row" }, h("span", { class: "tier " + (r.choice == null ? "def" : r.tier === "confident_answer" ? "conf" : "inf") }, cert),
            r.mode ? h("span", { class: "mode-badge " + r.mode }, r.mode === "online" ? "online · " + (r.model || "gemini").replace(/^gemini-/, "gemini ") : "offline · this mac") : null,
            h("span", { class: "spacer" }), h("span", { class: "muted small num" }, r.seconds + " s")),
          r.choice == null ? h("p", { class: "ans-text" }, "I don’t know.") : h("p", { class: "ans-text" }, "I’d go with: ", h("b", null, r.options[r.choice])),
          h("ul", { class: "dec-reasons" }, ...r.reasons.map((x) => h("li", null, x))),
          h("div", { class: "stack dec-support" }, h("span", { class: "eyebrow" }, "How close each option is to what you’ve said"),
            ...r.support.map((s, i) => h("div", { class: "dec-row" + (i === r.choice ? " pick" : "") }, h("span", null, s.option),
              h("div", { class: "evbar", title: "Similarity " + fmt(s.score, 3) }, h("i", { style: `width:${Math.min(100, (100 * Math.max(0, s.score)) / GMAX)}%;background:${s.score < HARD ? "var(--def)" : s.score < SOFT ? "var(--inf)" : "var(--conf)"}` })),
              h("span", { class: "num small" }, fmt(s.score, 2))))),
          h("p", { class: "small muted" }, `Overall, the closest thing you’ve said scored ${fmt(r.confidence, 2)} (needs ${HARD} to answer, ${SOFT} to be sure).`),
          r.online_note ? h("p", { class: "small mode-note" }, "Gemini couldn’t answer (" + r.online_note + `), so the model on your ${DEVICE} chose.`) : null));
      } catch (e) { toast(e.message); }
      go_.disabled = false; go_.textContent = "What would I do?";
    });
  };

  // Your job and skills: questions about your field are answered from general professional knowledge
  function workPanel() {
    const box = h("section", { class: "panel stack work" });
    const title = h("input", { type: "text", id: "work-title", placeholder: "e.g. Civil engineer, final-year CSE student, nurse", maxlength: 80 });
    const skills = h("input", { type: "text", id: "work-skills", placeholder: "e.g. AutoCAD, structural design, site supervision, estimation" });
    const about = h("textarea", { id: "work-about", rows: 5, placeholder: "Optional: paste your CV, job report or a few lines about your work. Or choose a file." });
    const file = h("input", { type: "file", accept: ".pdf,.docx,.txt,.md", hidden: true });
    const status = h("p", { class: "small muted" });
    const save = h("button", { class: "btn", type: "button" }, "Save");
    const clear = h("button", { class: "btn ghost small", type: "button", hidden: true }, "Remove");
    box.append(h("h2", null, "Expert mode · your work and skills"),
      h("p", { class: "muted" }, "In Online mode, questions in your field are answered with Gemini’s expert knowledge, in your voice and style, and labelled “Expert knowledge · your field”, so it’s never passed off as something you said. Anything outside your field still gets the honest “I don’t know”. If you leave this empty, your job and skills are picked up from your interview answers."),
      h("label", { class: "field", for: "work-title" }, "Job or field", title),
      h("label", { class: "field", for: "work-skills" }, "Skills (comma-separated)", skills),
      h("label", { class: "field", for: "work-about" }, "CV / job report", about),
      h("div", { class: "row" }, save, file, h("button", { class: "btn quiet small", type: "button", onclick: () => file.click() }, "Choose a file (PDF, Word, text)"), h("span", { class: "spacer" }), clear),
      status);
    if (S.mode !== "live") { save.disabled = true; status.textContent = "Available in the desktop app."; return box; }
    const paint = (r) => {
      const w = r.work;
      clear.hidden = !w;
      if (w) { title.value = w.title; skills.value = (w.skills || []).join(", "); about.value = w.about || ""; }
      status.textContent = !w ? (S.status && S.status.online ? "Nothing yet: it will be picked up from your answers on your first question in Online mode." : "Turn on Online mode in Settings to use Expert mode.")
        : (w.auto ? "Picked up from your interview answers (edit it if it’s wrong). " : "") + (r.job && r.job.state === "making" ? `Learning what a ${w.title.toLowerCase()} knows…`
        : `✓ Ask it something about your field, for example a question a ${w.title.toLowerCase()} gets asked.` + (w.topics && w.topics.length ? ` (${w.topics.length} subjects recognised)` : "") + (S.status && S.status.online ? "" : " Expert answers need Online mode."));
    };
    API.work().then(paint).catch(() => {});
    save.addEventListener("click", async () => {
      save.disabled = true;
      try { const r = await API.workSave({ title: title.value, skills: skills.value, about: about.value }); paint(r); refresh();
        if (r.job && r.job.state === "making") { for (let i = 0; i < 30; i++) { await sleep(2000); const x = await API.work(); if (x.job.state !== "making") { paint(x); break; } } } }
      catch (e) { toast(e.message); }
      save.disabled = false;
    });
    file.addEventListener("change", async () => {
      const f = file.files[0]; if (!f) return;
      status.textContent = "Reading the file…";
      try { const r = await API.workUpload(f); about.value = r.text; status.textContent = r.truncated ? "Text added (shortened to fit). Press Save." : "Text added. Press Save."; }
      catch (e) { status.textContent = ""; toast(e.message); }
      file.value = "";
    });
    clear.addEventListener("click", async () => { await API.workDelete(); title.value = skills.value = about.value = ""; status.textContent = "Removed."; clear.hidden = true; });
    return box;
  }

  VIEWS.teach = function (el) {
    const wrap = h("div", { class: "cols" });
    const ta = h("textarea", { id: "noteBox", rows: 8, placeholder: "I moved to a new lab last month and I'm enjoying it.\nI say no to things far more than I used to.\nWhen a plan falls apart I'd rather rebuild it than push through." });
    const msg = h("div");
    const list = h("div", { class: "stack" });
    const add = h("button", { class: "btn", type: "button", onclick: async () => {
      try { const r = await API.note(ta.value); ta.value = ""; msg.textContent = ""; msg.append(h("p", { class: "okline" }, `✓ Added ${r.added.length} fact${r.added.length === 1 ? "" : "s"}. The twin uses them from your next question.`)); paintMem(); refresh(); }
      catch (e) { toast(e.message); }
    } }, "Add to memory");
    wrap.append(h("section", { class: "panel stack" }, h("h2", null, "Tell it about you"),
      h("p", { class: "muted" }, "The interview is a snapshot. Anything you add here goes straight into memory, outranks the interview on the same topic, and survives a restart. Nothing is retrained."),
      h("label", { class: "field", for: "noteBox" }, "One thought per line. Each line becomes its own fact.", ta), h("div", { class: "row" }, add), msg),
      h("section", { class: "panel stack" }, h("h2", null, "Everything you’ve added"), list));
    const profBox = h("section", { class: "panel stack", hidden: true });
    el.append(bigFivePanel(), workPanel(), profBox);
    (async () => {
      let o; try { o = await API.online(); } catch (e) { return; }
      if (!o.profile && !(o.enabled && o.has_key)) return;
      profBox.hidden = false;
      profBox.append(h("h2", null, "Your online profile"),
        h("p", { class: "muted small" }, o.profile ? `Written by ${o.profile.model} from your interview, corrections and notes${o.profile.fresh ? "" : " (out of date: it will be rewritten from what you added)"}. Online mode thinks from this.` : o.job.state === "making" ? "Gemini is writing it now…" : "It will be written the first time Online mode answers."),
        o.profile ? h("pre", { class: "profile-text" }, o.profile.text) : null,
        o.enabled && o.has_key ? h("div", { class: "row" }, h("button", { class: "btn quiet small", type: "button", onclick: async () => { try { await API.onlineProfile(); toast("Rewriting your profile…"); } catch (e) { toast(e.message); } } }, "Rewrite it")) : null);
    })();
    const gapList = h("div", { class: "stack" });
    const gaps = h("section", { class: "panel stack gaps", hidden: true }, h("h2", null, "Questions your twin couldn’t answer"),
      h("p", { class: "muted" }, "It refused these or had to guess. Answer a few and it will know next time."), gapList);
    el.append(gaps, wrap);
    (async () => {
      let g; try { g = await API.gaps(); } catch (e) { return; }
      const skipped = store.get("dt-gaps-skipped") || [];
      const items = (g.items || []).filter((it) => !skipped.includes(it.question));
      if (!items.length) return;
      gaps.hidden = false;
      items.forEach((it) => {
        const box = h("input", { type: "text", placeholder: "What would you say?", "aria-label": it.question });
        const row = h("div", { class: "gap" }, h("div", null, h("b", null, it.question), h("span", { class: "small muted" }, it.tier === "hard_defer" ? " · refused" : " · guessed")),
          h("div", { class: "row" }, box,
            h("button", { class: "btn small", type: "button", onclick: async () => {
              if (!box.value.trim()) return toast("Type what you would say.");
              try { await API.correct({ question: it.question, twin_answer: "", expected: box.value.trim(), tier: it.tier, confidence: it.confidence });
                row.replaceWith(h("p", { class: "okline small" }, `✓ Learned: “${it.question}”`)); paintMem(); refresh(); } catch (e) { toast(e.message); }
            } }, "Teach"),
            h("button", { class: "btn ghost small", type: "button", onclick: () => { store.set("dt-gaps-skipped", [...skipped, it.question]); row.remove(); } }, "Skip")));
        gapList.append(row);
      });
    })();
    async function paintMem() {
      list.textContent = "";
      try {
        const m = await API.memory();
        list.append(h("p", { class: "muted small" }, `${m.items.length} added by you, on top of ${m.interview_facts} interview answers.`));
        if (!m.items.length) list.append(h("p", { class: "muted" }, "Nothing yet. Corrections from the Ask tab show up here too."));
        m.items.forEach((it) => list.append(h("div", { class: "evi" }, h("span", { class: "src" }, it.kind === "correction" ? "fix" : "you"), h("span", null, it.text.replace("\n", " · ")))));
      } catch (e) { list.append(h("p", { class: "bad-line" }, e.message)); }
    }
    paintMem();
  };

  // ---------- profile ----------
  VIEWS.profile = async function (el) {
    el.append(h("p", { class: "muted" }, "Loading…"));
    let p; try { p = await API.profile(); } catch (e) { el.textContent = ""; el.append(h("p", { class: "bad-line" }, e.message)); return; }
    el.textContent = "";
    const traits = h("div", { class: "traits" });
    Object.entries(p.traits || {}).forEach(([t, d]) => {
      const poles = (p.poles || {})[t] || ["", ""];
      traits.append(h("div", { class: "trait" }, h("div", { class: "trait-top" }, h("b", null, t.replace("_", " ").replace(/^./, (c) => c.toUpperCase())), h("span", { class: "num muted small" }, `${d.score}/100 · ${d.band} · ${d.n_items} items`)),
        h("div", { class: "trait-bar" }, h("i", { style: `left:${d.score}%` })), h("div", { class: "poles" }, h("span", null, poles[0]), h("span", null, poles[1]))));
    });
    el.append(h("div", { class: "stack" },
      h("div", { class: "sec-head" }, h("span", { class: "eyebrow" }, `From ${p.n_answers} answers · computed, not guessed`), h("h2", null, `How ${p.name} tends to be`),
        h("p", { class: "muted" }, "Scored from the Likert items, so the same answers always give the same profile. On a question you never answered, the twin reasons from this and says that it is guessing.")),
      h("div", { class: "cols" }, h("section", { class: "panel stack" }, h("h3", null, "Disposition"), traits),
        h("section", { class: "panel md", html: markdown(p.markdown || "") }))));
  };

  // ---------- results ----------
  VIEWS.results = async function (el) {
    let r; try { r = await API.results(); } catch (e) { el.append(h("p", { class: "bad-line" }, e.message)); return; }
    const p1 = r.phase1;
    const tierName = { hard_defer: "Refuses (“I don’t know”)", soft_answer: "Infers (low confidence)", confident_answer: "Answers (confident)" };
    const evalBtn = h("button", { class: "btn", type: "button" }, r.eval && r.eval.state === "running" ? "Scoring…" : r.phase2 ? "Score again" : "Score the twin now");
    evalBtn.disabled = r.eval && r.eval.state === "running";
    evalBtn.addEventListener("click", async () => {
      try { await API.runEval(); evalBtn.disabled = true; evalBtn.textContent = "Scoring…";
        for (;;) { await sleep(2000); const x = await API.results(); if (x.eval.state !== "running") { go("results"); if (x.eval.state === "error") toast(x.eval.error); return; } } }
      catch (e) { toast(e.message); }
    });
    const p2 = r.phase2, bm = r.benchmark;
    el.append(h("div", { class: "stack" },
      h("div", { class: "sec-head" }, h("span", { class: "eyebrow" }, "Phase 1 · Twin-2K-500 benchmark"), h("h2", null, `${p1.participants} participants, ${p1.heldout_questions.toLocaleString()} held-out questions`)),
      h("div", { class: "kpis" },
        h("div", { class: "kpi" }, h("span", { class: "eyebrow" }, "Twin"), h("b", null, fmt(p1.twin_mean, 3)), h("span", { class: "small muted" }, `95% CI ${p1.twin_ci[0]} to ${p1.twin_ci[1]}`)),
        h("div", { class: "kpi" }, h("span", { class: "eyebrow" }, "Human re-test"), h("b", null, fmt(p1.human_ceiling, 3)), h("span", { class: "small muted" }, "same person, asked again")),
        h("div", { class: "kpi" }, h("span", { class: "eyebrow" }, "Confident only"), h("b", null, fmt(p1.selective.score, 3)), h("span", { class: "small muted" }, `at ${pct(p1.selective.coverage)} coverage, +${p1.selective.gain} per person`))),
      h("div", { class: "panel tbl-wrap" }, h("table", null, h("thead", null, h("tr", null, h("th", null, "Tier"), h("th", null, "Share"), h("th", null, "Similarity to true answer"))),
        h("tbody", null, p1.tiers.map((t) => h("tr", null, h("td", null, tierName[t.tier]), h("td", { class: "n" }, (t.share * 100).toFixed(1) + "%"), h("td", { class: "n" }, t.score == null ? "not scored" : fmt(t.score, 3))))))),
      h("p", { class: "muted small", style: "max-width:70ch" }, `Correction loop (${p1.feedback.participants} participants): five corrections lift similarity from ${p1.feedback.before} to ${p1.feedback.after5}. A control using another person’s answers lifts it to ${p1.feedback.control}, so most of the gain is learning the answer format. Scores compare sentence embeddings, so a full sentence scored against a one-word survey label is penalised even when it is right.`),
      r.figures.length ? h("div", { class: "figs" }, r.figures.map((f) => h("img", { src: "figures/" + f, alt: f.replace(/_/g, " ").replace(".png", "") }))) : null,
      h("div", { class: "sec-head", style: "margin-top:20px" }, h("span", { class: "eyebrow" }, "Phase 2 · the real subject"), h("h2", null, p2 ? `${p2.n_test} held-out questions the twin never saw` : "Score this twin on its held-out questions")),
      p2 ? h("div", { class: "kpis" },
        h("div", { class: "kpi" }, h("span", { class: "eyebrow" }, "Twin"), h("b", null, fmt(p2.twin_mean, 3)), h("span", { class: "small muted" }, "all answered questions")),
        h("div", { class: "kpi" }, h("span", { class: "eyebrow" }, "Your own ceiling"), h("b", null, fmt(p2.human_ceiling, 3)), h("span", { class: "small muted" }, "you vs. you, reworded")),
        h("div", { class: "kpi" }, h("span", { class: "eyebrow" }, "Of ceiling"), h("b", null, p2.pct_of_ceiling == null ? "n/a" : p2.pct_of_ceiling + "%"), h("span", { class: "small muted" }, `confident-only ${fmt(p2.confident_only_mean, 3)} at ${pct(p2.confident_coverage)}`)))
        : h("p", { class: "muted" }, "Runs every held-out question through the twin and compares it with your real answers and with your own consistency on the reworded repeats."),
      h("div", { class: "row" }, evalBtn),
      h("div", { class: "sec-head", style: "margin-top:20px" }, h("span", { class: "eyebrow" }, `This ${DEVICE}`), h("h2", null, "Measured speed")),
      bm ? h("div", { class: "kpis" },
        h("div", { class: "kpi" }, h("span", { class: "eyebrow" }, "Answer"), h("b", null, bm.llm ? fmt(bm.llm.warm_s || bm.llm.first_answer_s, 1) + " s" : "n/a"), h("span", { class: "small muted" }, bm.llm ? `model loads in ${fmt(bm.llm.load_s, 1)} s` : "")),
        h("div", { class: "kpi" }, h("span", { class: "eyebrow" }, "Voice"), h("b", null, bm.voice ? fmt(bm.voice.rtf, 1) + "×" : "n/a"), h("span", { class: "small muted" }, "seconds of work per second of speech")),
        h("div", { class: "kpi" }, h("span", { class: "eyebrow" }, "Lip-sync"), h("b", null, bm.lipsync ? fmt(bm.lipsync.per_audio_second, 0) + "×" : "n/a"), h("span", { class: "small muted" }, bm.lipsync_sharpen ? `${fmt(bm.lipsync_sharpen.per_audio_second, 0)}× with sharpening` : "without sharpening")))
        : h("p", { class: "muted" }, "Not measured yet. Setup’s self-test and the benchmark fill this in.")));
  };

  const ONLINE_CONSENT = `Optional Online mode, off by default: if you turn it on in Settings, your interview answers, corrections and notes (once, to write your profile) and each question you ask are sent to Google Gemini. Voice and video always stay on this ${DEVICE}.`;

  function mediaPanel() {
    const live = S.mode === "live", t = Date.now();
    return h("section", { class: "panel stack" }, h("h3", null, "Your photo and voice"),
      h("p", { class: "muted small" }, "Change them any time. What the twin knows stays the same; only how it looks and sounds changes."),
      h("div", { class: "media-row" },
        h("div", { class: "media-card" },
          hasPhoto() && live ? h("img", { class: "media-thumb", src: "api/upload/photo?t=" + t, alt: "Your current photo" }) : h("div", { class: "media-thumb empty" }, "No photo"),
          h("button", { class: "btn quiet small", type: "button", onclick: () => go("photo") }, hasPhoto() ? "Change photo" : "Add a photo")),
        h("div", { class: "media-card" },
          hasVoice() && live ? h("audio", { controls: true, src: "api/upload/voice?t=" + t }) : h("p", { class: "muted small" }, "No voice clip"),
          h("button", { class: "btn quiet small", type: "button", onclick: () => go("voice") }, hasVoice() ? "Change voice" : "Add your voice"))));
  }
  function onlinePanel() {
    const box = h("section", { class: "panel stack" }, h("h3", null, "Online mode · Google Gemini"), h("p", { class: "muted small" }, "Loading…"));
    const paint = async () => {
      let o; try { o = await API.online(); } catch (e) { box.lastChild.textContent = e.message; return; }
      const sw = h("input", { type: "checkbox", id: "set-online", disabled: !o.has_key }); sw.checked = o.enabled && o.has_key;
      sw.addEventListener("change", async () => { S.settings = await API.putSettings({ online: sw.checked }); toast(sw.checked ? "Online mode on." : "Online mode off."); paint(); });
      const key = h("input", { type: "password", id: "gemini-key", placeholder: "Paste your Gemini API key", autocomplete: "off", spellcheck: "false" });
      const save = h("button", { class: "btn small", type: "button", onclick: async () => {
        if (!key.value.trim()) return toast("Paste the key first.");
        save.disabled = true; save.textContent = "Checking…";
        try { await API.onlineKey(key.value.trim()); key.value = ""; toast(`Key saved in ${KEYSTORE}.`); paint(); }
        catch (e) { toast(e.message); save.disabled = false; save.textContent = "Save key"; }
      } }, "Save key");
      const prof = o.job && o.job.state === "making" ? "Gemini is writing your profile…" : o.job && o.job.state === "error" ? "Couldn't write the profile: " + o.job.error
        : o.profile ? `Profile ready${o.profile.fresh ? "" : " (will be rewritten with what you added)"}; it's on the Teach page.` : o.enabled ? "The profile will be written now." : "";
      box.replaceChildren(...[h("h3", null, "Online mode · Google Gemini"),
        h("label", { class: "check", for: "set-online" }, sw, h("span", null, h("b", null, "Online mode"), h("br"),
          h("span", { class: "small muted" }, "Off by default. The twin thinks from your whole profile with Gemini, still behind the same “I don’t know” rule. If there’s no internet, no key or no quota left, it quietly answers offline."))),
        h("p", { class: "small" }, ONLINE_CONSENT),
        o.has_key ? h("div", { class: "row" }, h("span", { class: "okline small" }, `✓ Key saved in ${KEYSTORE}`),
            h("button", { class: "btn ghost small", type: "button", onclick: async () => { await API.onlineKeyDelete(); toast("Key removed."); paint(); } }, "Remove key"))
          : h("div", { class: "stack" }, h("label", { class: "field", for: "gemini-key" }, h("span", null, "Gemini API key (from ", h("a", { href: "https://aistudio.google.com/apikey", target: "_blank", rel: "noopener" }, "aistudio.google.com"), ")"), key), h("div", { class: "row" }, save)),
        o.models ? h("p", { class: "small muted" }, `Tries ${o.models.pro || o.models.flash} first${o.models.pro && o.models.flash ? `, then ${o.models.flash} and the other Flash models when it's out of quota or busy (on Google's free tier, Pro usually has no quota, so Flash answers)` : ""}.`) : null,
        prof ? h("p", { class: "small muted" }, prof) : null].filter(Boolean));
    };
    paint();
    return box;
  }

  // ---------- settings ----------
  VIEWS.settings = function (el) {
    const caps = (S.status && S.status.media) || {}, est = caps.estimates || {};
    const name = (S.status && S.status.subject) || "";
    const wrap = h("div", { class: "stack" });
    const sw = (key, label, sub, ok) => {
      const c = h("input", { type: "checkbox", id: "set-" + key, disabled: ok === false }); c.checked = !!S.settings[key];
      c.addEventListener("change", async () => { S.settings = await API.putSettings({ [key]: c.checked }); toast("Saved."); });
      return h("label", { class: "check", for: "set-" + key }, c, h("span", null, h("b", null, label), h("br"), h("span", { class: "small muted" }, sub)));
    };
    const langSel = h("select", { class: "btn quiet small", id: "set-lang" }, h("option", { value: "en" }, "English"), h("option", { value: "hi", disabled: !caps.hindi }, "Hindi"));
    langSel.value = S.settings.lang || "en";
    langSel.addEventListener("change", async () => { S.settings = await API.putSettings({ lang: langSel.value }); toast("Saved."); });
    const missing = Object.entries((caps.installed) || {}).filter(([, v]) => !v).map(([k]) => k);
    wrap.append(h("div", { class: "sec-head" }, h("span", { class: "eyebrow" }, "Settings and data"), h("h2", null, "Settings")),
      h("div", { class: "cols" },
        h("section", { class: "panel stack" }, h("h3", null, "Defaults"),
          sw("speak", "Speak answers", "Your cloned voice, after the text appears.", caps.voice),
          sw("video", "Avatar video", est.video_seconds != null ? `Off by default. On this ${DEVICE} a typical answer takes ${minutes(est.video_seconds)} to animate; the text never waits for it.` : `Off by default. Slow on a ${DEVICE}; the time is measured by the self-test.`, caps.video),
          sw("welcome", "Greeting when the app opens", "Your avatar says hello in your voice before the opening page. Made once, then it plays straight away.", caps.video),
          sw("sharpen", "Sharpen the face", est.video_sharpen_seconds != null ? `GFPGAN on every frame: clearer, and ${minutes(est.video_sharpen_seconds)} per answer instead.` : "GFPGAN on every frame: clearer, and several times slower.", caps.sharpen),
          h("label", { class: "field" }, "Voice language", langSel),
          missing.length && S.status.bootstrap_url ? h("div", { class: "banner" }, "Not installed: ", missing.join(", "), ". ", installLink(missing.join(","), "Install now")) : null),
        demoPanel(caps, est)),
      mediaPanel(),
      onlinePanel(),
      h("section", { class: "panel stack" }, h("h3", null, "Your data"),
        h("p", { class: "muted small" }, "Everything is in ", h("code", null, (S.status && S.status.home) || "the app folder"), ` on this ${DEVICE}. Nothing is uploaded anywhere, unless you turn on Online mode above.`),
        h("div", { class: "row" },
          h("button", { class: "btn quiet", type: "button", onclick: async () => { try { const r = await API.exportData(); toast("Saved " + r.name + " to Downloads."); API.reveal(r.path).catch(() => {}); } catch (e) { toast(e.message); } } }, "Export my data"),
          h("button", { class: "btn quiet", type: "button", onclick: () => redoDialog() }, "Redo the interview"))),
      h("section", { class: "panel stack danger-zone" }, h("h3", null, "Delete"),
        confirmRow("Delete my twin", `Erases your answers, voice, photo, corrections, notes and every rendered clip. The downloaded models stay. Type ${name ? "your name" : "remove"} to confirm.`, name || "remove", async (v) => {
          await API.del(v); store.del(DKEY); S.draft = { consent: null, answers: {}, pos: null }; S.chat = []; await refresh(); toast("Your twin is deleted."); go("consent"); }),
        confirmRow("Remove everything", `Deletes the app’s whole folder: your twin and roughly 10 GB of models and environments. Then drag the app to the Bin. Type ${name ? "your name" : "remove"} to confirm.`, name || "remove", async (v) => {
          const r = await API.removeAll(v); store.del(DKEY); if (r.next) location.replace(r.next); else { document.body.textContent = ""; document.body.append(h("main", { class: "page narrow stack" }, h("h1", null, "Everything is removed"), h("p", null, `${r.home} was deleted. Drag the app to the Bin to finish.`))); } })));
    el.append(wrap);
  };
  function confirmRow(label, sub, want, act) {
    const inp = h("input", { type: "text", placeholder: want === "remove" ? "remove" : "Your full name", "aria-label": label + " confirmation" });
    const btn = h("button", { class: "btn danger", type: "button", disabled: true }, label);
    inp.addEventListener("input", () => (btn.disabled = inp.value.trim().toLowerCase().replace(/\s+/g, " ") !== want.trim().toLowerCase().replace(/\s+/g, " ")));
    btn.addEventListener("click", async () => { btn.disabled = true; try { await act(inp.value); } catch (e) { toast(e.message); btn.disabled = false; } });
    return h("div", { class: "stack", style: "gap:8px" }, h("b", null, label), h("p", { class: "small muted" }, sub), h("div", { class: "row" }, h("div", { style: "flex:1;min-width:200px" }, inp), btn));
  }
  function redoDialog() {
    const keep = h("input", { type: "checkbox", id: "keepmem" }); keep.checked = true;
    const d = h("dialog", { class: "panel stack" }, h("h3", null, "Redo the interview?"),
      h("p", { class: "muted small" }, "You start the questions again; your consent, voice and photo stay. The twin is rebuilt when you finish."),
      h("label", { class: "check", for: "keepmem" }, keep, h("span", null, "Keep my corrections and the things I told it directly")),
      h("div", { class: "row" }, h("button", { class: "btn", type: "button", onclick: async () => { try { await API.redo(keep.checked); S.draft.answers = {}; S.draft.pos = null; S.sec = 0; saveDraft(); d.close(); await refresh(); go("interview"); } catch (e) { toast(e.message); } } }, "Start again"),
        h("button", { class: "btn quiet", type: "button", onclick: () => d.close() }, "Cancel")));
    document.body.append(d); d.addEventListener("close", () => d.remove()); d.showModal();
  }
  function demoPanel(caps) {
    const box = h("section", { class: "panel stack" }, h("h3", null, "Prepare demo"),
      h("p", { class: "small muted" }, "Render avatar answers ahead of time, so they play instantly in front of an audience. Each one is cached by its answer text, and re-asking the same question plays the saved clip."));
    if (!caps.video) { box.append(h("p", { class: "small" }, caps.why || "The avatar needs a voice clip, a photo and the lip-sync model.")); return box; }
    const ta = h("textarea", { rows: 5, placeholder: "One question per line", "aria-label": "Demo questions" });
    ta.value = ["What do you do to relax?", "Your train is cancelled an hour before an exam. What do you do?", "What is your passport number?"].join("\n");
    const list = h("div", { class: "stack", style: "gap:6px" });
    const go1 = h("button", { class: "btn", type: "button", onclick: async () => { try { await API.demo({ questions: ta.value.split("\n"), lang: S.settings.lang }); poll(); } catch (e) { toast(e.message); } } }, "Render these");
    const addHeld = h("button", { class: "btn ghost small", type: "button", onclick: async () => { try { const r = await API.heldoutAll(); ta.value = (ta.value.trim() + "\n" + r.questions.slice(0, 5).join("\n")).trim(); } catch (e) { toast(e.message); } } }, "Add held-out questions");
    box.append(ta, h("div", { class: "row" }, go1, addHeld), list);
    async function poll() {
      for (;;) {
        let d; try { d = await API.demoStatus(); } catch (e) { return; }
        if (!document.body.contains(list)) return;
        list.textContent = "";
        d.items.forEach((it) => list.append(h("div", { class: "evi" }, h("span", { class: "s" }, it.ready ? "✓" : it.job && it.job.state === "error" ? "✕" : "…"),
          h("span", null, h("b", null, it.question), h("br"), h("span", { class: "small muted" }, it.ready ? "ready, plays instantly" : it.job ? (it.job.state === "video" ? (it.job.remaining_s ? `animating, ${minutes(it.job.remaining_s)} left` : "animating") : it.job.state === "error" ? it.job.error : "queued") : "answering…")))));
        if (d.state !== "running" && d.state !== "queued") return;
        await sleep(3000);
      }
    }
    poll();
    return box;
  }

  // ---------- splash, refresh, boot ----------
  async function refresh() {
    try { S.status = await API.status(); if (S.status.settings) S.settings = S.status.settings; } catch (e) { /* keep last */ }
    renderRail();
    return S.status;
  }
  function splash(msg) {
    let sp = $("#splash");
    if (!sp) { sp = h("div", { id: "splash", class: "splash", role: "status" }, h("div", { class: "pulse" }, $(".brand-mark").cloneNode(true)), h("h2", null, "Opening your twin"), h("p", { class: "muted small", id: "splashMsg" })); document.body.append(sp); }
    $("#splashMsg").textContent = msg;
  }
  async function boot() {
    try {
      const r = await fetch("api/status");
      if (!r.ok || !(r.headers.get("content-type") || "").includes("json")) throw 0;
      S.status = await r.json(); S.mode = "live"; API = live;
    } catch (e) { S.mode = "preview"; API = Sim; S.status = await Sim.status(); }
    if (S.status.settings) S.settings = S.status.settings;
    S.Q = await (await fetch("questions.json")).json();
    const local = store.get(DKEY);
    let server = {};
    if (S.mode === "live") { try { server = await API.getDraft(); } catch (e) {} }
    const count = (d) => (d && d.answers ? Object.keys(d.answers).length : -1);
    const best = S.mode === "live" && server && server.answers ? server : count(server) > count(local) ? server : local;
    if (best && (best.answers || best.consent)) S.draft = { consent: best.consent || null, answers: best.answers || {}, pos: best.pos || null };
    if (S.draft.pos && S.draft.pos.sec != null) S.sec = S.draft.pos.sec;
    // Later launches: a splash while the model loads and the twin rebuilds, then straight to Ask.
    if (S.mode === "live" && S.status.has_answers && !S.status.built) {
      for (let i = 0; i < 600; i++) {
        const st = S.status;
        if (st.built || st.build.state === "error" || (st.build.state === "idle" && !st.has_answers)) break;
        splash(st.llm.state !== "ready" ? "Loading the language model…" : "Loading your memory…");
        await sleep(400); await refresh();
      }
      const sp = $("#splash"); if (sp) sp.remove();
    }
    if (S.mode === "live") setInterval(async () => { const before = S.status && S.status.llm.state; await refresh(); if (before !== (S.status && S.status.llm.state) && S.view === "ask") paintSide(); }, 5000);
    const hash = location.hash.slice(1);
    if (S.status.built) go(TABS.some((t) => t[0] === hash && t[0] !== "home") ? hash : "home");
    else if (S.status.build && S.status.build.state === "running") go("build");
    else if (S.status.build && S.status.build.state === "error") go("build");
    else if (S.draft.consent) go(STEPS.some((t) => t[0] === hash) ? hash : nAnswered() >= 97 ? "voice" : "interview");
    else go("consent");
  }
  boot();
})();
