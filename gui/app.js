const els = {
  form: document.getElementById("search-form"),
  query: document.getElementById("query"),
  searchStatus: document.getElementById("search-status"),
  results: document.getElementById("results"),
  showPanel: document.getElementById("show-panel"),
  showTitle: document.getElementById("show-title"),
  showMeta: document.getElementById("show-meta"),
  episodes: document.getElementById("episodes"),
  outDir: document.getElementById("out-dir"),
  pickFolder: document.getElementById("pick-folder"),
  selectAll: document.getElementById("select-all"),
  downloadBtn: document.getElementById("download-btn"),
  downloadStatus: document.getElementById("download-status"),
  saveLibrary: document.getElementById("save-library"),
};

let currentFeed = null;
let downloadCancel = false;

function setStatus(el, message, isError = false) {
  if (!message) {
    el.hidden = true;
    el.textContent = "";
    el.classList.remove("error");
    return;
  }
  el.hidden = false;
  el.textContent = message;
  el.classList.toggle("error", isError);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail || res.statusText || "Request failed";
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

async function init() {
  const defaults = await api("/api/defaults");
  els.outDir.value = defaults.out_dir;
  if (!defaults.podcastindex_configured) {
    setStatus(
      els.searchStatus,
      "Optional: add free Podcast Index keys (see section below) for much wider RSS search."
    );
  }
  els.query.focus();
}

els.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = els.query.value.trim();
  if (!query) return;

  els.results.innerHTML = "";
  els.showPanel.hidden = true;

  if (/open\.spotify\.com\/show\//i.test(query)) {
    setStatus(els.searchStatus, "Resolving Spotify show to RSS…");
    try {
      const data = await api("/api/search", {
        method: "POST",
        body: JSON.stringify({ query, limit: 5 }),
      });
      if (!data.results.length) {
        setStatus(
          els.searchStatus,
          "Could not find a public RSS for that Spotify show. It may be Spotify-only.",
          true
        );
        return;
      }
      setStatus(els.searchStatus, `Resolved via ${data.results[0].source}.`);
      for (const item of data.results) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "result";
        btn.innerHTML = `
          <div>
            <h3></h3>
            <p></p>
            <span class="badge"></span>
          </div>
          <span class="open-label">Open</span>
        `;
        btn.querySelector("h3").textContent = item.name;
        btn.querySelector("p").textContent = `${item.artist}\n${item.feed}`;
        btn.querySelector(".badge").textContent = item.source || "spotify";
        btn.addEventListener("click", () => openFeed(item));
        els.results.appendChild(btn);
      }
      await openFeed(data.results[0]);
    } catch (err) {
      setStatus(els.searchStatus, err.message, true);
    }
    return;
  }

  if (/youtube\.com|youtu\.be/i.test(query)) {
    setStatus(els.searchStatus, "Opening YouTube link…");
    await openFeed({ name: query, artist: "YouTube", feed: query, source: "youtube" });
    return;
  }

  if (/^https?:\/\//i.test(query)) {
    setStatus(els.searchStatus, "Opening RSS URL…");
    await openFeed({ name: query, artist: "", feed: query, source: "url" });
    return;
  }

  setStatus(els.searchStatus, "Searching…");

  try {
    const data = await api("/api/search", {
      method: "POST",
      body: JSON.stringify({ query, limit: 20 }),
    });

    const notes = (data.notes || []).join("\n");
    if (!data.results.length) {
      setStatus(
        els.searchStatus,
        notes ||
          "No RSS results found. Paste a full RSS / YouTube URL, or add Podcast Index keys for wider search.",
        true
      );
      return;
    }

    setStatus(
      els.searchStatus,
      `Found ${data.results.length} result(s). Click one to open.` +
        (notes ? `\n\n${notes}` : "")
    );
    for (const item of data.results) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "result";
      const eps = item.episodes ? ` · ~${item.episodes} eps` : "";
      btn.innerHTML = `
        <div>
          <h3></h3>
          <p></p>
          <span class="badge"></span>
        </div>
        <span class="open-label">Open</span>
      `;
      btn.querySelector("h3").textContent = item.name;
      btn.querySelector("p").textContent = `${item.artist}${eps}\n${item.feed}`;
      btn.querySelector(".badge").textContent = item.source || "web";
      btn.addEventListener("click", () => openFeed(item));
      els.results.appendChild(btn);
    }
  } catch (err) {
    setStatus(els.searchStatus, err.message, true);
  }
});

