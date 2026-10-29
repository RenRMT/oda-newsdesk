// Client-side filters, starring (localStorage), copy-as-Markdown and theme toggle.
(function () {
  const STAR_KEY = "newsdesk.starred";
  const THEME_KEY = "newsdesk.theme";

  const toggle = document.querySelector(".theme-toggle");
  if (toggle) {
    const t = document.documentElement.dataset.theme;
    const dark = t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    toggle.setAttribute("aria-pressed", dark);
  }

  function loadStars() {
    try { return new Set(JSON.parse(localStorage.getItem(STAR_KEY) || "[]")); }
    catch { return new Set(); }
  }
  function saveStars(stars) {
    try { localStorage.setItem(STAR_KEY, JSON.stringify([...stars])); } catch { /* storage unavailable */ }
  }

  const stars = loadStars();
  const items = [...document.querySelectorAll(".item")];

  items.forEach((el) => {
    const star = el.querySelector(".star");
    const on = stars.has(el.dataset.id);
    star.setAttribute("aria-pressed", on);
    star.textContent = on ? "Starred" : "Star";
  });

  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button");
    if (!btn) return;
    const item = btn.closest(".item");
    if (btn.classList.contains("star") && item) {
      const id = item.dataset.id;
      const on = !stars.has(id);
      on ? stars.add(id) : stars.delete(id);
      saveStars(stars);
      btn.setAttribute("aria-pressed", on);
      btn.textContent = on ? "Starred" : "Star";
    } else if (btn.classList.contains("copy") && item) {
      const d = item.dataset;
      let md = `**[${d.title}](${d.url})** — ${d.publisher}\n\n${d.summary || ""}`.trim() + "\n";
      if (d.hook) md += `\n> Angle: ${d.hook}\n`;
      navigator.clipboard.writeText(md).then(
        () => { btn.textContent = "Copied"; setTimeout(() => (btn.textContent = "Copy as Markdown"), 1200); },
        () => { btn.textContent = "Clipboard not available"; }
      );
    } else if (btn.classList.contains("theme-toggle")) {
      const dark = btn.getAttribute("aria-pressed") !== "true";
      document.documentElement.dataset.theme = dark ? "dark" : "light";
      btn.setAttribute("aria-pressed", dark);
      try { localStorage.setItem(THEME_KEY, dark ? "dark" : "light"); } catch { /* storage unavailable */ }
    } else if (btn.classList.contains("f")) {
      btn.setAttribute("aria-pressed", btn.getAttribute("aria-pressed") !== "true");
      applyFilters();
    }
  });

  const q = document.querySelector(".filters .q");
  if (q) q.addEventListener("input", applyFilters);
  const typeSel = document.querySelector(".filters .type-filter");
  if (typeSel) typeSel.addEventListener("change", applyFilters);

  function applyFilters() {
    const pressed = [...document.querySelectorAll(".filters .f[aria-pressed='true']")];
    const tracks = pressed.filter((b) => b.dataset.track).map((b) => b.dataset.track);
    const lang = pressed.find((b) => b.dataset.lang)?.dataset.lang;
    const starredOnly = pressed.some((b) => b.dataset.starred);
    const text = (q?.value || "").trim().toLowerCase();
    const type = typeSel?.value || "";
    let shown = 0;
    items.forEach((el) => {
      const d = el.dataset;
      const ok =
        (!tracks.length || tracks.some((t) => d.tracks.split(" ").includes(t))) &&
        (!lang || d.lang === lang) &&
        (!type || d.type === type) &&
        (!starredOnly || stars.has(d.id)) &&
        (!text || (d.title + " " + d.publisher + " " + d.summary).toLowerCase().includes(text));
      el.hidden = !ok;
      if (ok) shown++;
    });
    document.querySelectorAll(".day").forEach((h) => {
      let n = h.nextElementSibling, any = false;
      while (n && !n.classList.contains("day")) { if (!n.hidden) any = true; n = n.nextElementSibling; }
      h.hidden = !any;
    });
    const count = document.querySelector(".filters .count");
    if (count) count.textContent = `${shown} of ${items.length} shown`;
  }
  applyFilters();
})();
