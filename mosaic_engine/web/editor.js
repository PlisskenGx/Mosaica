(() => {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const selectedIds = new Set();
  let state = null;
  let activeId = null;

  const byId = (id) => document.getElementById(id);
  const tileById = (id) => state?.tiles.find((tile) => tile.id === id);
  const selectedTiles = () => state?.tiles.filter((tile) => selectedIds.has(tile.id)) || [];
  const paletteName = (index) => index === null ? "None" : state.palette[index].name;

  async function request(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Request failed");
    return payload;
  }

  function render() {
    byId("project-name").textContent = state.project.path;
    renderMosaic();
    renderPalette();
    renderCounts();
    renderSelection();
    byId("dirty").textContent = state.dirty ? "Unsaved changes" : "";
  }

  function renderMosaic() {
    const svg = byId("mosaic");
    svg.replaceChildren();
    svg.setAttribute("viewBox", `0 0 ${state.panel.width_in} ${state.panel.height_in}`);
    svg.setAttribute("aria-label", "Editable tile mosaic");

    for (const tile of state.tiles) {
      const polygon = document.createElementNS(SVG_NS, "polygon");
      polygon.id = tile.id;
      polygon.dataset.tileId = tile.id;
      polygon.setAttribute("points", tile.vertices_in.map((point) => point.join(",")).join(" "));
      polygon.setAttribute("fill", state.palette[tile.effective_index].hex);
      polygon.classList.add("tile", tile.editable ? "editable" : "protected");
      if (selectedIds.has(tile.id)) polygon.classList.add("selected");
      if (tile.id === activeId) polygon.classList.add("active");
      polygon.setAttribute("tabindex", "0");
      polygon.addEventListener("click", (event) => selectTile(tile, event.shiftKey));
      polygon.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") selectTile(tile, event.shiftKey);
      });
      svg.appendChild(polygon);
    }
  }

  function renderPalette() {
    const palette = byId("palette");
    palette.replaceChildren();
    const hasSelection = selectedIds.size > 0;
    for (const color of state.palette) {
      const button = document.createElement("button");
      button.className = "swatch";
      button.disabled = !hasSelection;
      const chip = document.createElement("span");
      chip.className = "swatch-color";
      chip.style.background = color.hex;
      const label = document.createElement("span");
      label.textContent = `${color.index + 1}. ${color.name}${color.sku ? ` · ${color.sku}` : ""}`;
      button.append(chip, label);
      button.addEventListener("click", () => applyPalette(color.index));
      palette.appendChild(button);
    }
  }

  function renderCounts() {
    const counts = byId("counts");
    counts.replaceChildren();
    for (const color of state.palette) {
      const row = document.createElement("div");
      row.className = "count-row";
      const label = document.createElement("span");
      label.textContent = color.name;
      const value = document.createElement("strong");
      value.textContent = state.counts[color.name] || 0;
      row.append(label, value);
      counts.appendChild(row);
    }
  }

  function renderSelection() {
    const tiles = selectedTiles();
    const inspected = tileById(activeId);
    const detailed = tiles.length === 1 ? tiles[0] : (tiles.length === 0 ? inspected : null);
    const multiple = tiles.length > 1;
    byId("empty-selection").hidden = Boolean(detailed) || multiple;
    byId("selection").hidden = !detailed;
    byId("multi-selection").hidden = !multiple;
    byId("clear-selected").disabled = tiles.length === 0;

    if (detailed) {
      byId("tile-coordinate").textContent = `${detailed.display_row} / ${detailed.display_column}`;
      byId("tile-piece").textContent = detailed.piece_type;
      byId("tile-generated").textContent = paletteName(detailed.generated_index);
      byId("tile-override").textContent = paletteName(detailed.override_index);
      byId("tile-effective").textContent = paletteName(detailed.effective_index);
    }

    if (multiple) {
      byId("selection-count").textContent = String(tiles.length);
      const effective = new Set(tiles.map((tile) => tile.effective_index));
      byId("selection-effective").textContent = (
        effective.size === 1
          ? paletteName(tiles[0].effective_index)
          : "Mixed"
      );
    }
  }

  function selectTile(tile, toggle) {
    if (!tile.editable) {
      if (!toggle) selectedIds.clear();
      activeId = tile.id;
      render();
      return;
    }

    if (toggle) {
      if (selectedIds.has(tile.id)) {
        selectedIds.delete(tile.id);
        activeId = selectedIds.size ? Array.from(selectedIds).pop() : null;
      } else {
        selectedIds.add(tile.id);
        activeId = tile.id;
      }
    } else {
      selectedIds.clear();
      selectedIds.add(tile.id);
      activeId = tile.id;
    }

    render();
  }

  function clearSelection() {
    selectedIds.clear();
    activeId = null;
    render();
  }

  async function applyPalette(paletteIndex) {
    if (!selectedIds.size) return;
    state = await request("/api/overrides/batch", {
      method: "POST",
      body: JSON.stringify({
        tile_ids: Array.from(selectedIds),
        palette_index: paletteIndex,
      }),
    });
    render();
  }

  async function clearSelected() {
    if (!selectedIds.size) return;
    state = await request("/api/overrides/batch-clear", {
      method: "POST",
      body: JSON.stringify({ tile_ids: Array.from(selectedIds) }),
    });
    render();
  }

  function isEditableControl(target) {
    return Boolean(
      target?.isContentEditable
      || target?.closest?.("input, textarea, select, [contenteditable='true']")
    );
  }

  function handleShortcut(event) {
    if (!state) return;
    if (isEditableControl(event.target)) return;

    if (event.key === "Escape") {
      clearSelection();
      return;
    }

    if (event.ctrlKey || event.metaKey || event.altKey) return;

    if (event.key >= "1" && event.key <= "9") {
      const paletteIndex = Number(event.key) - 1;
      if (paletteIndex < state.palette.length && selectedIds.size) {
        event.preventDefault();
        applyPalette(paletteIndex);
      }
      return;
    }

    if (event.key.toLowerCase() === "x" && selectedIds.size) {
      event.preventDefault();
      clearSelected();
    }
  }

  async function clearAll() {
    if (!window.confirm("Clear every manual override?")) return;
    state = await request("/api/overrides/clear-all", { method: "POST", body: "{}" });
    render();
  }

  async function save() {
    const result = await request("/api/save", { method: "POST", body: "{}" });
    state.dirty = result.dirty;
    render();
    byId("status").textContent = result.saved ? "Saved" : "";
    window.setTimeout(() => { byId("status").textContent = ""; }, 1600);
  }

  byId("clear-selected").addEventListener("click", clearSelected);
  byId("clear-all").addEventListener("click", clearAll);
  byId("save").addEventListener("click", save);
  document.addEventListener("keydown", handleShortcut);

  window.addEventListener("beforeunload", (event) => {
    if (!state?.dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });

  request("/api/project")
    .then((payload) => { state = payload; render(); })
    .catch((error) => { byId("status").textContent = error.message; });
})();