document.getElementById("save-pi")?.addEventListener("click", async () => {
  const api_key = document.getElementById("pi-key").value.trim();
  const api_secret = document.getElementById("pi-secret").value.trim();
  const piStatus = document.getElementById("pi-status");
  if (!api_key || !api_secret) {
    setStatus(piStatus, "Enter both key and secret.", true);
    return;
  }
  try {
    await api("/api/settings/podcastindex", {
      method: "POST",
      body: JSON.stringify({ api_key, api_secret }),
    });
    setStatus(piStatus, "Podcast Index keys saved. Search again for wider results.");
  } catch (err) {
    setStatus(piStatus, err.message, true);
  }
});
async function openFeed(item) {
  setStatus(els.searchStatus, `Loading episodes for “${item.name}”…`);
  try {
    const feed = await api("/api/feed", {
      method: "POST",
      body: JSON.stringify({ rss: item.feed }),
    });
    currentFeed = {
      ...feed,
      searchName: item.name,
      searchArtist: item.artist || "",
      source: item.source || "",
    };
    renderShow(currentFeed);
    setStatus(els.searchStatus, `Loaded “${feed.title}” (${feed.episodes.length} listed).`);
    els.showPanel.hidden = false;
    els.showPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    setStatus(els.searchStatus, err.message, true);
  }
}

function renderShow(feed) {
  els.showTitle.textContent = feed.title;
  els.showMeta.textContent = `${feed.episode_count} items · ${feed.kind === "youtube" ? "YouTube" : "RSS"}: ${feed.feed}`;
  els.episodes.innerHTML = "";
  els.selectAll.checked = true;
  setStatus(els.downloadStatus, "");

  for (const ep of feed.episodes) {
    const row = document.createElement("label");
    row.className = `episode${ep.has_audio ? "" : " disabled"}`;
    row.innerHTML = `
      <input type="checkbox" class="ep-check" />
      <div>
        <h3></h3>
        <p class="ep-audio"></p>
      </div>
      <p class="ep-date"></p>
    `;
    const check = row.querySelector(".ep-check");
    check.value = String(ep.index);
    check.checked = ep.has_audio;
    check.disabled = !ep.has_audio;
    row.querySelector("h3").textContent = `${ep.index}. ${ep.title}`;
    row.querySelector(".ep-audio").textContent = ep.has_audio ? "Audio ready" : "No audio enclosure";
    row.querySelector(".ep-date").textContent = ep.published || "";
    els.episodes.appendChild(row);
  }
}

els.selectAll.addEventListener("change", () => {
  const on = els.selectAll.checked;
  for (const box of els.episodes.querySelectorAll(".ep-check:not(:disabled)")) {
    box.checked = on;
  }
});

els.pickFolder.addEventListener("click", async () => {
  setStatus(els.downloadStatus, "Opening folder dialog…");
  try {
    const data = await api("/api/pick-folder", { method: "POST", body: "{}" });
    if (data.cancelled) {
      setStatus(els.downloadStatus, "Folder selection cancelled.");
      return;
    }
    els.outDir.value = data.path;
    setStatus(els.downloadStatus, `Folder set to:\n${data.path}`);
  } catch (err) {
    setStatus(els.downloadStatus, err.message, true);
  }
});

