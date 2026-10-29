// Client-side filters, starring (localStorage) and copy-as-Markdown.
(function () {
  const STAR_KEY = "newsdesk.starred";

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
    star.textContent = on ? "★" : "☆";
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
      btn.textContent = on ? "★" : "☆";
    } else if (btn.classList.contains("copy") && item) {
      const d = item.dataset;
      const md = `**[${d.title}](${d.url})** — ${d.publisher}\n\n${d.summary || ""}`.trim() + "\n";
      navigator.clipboard.writeText(md).then(
        () => { btn.textContent = "Copied"; setTimeout(() => (btn.textContent = "Copy MD"), 1200); },
        () => { btn.textContent = "Copy failed"; }
      );
    } else if (btn.classList.contains("f")) {
      btn.setAttribute("aria-pressed", btn.getAttribute("aria-pressed") !== "true");
      applyFilters();
    }
  });

  const q = document.querySelector(".filters .q");
  if (q) q.addEventListener("input", applyFilters);

  function applyFilters() {
    const pressed = [...document.querySelectorAll(".filters .f[aria-pressed='true']")];
    const tracks = pressed.filter((b) => b.dataset.track).map((b) => b.dataset.track);
    const lang = pressed.find((b) => b.dataset.lang)?.dataset.lang;
    const starredOnly = pressed.some((b) => b.dataset.starred);
    const text = (q?.value || "").trim().toLowerCase();
    let shown = 0;
    items.forEach((el) => {
      const d = el.dataset;
      const ok =
        (!tracks.length || tracks.some((t) => d.tracks.split(" ").includes(t))) &&
        (!lang || d.lang === lang) &&
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
    if (count) count.textContent = `${shown} shown`;
  }
  applyFilters();
})();
