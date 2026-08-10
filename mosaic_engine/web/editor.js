(() => {
  const SVG_NS = "http://www.w3.org/2000/svg";
  let state = null;
  let selectedId = null;

  const byId = (id) => document.getElementById(id);
  const selectedTile = () => state?.tiles.find((tile) => tile.id === selectedId);
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
      polygon.dataset.row = tile.row;
      polygon.dataset.column = tile.column;
      polygon.setAttribute("points", tile.vertices_in.map((point) => point.join(",")).join(" "));
      polygon.setAttribute("fill", state.palette[tile.effective_index].hex);
      polygon.classList.add("tile", tile.editable ? "editable" : "protected");
      if (tile.id === selectedId) polygon.classList.add("selected");
      polygon.setAttribute("tabindex", "0");
      polygon.addEventListener("click", () => selectTile(tile.id));
      polygon.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") selectTile(tile.id);
      });
      svg.appendChild(polygon);
    }
  }

  function renderPalette() {
    const palette = byId("palette");
    palette.replaceChildren();
    const tile = selectedTile();
    for (const color of state.palette) {
      const button = document.createElement("button");
      button.className = "swatch";
      button.disabled = !tile?.editable;
      const chip = document.createElement("span");
      chip.className = "swatch-color";
      chip.style.background = color.hex;
      const label = document.createElement("span");
      label.textContent = `${color.name}${color.sku ? ` · ${color.sku}` : ""}`;
      button.append(chip, label);
      button.addEventListener("click", () => setOverride(color.index));
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
    const tile = selectedTile();
    byId("empty-selection").hidden = Boolean(tile);
    byId("selection").hidden = !tile;
    byId("clear-selected").disabled = !tile?.editable || tile.override_index === null;
    if (!tile) return;
    byId("tile-coordinate").textContent = `${tile.display_row} / ${tile.display_column}`;
    byId("tile-piece").textContent = tile.piece_type;
    byId("tile-generated").textContent = paletteName(tile.generated_index);
    byId("tile-override").textContent = paletteName(tile.override_index);
    byId("tile-effective").textContent = paletteName(tile.effective_index);
  }

  function selectTile(id) {
    selectedId = id;
    render();
  }

  async function setOverride(paletteIndex) {
    const tile = selectedTile();
    if (!tile?.editable) return;
    state = await request(`/api/tiles/${tile.row}/${tile.column}/override`, {
      method: "POST",
      body: JSON.stringify({ palette_index: paletteIndex }),
    });
    render();
  }

  async function clearSelected() {
    const tile = selectedTile();
    if (!tile?.editable) return;
    state = await request(`/api/tiles/${tile.row}/${tile.column}/clear`, {
      method: "POST",
      body: "{}",
    });
    render();
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

  window.addEventListener("beforeunload", (event) => {
    if (!state?.dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });

  request("/api/project")
    .then((payload) => { state = payload; render(); })
    .catch((error) => { byId("status").textContent = error.message; });
})();
