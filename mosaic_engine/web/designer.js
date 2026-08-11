(() => {
  const SVG_NS = "http://www.w3.org/2000/svg";
  let state = null;
  let viewportObserver = null;
  const byId = (id) => document.getElementById(id);

  async function request(path, body) {
    const response = await fetch(path, body === undefined ? {} : {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Request failed");
    return payload;
  }

  function showScreen(stage) {
    byId("canvas-screen").hidden = stage !== "canvas";
    byId("tile-screen").hidden = stage !== "tile";
    byId("workspace").hidden = stage !== "workspace";
    byId("back").hidden = stage === "canvas";
    document.body.classList.toggle("workspace-active", stage === "workspace");
    byId("document-name").textContent = state.document.title;
    byId("document-edited").hidden = !state.document.dirty;
  }

  function renderCanvasPresets() {
    const container = byId("canvas-presets");
    container.replaceChildren();
    for (const preset of state.canvas_presets) {
      const card = document.createElement("button");
      card.className = "choice-card canvas-card";
      card.type = "button";
      card.setAttribute("aria-label", `${preset.name}, ${preset.width_in} by ${preset.height_in} inches`);
      card.innerHTML = `
        <span class="canvas-preview-wrap"><span class="canvas-preview" style="--aspect:${preset.aspect_ratio};--preview-width:${preset.preview_width_rem}rem;--preview-height:${preset.preview_height_rem}rem"></span></span>
        <strong class="choice-title">${preset.name}</strong>
        <span class="choice-meta">${preset.width_in} × ${preset.height_in} in</span>`;
      card.addEventListener("click", () => chooseCanvas(preset.id));
      container.appendChild(card);
    }
  }

  function renderTilePresets() {
    const container = byId("tile-presets");
    container.replaceChildren();
    for (const preset of state.tile_presets) {
      const card = document.createElement("button");
      card.className = "choice-card tile-card";
      card.type = "button";
      card.setAttribute("aria-label", `${preset.id.toUpperCase()}, ${preset.flat_to_flat_mm} millimeters flat to flat`);
      const relativeSize = 3.2 * preset.flat_to_flat_mm / 24;
      card.innerHTML = `
        ${preset.recommended ? '<span class="recommended-badge">Recommended</span>' : ''}
        <span class="hex-preview-wrap"><span class="hex-preview" style="--hex-size:${relativeSize}rem"></span></span>
        <span class="tile-size"><strong>${preset.id.toUpperCase()}</strong><span>${preset.flat_to_flat_mm} mm</span></span>
        <h2>${preset.title}</h2>
        <p>${preset.summary}</p>`;
      card.addEventListener("click", () => chooseTile(preset.id));
      container.appendChild(card);
    }
  }

  function renderWorkspace() {
    if (!state.project) return;
    const project = state.project;
    const geometry = project.geometry;
    byId("workspace-title").textContent = `${project.canvas_preset.name} · ${project.tile_preset.id.toUpperCase()} tile`;
    const svg = byId("mosaic-canvas");
    svg.replaceChildren();
    svg.setAttribute("viewBox", `0 0 ${geometry.width_in} ${geometry.height_in}`);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    for (const tile of geometry.tiles) {
      const polygon = document.createElementNS(SVG_NS, "polygon");
      polygon.id = tile.id;
      polygon.classList.add("designer-tile");
      if (tile.piece_type !== "full") polygon.classList.add("cut");
      polygon.setAttribute("points", tile.vertices_in.map((point) => point.join(",")).join(" "));
      polygon.setAttribute("aria-label", `${tile.piece_type} tile, row ${tile.row + 1}, column ${tile.column + 1}`);
      svg.appendChild(polygon);
    }
    const boundary = document.createElementNS(SVG_NS, "rect");
    boundary.classList.add("panel-boundary");
    boundary.setAttribute("x", "0");
    boundary.setAttribute("y", "0");
    boundary.setAttribute("width", geometry.width_in);
    boundary.setAttribute("height", geometry.height_in);
    svg.appendChild(boundary);

    const plateEstimate = project.print_plate_estimate;
    const stats = [
      `${geometry.width_in} × ${geometry.height_in} in`,
      `${project.tile_preset.id.toUpperCase()} · ${project.tile_preset.flat_to_flat_mm} mm`,
      `${project.grout_mm} mm grout`,
      `${geometry.full_tile_count.toLocaleString()} full`,
      `${geometry.clipped_piece_count.toLocaleString()} clipped`,
      `${geometry.visible_piece_count.toLocaleString()} pieces`,
      `Est. minimum: ${plateEstimate.estimated_minimum_plates} plates`,
    ];
    byId("workspace-status").innerHTML = stats.map((value, index) => (
      `${index ? '<span class="status-separator">·</span>' : ''}<strong>${value}</strong>`
    )).join("");
    requestAnimationFrame(fitToWorkspace);
  }

  function calculateFitSize(
    viewportWidth, viewportHeight, canvasWidth, canvasHeight,
    horizontalPadding = 0, verticalPadding = horizontalPadding,
  ) {
    const availableWidth = Math.max(0, viewportWidth - horizontalPadding);
    const availableHeight = Math.max(0, viewportHeight - verticalPadding);
    const scale = Math.min(availableWidth / canvasWidth, availableHeight / canvasHeight);
    return {
      width: canvasWidth * scale,
      height: canvasHeight * scale,
      scale,
    };
  }

  function fitToWorkspace() {
    if (!state?.project) return;
    const viewport = byId("canvas-viewport");
    const svg = byId("mosaic-canvas");
    const style = getComputedStyle(viewport);
    const horizontalPadding = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
    const verticalPadding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
    const geometry = state.project.geometry;
    const fitted = calculateFitSize(
      viewport.clientWidth,
      viewport.clientHeight,
      geometry.width_in,
      geometry.height_in,
      horizontalPadding,
      verticalPadding,
    );
    if (fitted.scale > 0 && Number.isFinite(fitted.scale)) {
      svg.style.width = `${fitted.width}px`;
      svg.style.height = `${fitted.height}px`;
    }
  }

  function render() {
    showScreen(state.stage);
    renderCanvasPresets();
    renderTilePresets();
    renderWorkspace();
    if (state.stage === "workspace" && !viewportObserver && "ResizeObserver" in window) {
      viewportObserver = new ResizeObserver(() => fitToWorkspace());
      viewportObserver.observe(byId("canvas-viewport"));
    }
  }

  async function chooseCanvas(canvasId) {
    try { state = await request("/api/designer/canvas", { canvas_id: canvasId }); render(); }
    catch (error) { byId("status").textContent = error.message; }
  }

  async function chooseTile(tileId) {
    try { state = await request("/api/designer/tile", { tile_id: tileId }); render(); }
    catch (error) { byId("status").textContent = error.message; }
  }

  byId("back").addEventListener("click", async () => {
    try { state = await request("/api/designer/back", {}); render(); }
    catch (error) { byId("status").textContent = error.message; }
  });
  window.addEventListener("resize", fitToWorkspace);

  request("/api/designer").then((payload) => { state = payload; render(); })
    .catch((error) => { byId("status").textContent = error.message; });
})();
