(() => {
  const $ = (id) => document.getElementById(id);
  const el = {
    title: $("title"), who: $("who"), signout: $("signout"),
    login: $("login"), loginForm: $("login-form"), loginRepo: $("login-repo"), loginMsg: $("login-msg"), loginBtn: $("login-btn"),
    username: $("username"), password: $("password"),
    search: $("search"), kinds: $("kinds"), searchForm: $("search-form"), q: $("q"), qLabel: $("q-label"), find: $("find"),
    status: $("status"), folder: $("folder"), folderName: $("folder-name"), folderPath: $("folder-path"), folderOpen: $("folder-open"),
    results: $("results"),
  };

  let cfg = null;
  let kind = null;

  // ---------- helpers ----------
  const api = async (path, opts = {}) => {
    const r = await fetch("api/" + path, { credentials: "same-origin", headers: { "Accept": "application/json", ...(opts.body ? { "Content-Type": "application/json" } : {}) }, ...opts });
    let j = null;
    try { j = await r.json(); } catch { /* non-JSON */ }
    if (r.status === 401) { showLogin(j && j.error); throw new Error(j && j.error || "Not signed in"); }
    if (!r.ok) throw new Error((j && (j.error || j.detail)) || ("HTTP " + r.status));
    return j;
  };
  const status = (text, cls) => { el.status.textContent = text || ""; el.status.className = "status " + (cls || ""); };
  const fmtDate = (iso) => { if (!iso) return ""; const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" }); };
  const busy = (b) => { el.find.disabled = b; el.q.disabled = b; document.body.style.cursor = b ? "progress" : ""; };

  // ---------- views ----------
  function showLogin(msg) {
    el.login.hidden = false; el.search.hidden = true; el.signout.hidden = true; el.who.textContent = "";
    el.loginMsg.textContent = msg || "";
    if (!el.username.value && cfg && cfg.username) el.username.value = cfg.username;
    if (!el.username.value) { const saved = localStorage.getItem("lfdocs.username"); if (saved) el.username.value = saved; }
    (el.username.value ? el.password : el.username).focus();
  }
  function showSearch(user) {
    el.login.hidden = true; el.search.hidden = false; el.signout.hidden = false; el.who.textContent = user || "";
    el.q.focus(); el.q.select();
  }
  function renderKinds() {
    el.kinds.innerHTML = "";
    el.kinds.hidden = cfg.kinds.length < 2;
    for (const k of cfg.kinds) {
      const b = document.createElement("button"); b.type = "button"; b.textContent = k.label + "s";
      b.className = k.id === kind.id ? "active" : ""; b.onclick = () => { setKind(k); el.q.focus(); };
      el.kinds.appendChild(b);
    }
  }
  function setKind(k) {
    kind = k; localStorage.setItem("lfdocs.kind", k.id);
    el.qLabel.textContent = k.label + " #"; el.q.placeholder = "e.g. 80670";
    renderKinds(); clearResults(); status("");
  }
  function clearResults() { el.results.innerHTML = ""; el.folder.hidden = true; }

  function showFolder(folder) {
    el.folder.hidden = false;
    el.folderName.textContent = folder.name;
    el.folderPath.textContent = folder.fullPath || "";
    el.folderOpen.href = folder.webUrl;
  }

  function renderEntries(entries, onFolder) {
    el.results.innerHTML = "";
    const sorted = entries.slice().sort((a, b) => (a.isFolder === b.isFolder ? a.name.localeCompare(b.name, undefined, { numeric: true }) : (a.isFolder ? -1 : 1)));
    for (const e of sorted) {
      const li = document.createElement("li"); li.className = e.isFolder ? "folder" : "doc"; li.tabIndex = 0;
      const ico = document.createElement("span"); ico.className = "ico"; ico.textContent = e.isFolder ? "📁" : "📄";
      const name = document.createElement("span"); name.className = "name";
      const a = document.createElement("a"); a.textContent = e.name; a.href = e.webUrl; a.target = "_blank"; a.rel = "noopener";
      a.title = e.isFolder ? "Open this folder in Laserfiche" : "Open in the Laserfiche document viewer";
      name.appendChild(a);
      const meta = document.createElement("span"); meta.className = "meta";
      meta.textContent = e.isFolder ? "Folder" : [e.extension ? e.extension.toUpperCase() : e.entryType, e.pageCount ? e.pageCount + " p" : ""].filter(Boolean).join(" · ");
      const meta2 = document.createElement("span"); meta2.className = "meta wide"; meta2.textContent = fmtDate(e.modified);
      li.append(ico, name, meta, meta2);
      if (e.isFolder) {
        // click the row = browse into it here; click the name = open in Laserfiche
        li.onclick = (ev) => { if (ev.target !== a) onFolder(e); };
        li.onkeydown = (ev) => { if (ev.key === "Enter") onFolder(e); };
        a.onclick = (ev) => ev.stopPropagation();
      } else {
        li.onclick = (ev) => { if (ev.target !== a) window.open(e.webUrl, "_blank", "noopener"); };
        li.onkeydown = (ev) => { if (ev.key === "Enter") window.open(e.webUrl, "_blank", "noopener"); };
      }
      el.results.appendChild(li);
    }
  }

  async function openFolderHere(folder) {
    busy(true); status("Loading " + folder.name + " …");
    try {
      const r = await api("folder/" + folder.id);
      showFolder(folder);
      renderEntries(r.children, openFolderHere);
      status(folder.name + ": " + r.children.length + " item" + (r.children.length === 1 ? "" : "s"), "ok");
    } catch (e) { status(e.message, "err"); }
    finally { busy(false); }
  }

  // ---------- lookup ----------
  async function lookup() {
    const q = el.q.value.trim();
    if (!q) { el.q.focus(); return; }
    clearResults(); busy(true); status("Looking up " + q + " …");
    try {
      const r = await api("lookup/" + encodeURIComponent(kind.id) + "?q=" + encodeURIComponent(q));
      if (!r.found) { status(r.message, "err"); return; }
      if (r.multiple) {
        renderEntries(r.matches, openFolderHere);
        status(r.matches.length + " folders match " + r.pattern + " — pick one", "warn");
        return;
      }
      showFolder(r.folder);
      renderEntries(r.children, openFolderHere);
      const label = r.matchedFrom ? kind.label + " " + r.matchedFrom + " → " + r.folder.name : kind.label + " " + r.folder.name;
      status(label + ": " + r.children.length + " item" + (r.children.length === 1 ? "" : "s"), "ok");
    } catch (e) { if (!el.login.hidden) return; status(e.message, "err"); }
    finally { busy(false); el.q.select(); }
  }

  // ---------- events ----------
  el.loginForm.addEventListener("submit", async (ev) => {
    ev.preventDefault(); el.loginBtn.disabled = true; el.loginMsg.textContent = "";
    try {
      const r = await api("login", { method: "POST", body: JSON.stringify({ username: el.username.value.trim(), password: el.password.value }) });
      localStorage.setItem("lfdocs.username", r.username);
      el.password.value = "";
      showSearch(r.username);
    } catch (e) { el.loginMsg.textContent = e.message; el.password.focus(); el.password.select(); }
    finally { el.loginBtn.disabled = false; }
  });
  el.signout.addEventListener("click", async () => { try { await api("logout", { method: "POST" }); } catch {} clearResults(); status(""); showLogin(); });
  el.searchForm.addEventListener("submit", (ev) => { ev.preventDefault(); lookup(); });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "/" && document.activeElement !== el.q && el.login.hidden) { ev.preventDefault(); el.q.focus(); el.q.select(); }
    if (ev.key === "Escape" && document.activeElement === el.q) { el.q.value = ""; }
  });

  // ---------- init ----------
  (async () => {
    try {
      cfg = await api("config");
    } catch (e) { status("Cannot reach LF Docs: " + e.message, "err"); el.search.hidden = false; return; }
    el.title.textContent = cfg.title; document.title = cfg.title;
    el.loginRepo.textContent = "Repository: " + cfg.repository;
    const savedKind = localStorage.getItem("lfdocs.kind");
    setKind(cfg.kinds.find((k) => k.id === savedKind) || cfg.kinds[0]);
    if (cfg.signedIn) showSearch(cfg.username); else showLogin();
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js", { scope: "./" }).catch(() => {});
  })();
})();