els.downloadBtn.addEventListener("click", async () => {
  if (!currentFeed) return;
  const indices = [...els.episodes.querySelectorAll(".ep-check:checked")].map((el) =>
    Number(el.value)
  );
  if (!indices.length) {
    setStatus(els.downloadStatus, "Select at least one episode.", true);
    return;
  }
  const outDir = els.outDir.value.trim();
  if (!outDir) {
    setStatus(els.downloadStatus, "Choose a download folder.", true);
    return;
  }

  if (indices.length > 30) {
    const ok = window.confirm(
      `You selected ${indices.length} episodes.\n` +
        "This can take a long time. Progress will update after each episode.\n\nContinue?"
    );
    if (!ok) return;
  }

  downloadCancel = false;
  els.downloadBtn.disabled = true;
  const cancelBtn = document.getElementById("cancel-download");
  if (cancelBtn) {
    cancelBtn.hidden = false;
    cancelBtn.disabled = false;
  }

  const totals = { downloaded: 0, exists: 0, error: 0, skipped: 0 };
  const recent = [];
  let dest = outDir;

  const renderProgress = (currentIdx, title) => {
    setStatus(
      els.downloadStatus,
      [
        `Progress: ${totals.downloaded + totals.exists + totals.error + totals.skipped}/${indices.length}`,
        `New: ${totals.downloaded} · Already had: ${totals.exists} · Errors: ${totals.error}`,
        title ? `Now: #${currentIdx} ${title}` : `Starting…`,
        `Folder: ${dest}`,
        "",
        ...recent.slice(-10),
      ].join("\n")
    );
  };

  renderProgress(indices[0], "");

  try {
    for (let i = 0; i < indices.length; i++) {
      if (downloadCancel) {
        recent.push("Cancelled by user.");
        break;
      }
      const idx = indices[i];
      renderProgress(idx, "(downloading…)");
      try {
        const data = await api("/api/download", {
          method: "POST",
          body: JSON.stringify({
            rss: currentFeed.feed,
            out_dir: outDir,
            indices: [idx],
            skip_existing: true,
          }),
        });
        if (data.dest) dest = data.dest;
        for (const r of data.results || []) {
          if (r.status === "downloaded") totals.downloaded += 1;
          else if (r.status === "exists") totals.exists += 1;
          else if (r.status === "error") totals.error += 1;
          else totals.skipped += 1;
          recent.push(`${r.status}: ${r.title}${r.reason ? " (" + r.reason + ")" : ""}`);
          renderProgress(idx, r.title);
        }
        if (!(data.results || []).length) {
          totals.error += 1;
          recent.push(`error: #${idx} (empty response)`);
          renderProgress(idx, "");
        }
      } catch (err) {
        totals.error += 1;
        recent.push(`error: #${idx} (${err.message})`);
        renderProgress(idx, err.message);
      }
    }

    setStatus(
      els.downloadStatus,
      [
        downloadCancel ? "Stopped." : "Done.",
        `New files: ${totals.downloaded}`,
        `Already existed: ${totals.exists}`,
        `Errors: ${totals.error}`,
        `Folder: ${dest}`,
        "",
        ...recent.slice(-30),
      ].join("\n"),
      totals.error > 0
    );
  } finally {
    els.downloadBtn.disabled = false;
    if (cancelBtn) cancelBtn.hidden = true;
  }
});

const cancelDownloadBtn = document.getElementById("cancel-download");
if (cancelDownloadBtn) {
  cancelDownloadBtn.addEventListener("click", () => {
    downloadCancel = true;
    cancelDownloadBtn.disabled = true;
    setStatus(els.downloadStatus, "Cancelling after current episode…");
  });
}

els.saveLibrary.addEventListener("click", async () => {
  if (!currentFeed) return;
  try {
    const data = await api("/api/library/add", {
      method: "POST",
      body: JSON.stringify({
        name: currentFeed.searchName || currentFeed.title,
        rss: currentFeed.feed,
        artist: currentFeed.searchArtist || currentFeed.author || "",
        aliases: [currentFeed.title],
      }),
    });
    setStatus(els.downloadStatus, `Library ${data.status}: ${data.show.name}`);
  } catch (err) {
    setStatus(els.downloadStatus, err.message, true);
  }
});

init().catch((err) => setStatus(els.searchStatus, err.message, true));
