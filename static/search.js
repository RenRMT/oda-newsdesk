// Client-side search over data/recent.json with MiniSearch.
(function () {
  const root = document.querySelector(".search");
  const input = document.getElementById("search-q");
  const list = root.querySelector(".results");
  const count = root.querySelector(".count");
  const LIMIT = 50;
  let index = null;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text) e.textContent = text;
    return e;
  }

  function card(doc) {
    const art = el("article", "item");
    const meta = el("div", "meta");
    meta.append(el("span", "pub", doc.publisher_name));
    const time = el("time", "", new Date(doc.published).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }));
    time.dateTime = doc.published;
    meta.append(time);
    if (doc.lang !== "en") meta.append(el("span", "lang", doc.lang.toUpperCase()));
    if (doc.type) meta.append(el("span", "type", doc.type));
    if (doc.region) meta.append(el("span", "region", doc.region));
    (doc.tracks || []).forEach((t) => meta.append(el("span", "tag tag-" + t, t)));
    const h = el("h3");
    const a = el("a", "", doc.title);
    a.href = doc.url; a.target = "_blank"; a.rel = "noopener";
    h.append(a);
    art.append(meta, h);
    if (doc.summary) art.append(el("p", "summary", doc.summary));
    if (doc.hook) {
      const p = el("p", "hook");
      p.append(el("span", "key", "Hook"), el("span", "", doc.hook));
      art.append(p);
    }
    return art;
  }

  function run() {
    const q = input.value.trim();
    const url = new URL(location.href);
    q ? url.searchParams.set("q", q) : url.searchParams.delete("q");
    history.replaceState(null, "", url);
    list.replaceChildren();
    if (!index) return;
    if (!q) { count.textContent = `${index.documentCount} items indexed`; return; }
    const hits = index.search(q);
    count.textContent = hits.length > LIMIT ? `${LIMIT} of ${hits.length} matches` : `${hits.length} matches`;
    hits.slice(0, LIMIT).forEach((h) => list.append(card(h)));
  }

  fetch(root.dataset.src)
    .then((r) => r.json())
    .then((items) => {
      index = new MiniSearch({
        fields: ["title", "summary", "publisher_name", "region"],
        storeFields: ["title", "url", "publisher_name", "published", "lang", "tracks", "type", "region", "summary", "hook"],
        searchOptions: { boost: { title: 2 }, prefix: true, fuzzy: 0.2, combineWith: "AND" },
      });
      index.addAll(items.map((i) => {
        const e = i.enrichment || {};
        return {
          id: i.id, title: i.title, url: i.url, publisher_name: i.publisher_name, published: i.published,
          lang: i.lang, tracks: i.tracks, type: e.type || "", region: e.region || "",
          summary: e.summary_en || i.excerpt || "", hook: e.hook && e.hook.flag ? e.hook.angle : "",
        };
      }));
      input.value = new URLSearchParams(location.search).get("q") || input.value;
      run();
    })
    .catch(() => { count.textContent = "Search index could not be loaded."; });

  let timer;
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 120); });
})();
