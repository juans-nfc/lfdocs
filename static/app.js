(() => {
  const $ = (id) => document.getElementById(id);
  const el = {
    title: $("title"), who: $("who"), signout: $("signout"),
    login: $("login"), loginForm: $("login-form"), loginRepo: $("login-repo"), loginMsg: $("login-msg"), loginBtn: $("login-btn"),
    username: $("username"), password: $("password"),
    search: $("search"), kinds: $("kinds"), searchForm: $("search-form"), q: $("q"), qLabel: $("q-label"), find: $("find"),
    status: $("status"), folder: $("folder"), folderName: $("folder-name"), folderPath: $("folder-path"), folderOpen: $("folder-open"),
    results: $("results"),
    settingsBtn: $("settings-btn"), settings: $("settings"), scopeRow: $("scope-row"), lkTable: $("lk-table"), lkBody: $("lk-table").querySelector("tbody"),
    lkAdd: $("lk-add"), lkReset: $("lk-reset"), lkCancel: $("lk-cancel"), lkSave: $("lk-save"), lkMsg: $("lk-msg"), lkHelp: $("lk-help"),
    picker: $("picker"), pickerTree: $("picker-tree"), pickerPath: $("picker-path"), pickerOk: $("picker-ok"), pickerCancel: $("picker-cancel"),
  };

  let cfg = null;
  let kind = null;
  let openWith = localStorage.getItem("lfdocs.openWith") || "web";   // "web" | "win"
  let currentFolder = null;

  // ---------- open with ----------
  const owBtns = () => Array.from(document.querySelectorAll(".seg button[data-ow]"));
  function setOpenWith(v) {
    openWith = v; localStorage.setItem("lfdocs.openWith", v);
    owBtns().forEach((b) => b.classList.toggle("active", b.dataset.ow === v));
    if (currentFolder) applyFolderButton(currentFolder);
    el.results.querySelectorAll("li").forEach((li) => { if (li._entry) applyEntryLink(li, li._entry); });
    if (v === "win" && !localStorage.getItem("lfdocs.lfeHintShown")) {
      localStorage.setItem("lfdocs.lfeHintShown", "1");
      status("Windows client: your browser downloads a small .lfe shortcut — choose \"Always open files of this type\" on it once and it becomes one click.", "warn");
    }
  }
  function download(url) {
    const a = document.createElement("a"); a.href = url; a.download = ""; a.style.display = "none";
    document.body.appendChild(a); a.click(); a.remove();
  }
  function openEntry(e) {
    if (openWith === "win") download(e.lfeUrl); else window.open(e.webUrl, "_blank", "noopener");
  }
  function applyFolderButton(folder) {
    if (openWith === "win") {
      el.folderOpen.textContent = "Open folder in Windows client";
      el.folderOpen.href = folder.lfeUrl; el.folderOpen.removeAttribute("target"); el.folderOpen.setAttribute("download", "");
    } else {
      el.folderOpen.textContent = "Open folder in web client";
      el.folderOpen.href = folder.webUrl; el.folderOpen.target = "_blank"; el.folderOpen.removeAttribute("download");
    }
  }
  function applyEntryLink(li, e) {
    const a = li.querySelector("a");
    if (openWith === "win") { a.href = e.lfeUrl; a.removeAttribute("target"); a.setAttribute("download", ""); }
    else { a.href = e.webUrl; a.target = "_blank"; a.removeAttribute("download"); }
    a.title = (e.isFolder ? "Open this folder" : "Open this document") + (openWith === "win" ? " in the Windows client" : " in the web client");
  }

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
    el.login.hidden = false; el.search.hidden = true; el.settings.hidden = true; el.signout.hidden = true; el.settingsBtn.hidden = true; el.who.textContent = "";
    el.loginMsg.textContent = msg || "";
    if (!el.username.value && cfg && cfg.username) el.username.value = cfg.username;
    if (!el.username.value) { const saved = localStorage.getItem("lfdocs.username"); if (saved) el.username.value = saved; }
    (el.username.value ? el.password : el.username).focus();
  }
  function showSearch(user) {
    el.login.hidden = true; el.search.hidden = false; el.settings.hidden = true; el.signout.hidden = false; el.settingsBtn.hidden = false; el.who.textContent = user || "";
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
  function clearResults() { el.results.innerHTML = ""; el.folder.hidden = true; currentFolder = null; }

  function showFolder(folder) {
    currentFolder = folder;
    el.folder.hidden = false;
    el.folderName.textContent = folder.name;
    el.folderPath.textContent = folder.fullPath || "";
    applyFolderButton(folder);
  }

  function renderEntries(entries, onFolder) {
    el.results.innerHTML = "";
    const sorted = entries.slice().sort((a, b) => (a.isFolder === b.isFolder ? a.name.localeCompare(b.name, undefined, { numeric: true }) : (a.isFolder ? -1 : 1)));
    for (const e of sorted) {
      const li = document.createElement("li"); li.className = e.isFolder ? "folder" : "doc"; li.tabIndex = 0;
      const ico = document.createElement("span"); ico.className = "ico"; ico.textContent = e.isFolder ? "📁" : "📄";
      const name = document.createElement("span"); name.className = "name";
      const a = document.createElement("a"); a.textContent = (e.subPath ? e.subPath + "\\" : "") + e.name; a.rel = "noopener";
      name.appendChild(a);
      li._entry = e;
      const meta = document.createElement("span"); meta.className = "meta";
      meta.textContent = e.isFolder ? "Folder" : [e.extension ? e.extension.toUpperCase() : e.entryType, e.pageCount ? e.pageCount + " p" : ""].filter(Boolean).join(" · ");
      const meta2 = document.createElement("span"); meta2.className = "meta wide"; meta2.textContent = fmtDate(e.modified);
      li.append(ico, name, meta, meta2);
      applyEntryLink(li, e);
      if (e.isFolder) {
        // click the row = browse into it here; click the name = open in Laserfiche
        li.onclick = (ev) => { if (ev.target !== a) onFolder(e); };
        li.onkeydown = (ev) => { if (ev.key === "Enter") onFolder(e); };
        a.onclick = (ev) => ev.stopPropagation();
      } else {
        li.onclick = (ev) => { if (ev.target !== a) openEntry(e); };
        li.onkeydown = (ev) => { if (ev.key === "Enter") openEntry(e); };
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
        status(r.matches.length + " results match " + r.pattern + " — pick one", "warn");
        return;
      }
      showFolder(r.folder);
      renderEntries(r.children, openFolderHere);
      const label = r.matchedFrom ? kind.label + " " + r.matchedFrom + " → " + r.folder.name : kind.label + " " + r.folder.name;
      status(label + ": " + r.children.length + " item" + (r.children.length === 1 ? "" : "s"), "ok");
    } catch (e) { if (!el.login.hidden) return; status(e.message, "err"); }
    finally { busy(false); el.q.select(); }
  }

  // ---------- settings (document types) ----------
  let lk = { mine: [], shared: [], isAdmin: false, hasCustom: false };
  let lkScope = "mine";
  let lkDraft = [];

  async function openSettings() {
    try { lk = await api("lookups"); } catch (e) { status(e.message, "err"); return; }
    lkScope = "mine";
    el.scopeRow.hidden = !lk.isAdmin;
    document.querySelectorAll("[data-scope]").forEach((b) => b.classList.toggle("active", b.dataset.scope === lkScope));
    loadDraft();
    el.search.hidden = true; el.settings.hidden = false; el.lkMsg.textContent = "";
  }
  function closeSettings() { el.settings.hidden = true; el.search.hidden = false; el.q.focus(); }
  function loadDraft() {
    lkDraft = (lkScope === "shared" ? lk.shared : lk.mine).map((k) => ({ ...k }));
    el.lkReset.hidden = !(lkScope === "mine" && lk.hasCustom);
    el.lkHelp.innerHTML = lkScope === "shared"
      ? "You are editing the <b>shared defaults</b> everyone starts with. Users who customized their own list keep theirs."
      : "Your own list — changes only affect you. Each row becomes a tab. <b>Match</b>: prefix = folder named the text or starting with it; contains = name contains the text; field = template field equals the text. <b>Sub</b> = search anywhere under the root.";
    renderDraft();
  }
  function renderDraft() {
    el.lkBody.innerHTML = "";
    lkDraft.forEach((k, i) => {
      const tr = document.createElement("tr");
      const td = (cls) => { const c = document.createElement("td"); if (cls) c.className = cls; tr.appendChild(c); return c; };
      const label = document.createElement("input"); label.type = "text"; label.value = k.label; label.placeholder = "Order"; label.oninput = () => (k.label = label.value);
      td().appendChild(label);
      const root = document.createElement("input"); root.type = "text"; root.value = k.root; root.placeholder = "\\Sales\\Orders"; root.oninput = () => (k.root = root.value);
      td().appendChild(root);
      const browse = document.createElement("button"); browse.type = "button"; browse.className = "icon"; browse.textContent = "…"; browse.title = "Browse the repository";
      browse.onclick = async () => { const p = await pickFolder(k.root); if (p) { k.root = p; root.value = p; if (!k.label) { k.label = p.split("\\").pop(); label.value = k.label; } } };
      td("tight").appendChild(browse);
      const match = document.createElement("select");
      for (const m of ["prefix", "contains", "field"]) { const o = document.createElement("option"); o.value = m; o.textContent = m; match.appendChild(o); }
      match.value = k.match || "prefix"; match.onchange = () => { k.match = match.value; field.disabled = k.match !== "field"; };
      td("tight").appendChild(match);
      const sub = document.createElement("input"); sub.type = "checkbox"; sub.checked = !!k.subfolders; sub.onchange = () => (k.subfolders = sub.checked);
      td("tight").appendChild(sub);
      const field = document.createElement("input"); field.type = "text"; field.value = k.field || ""; field.placeholder = "Employee ID"; field.disabled = (k.match || "prefix") !== "field";
      field.oninput = () => (k.field = field.value);
      td().appendChild(field);
      const ops = td("tight");
      const up = document.createElement("button"); up.type = "button"; up.className = "icon"; up.textContent = "▲"; up.disabled = i === 0; up.onclick = () => { [lkDraft[i - 1], lkDraft[i]] = [lkDraft[i], lkDraft[i - 1]]; renderDraft(); };
      const dn = document.createElement("button"); dn.type = "button"; dn.className = "icon"; dn.textContent = "▼"; dn.disabled = i === lkDraft.length - 1; dn.onclick = () => { [lkDraft[i + 1], lkDraft[i]] = [lkDraft[i], lkDraft[i + 1]]; renderDraft(); };
      const del = document.createElement("button"); del.type = "button"; del.className = "icon"; del.textContent = "✕"; del.title = "Remove"; del.onclick = () => { lkDraft.splice(i, 1); renderDraft(); };
      ops.append(up, dn, del);
      el.lkBody.appendChild(tr);
    });
  }
  async function saveDraft() {
    el.lkSave.disabled = true; el.lkMsg.textContent = "";
    try {
      const r = await api("lookups", { method: "PUT", body: JSON.stringify({ lookups: lkDraft, scope: lkScope }) });
      cfg.kinds = r.kinds; cfg.hasCustom = r.hasCustom;
      setKind(cfg.kinds.find((k) => k.id === (kind && kind.id)) || cfg.kinds[0]);
      closeSettings(); status("Document types saved.", "ok");
    } catch (e) { el.lkMsg.textContent = e.message; }
    finally { el.lkSave.disabled = false; }
  }
  async function resetMine() {
    if (!confirm("Go back to the shared defaults? Your own list will be removed.")) return;
    try {
      const r = await api("lookups", { method: "DELETE" });
      cfg.kinds = r.kinds; cfg.hasCustom = false;
      setKind(cfg.kinds.find((k) => k.id === (kind && kind.id)) || cfg.kinds[0]);
      closeSettings(); status("Back to the shared defaults.", "ok");
    } catch (e) { el.lkMsg.textContent = e.message; }
  }

  // ---------- folder picker ----------
  function pickFolder(startPath) {
    return new Promise((resolve) => {
      let selected = null;
      el.pickerTree.innerHTML = ""; el.pickerPath.textContent = ""; el.pickerOk.disabled = true;
      const finish = (v) => { el.picker.hidden = true; el.pickerOk.onclick = null; el.pickerCancel.onclick = null; resolve(v); };
      el.pickerOk.onclick = () => finish(selected);
      el.pickerCancel.onclick = () => finish(null);
      el.picker.hidden = false;

      const makeNode = (folder, depth) => {
        const li = document.createElement("li");
        const node = document.createElement("div"); node.className = "node";
        const tw = document.createElement("span"); tw.className = "tw"; tw.textContent = "▸";
        const name = document.createElement("span"); name.textContent = folder.name || "\\ (repository root)";
        node.append(tw, name); li.appendChild(node);
        let ul = null, loaded = false;
        const expand = async () => {
          if (!loaded) {
            loaded = true; tw.textContent = "…";
            try {
              const r = await api("subfolders/" + folder.id);
              ul = document.createElement("ul");
              for (const f of r.folders) { if (!f.fullPath) f.fullPath = (folder.fullPath === "\\" ? "" : folder.fullPath) + "\\" + f.name; ul.appendChild(makeNode(f, depth + 1)); }
              li.appendChild(ul);
              tw.textContent = r.folders.length ? "▾" : "·";
            } catch (e) { tw.textContent = "!"; loaded = false; el.pickerPath.textContent = e.message; }
          } else if (ul) { ul.hidden = !ul.hidden; tw.textContent = ul.hidden ? "▸" : "▾"; }
        };
        tw.onclick = (ev) => { ev.stopPropagation(); expand(); };
        node.onclick = () => {
          el.pickerTree.querySelectorAll(".node.selected").forEach((n) => n.classList.remove("selected"));
          node.classList.add("selected"); selected = folder.fullPath; el.pickerPath.textContent = selected; el.pickerOk.disabled = false;
          if (!loaded) expand();
        };
        node.ondblclick = () => { if (selected) finish(selected); };
        li._expandTo = async (segs) => {
          if (!segs.length) { node.click(); return; }
          await expand();
          if (!ul) return;
          const next = Array.from(ul.children).find((c) => c._name.toLowerCase() === segs[0].toLowerCase());
          if (next) await next._expandTo(segs.slice(1)); else node.click();
        };
        li._name = folder.name;
        return li;
      };
      const rootUl = document.createElement("ul");
      const rootLi = makeNode({ id: 1, name: "", fullPath: "\\" }, 0);
      rootUl.appendChild(rootLi); el.pickerTree.appendChild(rootUl);
      const segs = (startPath || "").split("\\").filter(Boolean);
      rootLi._expandTo(segs);
    });
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
  el.settingsBtn.addEventListener("click", openSettings);
  el.lkCancel.addEventListener("click", closeSettings);
  el.lkSave.addEventListener("click", saveDraft);
  el.lkReset.addEventListener("click", resetMine);
  el.lkAdd.addEventListener("click", () => { lkDraft.push({ id: "", label: "", root: "", match: "prefix", field: "", subfolders: false }); renderDraft(); el.lkBody.querySelector("tr:last-child input").focus(); });
  document.querySelectorAll("[data-scope]").forEach((b) => (b.onclick = () => { lkScope = b.dataset.scope; document.querySelectorAll("[data-scope]").forEach((x) => x.classList.toggle("active", x === b)); loadDraft(); }));
  el.signout.addEventListener("click", async () => { try { await api("logout", { method: "POST" }); } catch {} clearResults(); status(""); showLogin(); });
  el.searchForm.addEventListener("submit", (ev) => { ev.preventDefault(); lookup(); });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "/" && document.activeElement !== el.q && el.login.hidden && el.settings.hidden && el.picker.hidden) { ev.preventDefault(); el.q.focus(); el.q.select(); }
    if (ev.key === "Escape" && !el.picker.hidden) { el.pickerCancel.click(); }
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
    owBtns().forEach((b) => (b.onclick = () => setOpenWith(b.dataset.ow)));
    owBtns().forEach((b) => b.classList.toggle("active", b.dataset.ow === openWith));
    if (cfg.signedIn) showSearch(cfg.username); else showLogin();
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js", { scope: "./" }).catch(() => {});
  })();
})();
