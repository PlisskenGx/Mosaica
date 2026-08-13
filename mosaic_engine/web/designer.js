(() => {
  const SVG_NS = "http://www.w3.org/2000/svg";
  let state = null;
  let viewportObserver = null;
  let artworkInteraction = null;
  let artworkUploadPath = "/api/designer/artwork/upload";
  let generationInFlight = false;
  let mutationQueue = Promise.resolve();
  let paintTool = null;
  let activePaintColorId = null;
  let paintStroke = null;
  let activePartialPreviewId = null;
  let selectedShapeOrientation = "point_top";
  let customAcross = 40;
  let customDown = 24;
  const byId = (id) => document.getElementById(id);
  const hasOwn = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const orientationLabel = (orientation) => (
    orientation === "flat_top" ? "Flat Top" : "Point Top"
  );
  const tileShapeLabel = (shape) => (
    shape === "hexagon" ? "Hexagon" : shape
  );

  class DesignerResponseReadError extends Error {
    constructor(message, response, cause) {
      super(message, { cause });
      this.name = "DesignerResponseReadError";
      this.responseStatus = response.status;
      this.successfulResponse = response.ok;
    }
  }

  class DesignerResponseParseError extends Error {
    constructor(message, response, cause) {
      super(message, { cause });
      this.name = "DesignerResponseParseError";
      this.responseStatus = response.status;
      this.successfulResponse = response.ok;
    }
  }

  async function request(path, body, mutationName = "Designer load") {
    const method = body === undefined ? "GET" : "POST";
    const startedAt = performance.now();
    console.debug(`[Mosaica] ${mutationName}: ${method} ${path} fetch start`);
    let response;
    try {
      response = await fetch(path, body === undefined ? {} : {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (error) {
      console.error(`[Mosaica] ${mutationName}: ${method} ${path} network failure`, error);
      throw new Error(`${mutationName} could not reach the local Mosaica server.`);
    }
    console.debug(
      `[Mosaica] ${mutationName}: ${method} ${path} headers received`,
      {
        status: response.status,
        ok: response.ok,
        contentType: response.headers.get("Content-Type"),
        contentLength: response.headers.get("Content-Length"),
        connection: response.headers.get("Connection"),
        transferEncoding: response.headers.get("Transfer-Encoding"),
        elapsedMs: Math.round(performance.now() - startedAt),
      },
    );
    let responseText;
    try {
      responseText = await response.text();
    } catch (error) {
      console.error(
        `[Mosaica] ${mutationName}: ${method} ${path} returned ${response.status} but its body could not be read`,
        error,
      );
      console.error("[Mosaica] response body read exception", {
        name: error?.name,
        message: error?.message,
        elapsedMs: Math.round(performance.now() - startedAt),
      });
      throw new DesignerResponseReadError(
        `${mutationName} could not read the local server response.`,
        response,
        error,
      );
    }
    let payload;
    try {
      payload = JSON.parse(responseText);
    } catch (error) {
      console.error(
        `[Mosaica] ${mutationName}: ${method} ${path} returned ${response.status} with invalid JSON`,
        responseText,
      );
      throw new DesignerResponseParseError(
        `${mutationName} received invalid JSON from the local server.`,
        response,
        error,
      );
    }
    if (!response.ok) {
      console.error(
        `[Mosaica] ${mutationName}: ${method} ${path} returned ${response.status}`,
        payload,
      );
      throw new Error(payload.error || `${mutationName} failed.`);
    }
    return payload;
  }

  function isRecoverableSuccessfulResponse(error) {
    return (
      error?.successfulResponse === true
      && (
        error.name === "DesignerResponseReadError"
        || error.name === "DesignerResponseParseError"
      )
    );
  }

  const SETUP_STAGE_ORDER = ["shape", "tile", "canvas", "custom"];
  const SETUP_TRANSITION_MS = 250;
  let visibleStage = null;
  let setupTransitionActive = false;
  let setupTransitionToken = 0;

  function setupPanel(stage) {
    return byId(`${stage}-screen`);
  }

  function focusSetupStage(panel) {
    const target = panel?.querySelector("h1, button, input");
    if (!target) return;
    if (target.matches("h1")) target.setAttribute("tabindex", "-1");
    target.focus({ preventScroll: true });
  }

  function previousSetupStage(stage) {
    const index = SETUP_STAGE_ORDER.indexOf(stage);
    return index > 0 ? SETUP_STAGE_ORDER[index - 1] : null;
  }

  function updateSetupNavigation(stage) {
    const previous = previousSetupStage(stage);
    const previousButton = byId("setup-previous");
    previousButton.hidden = !previous;
    previousButton.dataset.stage = previous || "";
    previousButton.setAttribute(
      "aria-label", previous ? `Back to ${setupPanel(previous).querySelector("h1").textContent}` : "Previous setup step",
    );
  }

  function settleSetupPanels(stage, focus = false) {
    for (const name of SETUP_STAGE_ORDER) {
      const panel = setupPanel(name);
      const active = name === stage;
      panel.classList.remove(
        "is-entering-left", "is-entering-right",
        "is-exiting-left", "is-exiting-right", "is-active",
      );
      panel.hidden = !active;
      panel.inert = !active;
      panel.setAttribute("aria-hidden", String(!active));
      if (active) panel.classList.add("is-active");
    }
    updateSetupNavigation(stage);
    setupTransitionActive = false;
    byId("setup-viewport").classList.remove("is-transitioning");
    if (focus && SETUP_STAGE_ORDER.includes(stage)) focusSetupStage(setupPanel(stage));
  }

  function transitionSetupStage(previous, next) {
    if (previous === next) {
      settleSetupPanels(next);
      return;
    }
    const previousIndex = SETUP_STAGE_ORDER.indexOf(previous);
    const nextIndex = SETUP_STAGE_ORDER.indexOf(next);
    const adjacent = previousIndex >= 0 && nextIndex >= 0
      && Math.abs(previousIndex - nextIndex) === 1;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (!adjacent || reduced) {
      settleSetupPanels(next, previous !== null);
      return;
    }

    const forward = nextIndex > previousIndex;
    const outgoing = setupPanel(previous);
    const incoming = setupPanel(next);
    const token = ++setupTransitionToken;
    setupTransitionActive = true;
    byId("setup-viewport").classList.add("is-transitioning");
    outgoing.inert = true;
    outgoing.setAttribute("aria-hidden", "true");
    incoming.hidden = false;
    incoming.inert = true;
    incoming.setAttribute("aria-hidden", "true");
    outgoing.classList.remove("is-active");
    outgoing.classList.add(forward ? "is-exiting-left" : "is-exiting-right");
    incoming.classList.add(forward ? "is-entering-right" : "is-entering-left");
    byId("setup-previous").hidden = true;

    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (token !== setupTransitionToken) return;
      outgoing.classList.add("is-active");
      incoming.classList.add("is-active");
    }));

    let completed = false;
    const complete = () => {
      if (completed || token !== setupTransitionToken) return;
      completed = true;
      incoming.removeEventListener("transitionend", complete);
      settleSetupPanels(next, true);
    };
    incoming.addEventListener("transitionend", complete, { once: true });
    window.setTimeout(complete, SETUP_TRANSITION_MS + 80);
  }

  function showScreen(stage) {
    byId("setup-viewport").dataset.stage = stage;
    const previous = visibleStage;
    visibleStage = stage;
    if (SETUP_STAGE_ORDER.includes(stage)) {
      transitionSetupStage(previous, stage);
    } else {
      ++setupTransitionToken;
      settleSetupPanels(null);
    }
    byId("workspace").hidden = stage !== "workspace";
    byId("back").hidden = stage !== "workspace";
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
        <span class="canvas-preview-wrap"><span class="canvas-preview" style="--aspect:${preset.aspect_ratio}"></span></span>
        <strong class="choice-title">${preset.name}</strong>
        <span class="choice-meta">≈ ${preset.width_in} × ${preset.height_in} in</span>`;
      card.addEventListener("click", () => chooseCanvas(preset.id));
      container.appendChild(card);
    }
  }

  function renderTilePresets() {
    const container = byId("tile-presets");
    container.replaceChildren();
    for (const preset of state.tile_presets) {
      const card = document.createElement("article");
      card.className = "choice-card tile-card";
      card.setAttribute("aria-label", `${preset.id.toUpperCase()}, ${preset.flat_to_flat_mm} millimeters flat to flat`);
      const relativeSize = 3.2 * preset.flat_to_flat_mm / 24;
      card.innerHTML = `
        ${preset.recommended ? '<span class="recommended-badge">Recommended</span>' : ''}
        <span class="hex-preview-wrap"><span class="hex-preview ${state.selected_tile_orientation}" style="--hex-size:${relativeSize}rem;--preview-rotation:${state.selected_tile_orientation === "flat_top" ? "30deg" : "0deg"}"></span></span>
        <span class="tile-size"><strong>${preset.id.toUpperCase()}</strong><span>${preset.flat_to_flat_mm} mm</span></span>
        <h2>${preset.title}</h2><p>${preset.summary}</p>`;
      card.addEventListener("click", () => chooseTile(preset.id));
      container.appendChild(card);
    }
  }

  function renderWorkspace() {
    if (!state.project) return;
    const project = state.project;
    const geometry = project.geometry;
    const svg = byId("mosaic-canvas");
    svg.replaceChildren();
    svg.setAttribute("viewBox", `0 0 ${geometry.width_in} ${geometry.height_in}`);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    const baseLayer = document.createElementNS(SVG_NS, "g");
    baseLayer.classList.add("base-tile-layer");
    const protectedLayer = document.createElementNS(SVG_NS, "g");
    protectedLayer.classList.add("protected-layer");
    const partialAidLayer = document.createElementNS(SVG_NS, "g");
    partialAidLayer.classList.add("partial-aid-layer");
    const tileHitLayer = document.createElementNS(SVG_NS, "g");
    tileHitLayer.classList.add("tile-hit-layer");
    for (const tile of geometry.tiles) {
      const polygon = document.createElementNS(SVG_NS, "polygon");
      polygon.id = tile.id;
      polygon.classList.add("designer-tile");
      if (tile.piece_type !== "full") polygon.classList.add("cut");
      if (tile.border_owned) polygon.classList.add("border-owned");
      if (tile.artwork_available) polygon.classList.add("artwork-available");
      if (tile.editable) polygon.classList.add("editable");
      if (tile.principal_grid) {
        polygon.classList.add("principal-grid");
        polygon.dataset.principalRow = tile.principal_row;
        polygon.dataset.principalColumn = tile.principal_column;
      }
      polygon.style.fill = tile.display_color;
      polygon.dataset.colorId = tile.color_id;
      polygon.setAttribute("points", tile.vertices_in.map((point) => point.join(",")).join(" "));
      polygon.setAttribute("aria-label", `${tile.piece_type} tile, row ${tile.row + 1}, column ${tile.column + 1}`);
      (tile.protected ? protectedLayer : baseLayer).appendChild(polygon);
      const paintHit = document.createElementNS(SVG_NS, "polygon");
      paintHit.classList.add("tile-paint-hit", "editable");
      paintHit.dataset.tileId = tile.id;
      paintHit.setAttribute("points", polygon.getAttribute("points"));
      paintHit.setAttribute("aria-hidden", "true");
      tileHitLayer.appendChild(paintHit);
      if (tile.piece_type !== "full" && tile.full_vertices_in) {
        const ghost = document.createElementNS(SVG_NS, "polygon");
        ghost.classList.add("partial-parent-ghost");
        ghost.dataset.tileId = tile.id;
        ghost.setAttribute("points", tile.full_vertices_in.map((point) => point.join(",")).join(" "));
        const hit = document.createElementNS(SVG_NS, "polygon");
        hit.classList.add("partial-parent-hit", "editable");
        hit.dataset.tileId = tile.id;
        [hit.dataset.centerX, hit.dataset.centerY] = tile.parent_center_in;
        hit.setAttribute("points", ghost.getAttribute("points"));
        hit.setAttribute("aria-label", `Paint ${tile.piece_type} tile, row ${tile.row + 1}, column ${tile.column + 1}`);
        polygon.setAttribute("tabindex", "0");
        polygon.addEventListener("focus", () => showPartialPreview(tile.id));
        polygon.addEventListener("blur", hidePartialPreview);
        partialAidLayer.append(ghost, hit);
      }
    }
    // Parent-hex interaction aids sit below real physical polygons. Their
    // uncovered area remains usable, while no aid can intercept a click on
    // another visible piece.
    svg.appendChild(partialAidLayer);
    svg.appendChild(baseLayer);
    svg.appendChild(protectedLayer);
    const boundary = document.createElementNS(SVG_NS, "rect");
    boundary.classList.add("panel-boundary");
    boundary.setAttribute("x", "0");
    boundary.setAttribute("y", "0");
    boundary.setAttribute("width", geometry.width_in);
    boundary.setAttribute("height", geometry.height_in);
    svg.appendChild(boundary);
    // Exact physical-piece hit geometry wins over the panel boundary and
    // supplemental parent aids. Artwork controls are appended afterward and
    // retain the highest interaction priority.
    svg.appendChild(tileHitLayer);
    // Source artwork and its controls are appended last so they retain input
    // priority wherever the layers overlap.
    if (project.artwork?.edit_mode || !project.generated_artwork) {
      renderArtwork(svg, project.artwork);
      renderArtworkSelection(svg, project.artwork);
    }

    renderWorkspaceStatus(project);
    requestAnimationFrame(fitToWorkspace);
  }

  function renderWorkspaceStatus(project) {
    const geometry = project.geometry;
    const orientationName = orientationLabel(project.tile_orientation);
    const canvasSummary = project.canvas_mode === "custom_grid"
      ? `${project.custom_grid.tiles_across} × ${project.custom_grid.tiles_down} tiles`
      : `≈ ${project.canvas_preset.width_in} × ${project.canvas_preset.height_in} in`;
    const statusBar = byId("workspace-status");
    statusBar.replaceChildren(
      createStatusGroup("status-project-summary", [
        tileShapeLabel(project.tile_shape),
        orientationName,
        project.tile_preset.title,
        canvasSummary,
        `${geometry.visible_piece_count.toLocaleString()} pieces`,
      ]),
    );
    renderColorCounts(statusBar, project.color_counts);
  }

  function refreshArtworkLayers() {
    const svg = byId("mosaic-canvas");
    svg.querySelector(".artwork-object")?.remove();
    svg.querySelector(".artwork-selection-layer")?.remove();
    const artwork = state.project.artwork;
    if (!(artwork?.edit_mode || !state.project.generated_artwork)) return;
    renderArtwork(svg, artwork);
    renderArtworkSelection(svg, artwork);
  }

  function updateExistingTiles() {
    const svg = byId("mosaic-canvas");
    const baseLayer = svg.querySelector(".base-tile-layer");
    const protectedLayer = svg.querySelector(".protected-layer");
    if (!baseLayer || !protectedLayer) {
      throw new Error("Mosaica cannot reconcile tiles before geometry is loaded.");
    }
    for (const tile of state.project.geometry.tiles) {
      const polygon = byId(tile.id);
      if (!polygon) throw new Error(`Mosaica could not find tile ${tile.id}.`);
      polygon.style.fill = tile.display_color;
      polygon.classList.toggle("border-owned", tile.border_owned);
      polygon.classList.toggle("artwork-available", tile.artwork_available);
      polygon.classList.toggle("editable", tile.editable);
      polygon.dataset.colorId = tile.color_id;
      (tile.protected ? protectedLayer : baseLayer).appendChild(polygon);
    }
  }

  function renderCompactWorkspace(updateTiles = false) {
    showScreen(state.stage);
    if (updateTiles) updateExistingTiles();
    refreshArtworkLayers();
    renderWorkspaceStatus(state.project);
    renderBorderInspector();
    renderArtworkInspector();
    renderPaintInspector();
    requestAnimationFrame(fitToWorkspace);
  }

  function renderArtwork(svg, artwork) {
    if (!artwork) return;
    const source = new DOMParser().parseFromString(
      artwork.sanitized_svg, "image/svg+xml",
    ).documentElement;
    const imported = document.importNode(source, true);
    const transform = artwork.transform;
    imported.classList.add("artwork-vector");
    imported.setAttribute("x", transform.x_in);
    imported.setAttribute("y", transform.y_in);
    imported.setAttribute("width", transform.width_in);
    imported.setAttribute("height", transform.height_in);
    imported.setAttribute("preserveAspectRatio", "none");
    const group = document.createElementNS(SVG_NS, "g");
    group.classList.add("artwork-object");
    group.setAttribute("aria-label", `${artwork.source_filename} artwork`);
    group.appendChild(imported);
    const hit = document.createElementNS(SVG_NS, "rect");
    hit.classList.add("artwork-hit");
    setRectTransform(hit, transform);
    group.appendChild(hit);
    svg.appendChild(group);
  }

  function renderArtworkSelection(svg, artwork) {
    if (!artwork?.selected) return;
    const transform = artwork.transform;
    const selection = document.createElementNS(SVG_NS, "g");
    selection.classList.add("artwork-selection-layer");
    const outline = document.createElementNS(SVG_NS, "rect");
    outline.classList.add("artwork-selection");
    setRectTransform(outline, transform);
    selection.appendChild(outline);
    for (const [name, x, y, kind] of artworkHandles(transform)) {
      const target = document.createElementNS(SVG_NS, "circle");
      target.classList.add("artwork-handle-target");
      target.dataset.handle = name;
      target.dataset.kind = kind;
      target.setAttribute("cx", x);
      target.setAttribute("cy", y);
      target.setAttribute("r", ".16");
      target.setAttribute("aria-label", `Resize artwork from ${name} handle`);
      const visible = document.createElementNS(SVG_NS, "circle");
      visible.classList.add("artwork-handle");
      visible.dataset.handle = name;
      visible.dataset.kind = kind;
      visible.setAttribute("cx", x);
      visible.setAttribute("cy", y);
      visible.setAttribute("r", ".16");
      visible.setAttribute("pointer-events", "none");
      selection.append(target, visible);
    }
    svg.appendChild(selection);
  }

  function artworkCorners(transform) {
    return [
      ["nw", transform.x_in, transform.y_in],
      ["ne", transform.x_in + transform.width_in, transform.y_in],
      ["se", transform.x_in + transform.width_in, transform.y_in + transform.height_in],
      ["sw", transform.x_in, transform.y_in + transform.height_in],
    ];
  }

  function artworkHandles(transform) {
    const centerX = transform.x_in + transform.width_in / 2;
    const centerY = transform.y_in + transform.height_in / 2;
    return [
      ...artworkCorners(transform).map(([name, x, y]) => [name, x, y, "corner"]),
      ["top", centerX, transform.y_in, "side"],
      ["right", transform.x_in + transform.width_in, centerY, "side"],
      ["bottom", centerX, transform.y_in + transform.height_in, "side"],
      ["left", transform.x_in, centerY, "side"],
    ];
  }

  function setRectTransform(element, transform) {
    element.setAttribute("x", transform.x_in);
    element.setAttribute("y", transform.y_in);
    element.setAttribute("width", transform.width_in);
    element.setAttribute("height", transform.height_in);
  }

  function updateArtworkVisual(transform) {
    const svg = byId("mosaic-canvas");
    const vector = svg.querySelector(".artwork-vector");
    if (vector) {
      vector.setAttribute("x", transform.x_in);
      vector.setAttribute("y", transform.y_in);
      vector.setAttribute("width", transform.width_in);
      vector.setAttribute("height", transform.height_in);
    }
    const hit = svg.querySelector(".artwork-hit");
    const outline = svg.querySelector(".artwork-selection");
    if (hit) setRectTransform(hit, transform);
    if (outline) setRectTransform(outline, transform);
    const handles = new Map(artworkHandles(transform).map((value) => [value[0], value]));
    for (const name of ["nw", "ne", "se", "sw", "top", "right", "bottom", "left"]) {
      const handlePosition = handles.get(name);
      const target = svg.querySelector(`.artwork-handle-target[data-handle="${name}"]`);
      const visible = svg.querySelector(`.artwork-handle[data-handle="${name}"]`);
      for (const handle of [target, visible]) {
        if (!handle) continue;
        handle.setAttribute("cx", handlePosition[1]);
        handle.setAttribute("cy", handlePosition[2]);
      }
    }
  }

  function createStatusGroup(className, values) {
    const group = document.createElement("span");
    group.className = `status-group ${className}`;
    values.forEach((value, index) => {
      if (index) {
        const separator = document.createElement("span");
        separator.className = "status-separator";
        separator.textContent = "·";
        group.appendChild(separator);
      }
      const text = document.createElement("strong");
      text.textContent = value;
      group.appendChild(text);
    });
    return group;
  }

  function renderColorCounts(statusBar, colorCounts) {
    const group = document.createElement("span");
    group.className = "status-group status-colors physical-color-counts";
    group.setAttribute("aria-label", "Visible pieces by design color");
    for (const color of colorCounts) {
      const item = document.createElement("span");
      item.className = "physical-color-count";
      item.setAttribute("aria-label", `${color.name}, ${color.count.toLocaleString()} pieces`);
      const swatch = document.createElement("span");
      swatch.className = "physical-color-swatch";
      swatch.style.backgroundColor = color.display_color;
      swatch.setAttribute("aria-hidden", "true");
      const count = document.createElement("strong");
      count.textContent = color.count.toLocaleString();
      item.append(swatch, count);
      group.appendChild(item);
    }
    statusBar.appendChild(group);
  }

  function renderBorderInspector() {
    if (!state.project) return;
    const selected = state.project.border.preset_id;
    const container = byId("border-presets");
    container.replaceChildren();
    for (const preset of state.border_presets) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "border-preset";
      button.setAttribute("aria-pressed", String(preset.id === selected));
      const primary = state.project.color_system.design_colors.find(
        (color) => color.color_id === state.project.color_system.role_to_color_id.border_primary,
      );
      const secondary = state.project.color_system.design_colors.find(
        (color) => color.color_id === state.project.color_system.role_to_color_id.border_secondary,
      );
      button.style.setProperty("--border-primary", primary.display_color);
      button.style.setProperty("--border-secondary", secondary.display_color);
      button.innerHTML = `<span class="border-preview ${preset.preview_kind}" aria-hidden="true"></span><span>${preset.name}</span>`;
      button.addEventListener("click", () => chooseBorder(preset.id));
      container.appendChild(button);
    }
    byId("border-lock-state").textContent = (
      `${state.project.border.counts.border_owned.toLocaleString()} border pieces`
    );
  }

  function renderArtworkInspector(previewTransform = null) {
    if (!state.project) return;
    const artwork = state.project.artwork;
    const generated = state.project.generated_artwork;
    byId("artwork-empty").hidden = Boolean(artwork);
    byId("artwork-loaded").hidden = !artwork;
    byId("artwork-selection-state").textContent = artwork?.selected ? "Selected" : "";
    if (artwork) {
      const transform = previewTransform || artwork.transform;
      byId("artwork-filename").textContent = artwork.source_filename;
      byId("artwork-size").textContent = (
        `${transform.width_in.toFixed(2)} × ${transform.height_in.toFixed(2)} in`
      );
      byId("artwork-generate").textContent = generated ? "Regenerate Mosaic" : "Generate Mosaic";
      byId("artwork-edit").hidden = !generated || artwork.edit_mode;
      byId("artwork-generation-state").textContent = generated?.needs_regeneration
        ? "Needs update"
        : generated ? "Mosaic generated" : "";
    }
  }

  function renderPaintInspector() {
    if (!state.project) return;
    const colors = state.project.color_system.design_colors;
    if (!colors.some((color) => color.color_id === activePaintColorId)) {
      activePaintColorId = colors[0]?.color_id || null;
    }
    byId("paint-mode-restore").setAttribute("aria-pressed", String(paintTool === "restore"));
    byId("mosaic-canvas").classList.toggle("paint-active", paintTool !== null);
    byId("paint-clear").disabled = !state.project.paint?.override_count;
    const palette = byId("paint-colors");
    palette.replaceChildren();
    for (const color of colors) {
      const swatch = document.createElement("button");
      swatch.type = "button";
      swatch.className = "paint-swatch";
      swatch.style.backgroundColor = color.display_color;
      swatch.setAttribute("aria-label", `Paint with ${color.name}`);
      swatch.setAttribute("aria-pressed", String(color.color_id === activePaintColorId));
      swatch.addEventListener("click", () => {
        activePaintColorId = color.color_id;
        paintTool = "paint";
        renderPaintInspector();
      });
      palette.appendChild(swatch);
    }
  }

  function validateDesignerState(payload, requireGenerated = false) {
    if (payload?.payload_kind === "artwork_state") {
      if (
        payload.stage !== "workspace"
        || !payload.document
        || !hasOwn(payload, "artwork")
        || !hasOwn(payload, "generated_artwork")
      ) {
        throw new Error("Mosaica returned incomplete artwork state.");
      }
      return;
    }
    if (payload?.payload_kind === "design_state") {
      if (
        payload.stage !== "workspace"
        || !payload.document
        || !hasOwn(payload, "artwork")
        || !hasOwn(payload, "generated_artwork")
        || !payload.border
        || !payload.color_system
        || !Array.isArray(payload.color_counts)
        || !Array.isArray(payload.tile_updates)
        || !payload.paint
        || (requireGenerated && !payload.generated_artwork)
      ) {
        throw new Error("Mosaica returned incomplete design state.");
      }
      return;
    }
    if (
      !payload
      || typeof payload !== "object"
      || !["shape", "canvas", "tile", "workspace"].includes(payload.stage)
      || !payload.document
      || !Array.isArray(payload.canvas_presets)
      || !Array.isArray(payload.tile_presets)
      || !Array.isArray(payload.border_presets)
    ) {
      throw new Error("Mosaica returned an invalid Designer response.");
    }
    if (payload.stage !== "workspace") return;
    const project = payload.project;
    if (
      !project
      || !hasOwn(project, "artwork")
      || !hasOwn(project, "generated_artwork")
      || !Array.isArray(project.geometry?.tiles)
      || !Array.isArray(project.color_counts)
      || (requireGenerated && !project.generated_artwork)
    ) {
      throw new Error("Mosaica returned incomplete project state.");
    }
  }

  function applyDesignerState(payload, requireGenerated = false) {
    validateDesignerState(payload, requireGenerated);
    if (payload.payload_kind === "artwork_state") {
      if (!state?.project) {
        throw new Error("Mosaica cannot apply artwork state without an open project.");
      }
      state = {
        ...state,
        stage: payload.stage,
        document: payload.document,
        project: {
          ...state.project,
          artwork: payload.artwork,
          generated_artwork: payload.generated_artwork,
        },
      };
      renderCompactWorkspace(false);
      return;
    } else if (payload.payload_kind === "design_state") {
      if (!state?.project?.geometry?.tiles) {
        throw new Error("Mosaica cannot apply design state without an open project.");
      }
      const updates = new Map();
      for (const update of payload.tile_updates) {
        if (!update?.id || updates.has(update.id)) {
          throw new Error("Mosaica returned invalid tile updates.");
        }
        updates.set(update.id, update);
      }
      const knownIds = new Set(state.project.geometry.tiles.map((tile) => tile.id));
      if ([...updates.keys()].some((id) => !knownIds.has(id))) {
        throw new Error("Mosaica returned an update for unknown geometry.");
      }
      const tiles = state.project.geometry.tiles.map((tile) => (
        updates.has(tile.id) ? { ...tile, ...updates.get(tile.id) } : tile
      ));
      state = {
        ...state,
        stage: payload.stage,
        document: payload.document,
        project: {
          ...state.project,
          artwork: payload.artwork,
          generated_artwork: payload.generated_artwork,
          border: payload.border,
          color_system: payload.color_system,
          color_counts: payload.color_counts,
          paint: payload.paint,
          geometry: { ...state.project.geometry, tiles },
        },
      };
      renderCompactWorkspace(true);
      return;
    } else {
      state = payload;
    }
    render();
  }

  function performDesignerMutation(path, body = {}, options = {}) {
    const operation = async () => {
      const previousState = state;
      try {
        const payload = await request(path, body, options.name || path);
        applyDesignerState(payload, options.requireGenerated === true);
        const status = byId("status");
        if (status) status.textContent = "";
        return payload;
      } catch (error) {
        if (isRecoverableSuccessfulResponse(error)) {
          console.warn(
            `[Mosaica] ${options.name || path}: successful mutation response was unreadable; reconciling once`,
          );
          try {
            const recovered = await request(
              "/api/designer", undefined,
              `${options.name || path} state recovery`,
            );
            applyDesignerState(recovered, options.requireGenerated === true);
            const status = byId("status");
            if (status) status.textContent = "";
            console.info(`[Mosaica] ${options.name || path}: authoritative state recovered`);
            return recovered;
          } catch (recoveryError) {
            console.error(
              `[Mosaica] ${options.name || path}: authoritative recovery failed`,
              recoveryError,
            );
            error = new Error(
              `${options.name || path} completed, but Mosaica could not recover the updated state.`,
              { cause: recoveryError },
            );
          }
        }
        if (state !== previousState) {
          state = previousState;
          try {
            render();
          } catch (renderError) {
            // Error reporting and later mutations must survive recovery failure.
          }
        }
        const status = byId("status");
        if (status) status.textContent = error?.message || "Mosaica could not apply that change.";
        return null;
      }
    };
    const result = mutationQueue.then(operation, operation);
    mutationQueue = result.then(() => undefined, () => undefined);
    return result;
  }

  function syncGenerationControl() {
    const button = byId("artwork-generate");
    if (!button) return;
    button.disabled = generationInFlight;
    button.setAttribute("aria-busy", String(generationInFlight));
    if (generationInFlight) {
      button.textContent = "Generating…";
      return;
    }
    button.textContent = state?.project?.generated_artwork
      ? "Regenerate Mosaic" : "Generate Mosaic";
  }

  async function generateArtwork() {
    if (generationInFlight) return;
    generationInFlight = true;
    syncGenerationControl();
    byId("status").textContent = "";
    try {
      await performDesignerMutation(
        "/api/designer/artwork/generate", {},
        { requireGenerated: true, name: "Generate Mosaic" },
      );
    } finally {
      generationInFlight = false;
      syncGenerationControl();
    }
  }

  async function uploadArtwork(file) {
    if (!file) return;
    try {
      const svgContent = await file.text();
      await performDesignerMutation(artworkUploadPath, {
        filename: file.name,
        svg_content: svgContent,
      }, { name: artworkUploadPath.endsWith("/replace") ? "Replace artwork" : "Upload artwork" });
    } catch (error) {
      byId("status").textContent = error.message;
    } finally {
      byId("artwork-file").value = "";
      artworkUploadPath = "/api/designer/artwork/upload";
    }
  }

  async function artworkAction(path, body = {}, name = "Artwork change") {
    await performDesignerMutation(path, body, { name });
  }

  function canvasPoint(event) {
    const svg = byId("mosaic-canvas");
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    return point.matrixTransform(svg.getScreenCTM().inverse());
  }

  function beginArtworkInteraction(event) {
    if (!state?.project?.artwork || event.button > 0) return;
    const handle = event.target.closest?.(".artwork-handle-target");
    const object = event.target.closest?.(".artwork-object");
    if (!handle && !object) {
      if (state.project.artwork.selected) {
        artworkAction("/api/designer/artwork/selection", { selected: false }, "Deselect artwork");
      }
      return;
    }
    event.preventDefault();
    if (!state.project.artwork.selected) {
      artworkAction("/api/designer/artwork/selection", { selected: true }, "Select artwork");
      return;
    }
    const svg = byId("mosaic-canvas");
    const start = canvasPoint(event);
    const transform = { ...state.project.artwork.transform };
    artworkInteraction = {
      pointerId: event.pointerId,
      mode: handle ? handle.dataset.kind : "move",
      handle: handle?.dataset.handle,
      aspectRatio: handle?.dataset.kind === "corner"
        ? transform.width_in / transform.height_in
        : null,
      start,
      transform,
      previewTransform: transform,
    };
    svg.setPointerCapture(event.pointerId);
  }

  function moveArtworkInteraction(event) {
    if (!artworkInteraction || event.pointerId !== artworkInteraction.pointerId) return;
    event.preventDefault();
    const point = canvasPoint(event);
    let transform;
    if (artworkInteraction.mode === "move") {
      transform = {
        ...artworkInteraction.transform,
        x_in: artworkInteraction.transform.x_in + point.x - artworkInteraction.start.x,
        y_in: artworkInteraction.transform.y_in + point.y - artworkInteraction.start.y,
      };
    } else if (artworkInteraction.mode === "corner") {
      transform = scaledArtworkTransform(
        artworkInteraction.transform,
        artworkInteraction.handle,
        point,
        artworkInteraction.aspectRatio,
      );
    } else {
      transform = sideResizedArtworkTransform(
        artworkInteraction.transform,
        artworkInteraction.handle,
        point,
      );
    }
    artworkInteraction.previewTransform = transform;
    updateArtworkVisual(transform);
    renderArtworkInspector(transform);
  }

  function scaledArtworkTransform(transform, corner, point, aspectRatio) {
    const right = transform.x_in + transform.width_in;
    const bottom = transform.y_in + transform.height_in;
    const anchors = {
      nw: { x: right, y: bottom, sx: -1, sy: -1 },
      ne: { x: transform.x_in, y: bottom, sx: 1, sy: -1 },
      se: { x: transform.x_in, y: transform.y_in, sx: 1, sy: 1 },
      sw: { x: right, y: transform.y_in, sx: -1, sy: 1 },
    };
    const anchor = anchors[corner];
    const projectedWidth = (
      anchor.sx * (point.x - anchor.x)
      + (anchor.sy / aspectRatio) * (point.y - anchor.y)
    ) / (1 + 1 / (aspectRatio * aspectRatio));
    const width = Math.max(.05, projectedWidth);
    const height = width / aspectRatio;
    return {
      x_in: anchor.sx > 0 ? anchor.x : anchor.x - width,
      y_in: anchor.sy > 0 ? anchor.y : anchor.y - height,
      width_in: width,
      height_in: height,
    };
  }

  function sideResizedArtworkTransform(transform, side, point) {
    const minimum = .05;
    const right = transform.x_in + transform.width_in;
    const bottom = transform.y_in + transform.height_in;
    if (side === "right") {
      return { ...transform, width_in: Math.max(minimum, point.x - transform.x_in) };
    }
    if (side === "left") {
      const width = Math.max(minimum, right - point.x);
      return { ...transform, x_in: right - width, width_in: width };
    }
    if (side === "bottom") {
      return { ...transform, height_in: Math.max(minimum, point.y - transform.y_in) };
    }
    const height = Math.max(minimum, bottom - point.y);
    return { ...transform, y_in: bottom - height, height_in: height };
  }

  async function finishArtworkInteraction(event) {
    if (!artworkInteraction || event.pointerId !== artworkInteraction.pointerId) return;
    const svg = byId("mosaic-canvas");
    if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
    const proposedTransform = artworkInteraction.previewTransform;
    artworkInteraction = null;
    await artworkAction(
      "/api/designer/artwork/transform",
      proposedTransform,
      "Move or scale artwork",
    );
  }

  function resolvePartialTarget(event, initialTarget = null) {
    let target = initialTarget || event.target.closest?.(
      ".tile-paint-hit.editable, .designer-tile.editable, .partial-parent-hit.editable",
    );
    if (target?.classList.contains("partial-parent-hit") && event.clientX !== undefined) {
      const svg = byId("mosaic-canvas");
      const point = svg.createSVGPoint();
      point.x = event.clientX;
      point.y = event.clientY;
      const physical = point.matrixTransform(svg.getScreenCTM().inverse());
      const actual = document.elementsFromPoint(event.clientX, event.clientY).find(
        (element) => element.classList?.contains("designer-tile")
          && element.classList.contains("cut"),
      );
      if (actual) {
        target = actual;
      } else {
        const candidates = document.elementsFromPoint(event.clientX, event.clientY).filter(
          (element) => element.classList?.contains("partial-parent-hit"),
        );
        target = candidates.sort((left, right) => {
          const leftDistance = Math.hypot(
            Number(left.dataset.centerX) - physical.x,
            Number(left.dataset.centerY) - physical.y,
          );
          const rightDistance = Math.hypot(
            Number(right.dataset.centerX) - physical.x,
            Number(right.dataset.centerY) - physical.y,
          );
          return leftDistance - rightDistance
            || left.dataset.tileId.localeCompare(right.dataset.tileId);
        })[0] || target;
      }
    }
    return target;
  }

  function showPartialPreview(tileId) {
    if (paintTool === null || !tileId) return;
    if (activePartialPreviewId === tileId) return;
    hidePartialPreview();
    document.querySelector(
      `.partial-parent-ghost[data-tile-id="${tileId}"]`,
    )?.classList.add("visible");
    activePartialPreviewId = tileId;
  }

  function hidePartialPreview() {
    if (!activePartialPreviewId) return;
    document.querySelector(
      `.partial-parent-ghost[data-tile-id="${activePartialPreviewId}"]`,
    )?.classList.remove("visible");
    activePartialPreviewId = null;
  }

  function updatePartialPreview(event) {
    if (paintTool === null) return hidePartialPreview();
    const target = resolvePartialTarget(event);
    const tileId = target?.dataset.tileId || target?.id;
    const stateTile = state.project.geometry.tiles.find((value) => value.id === tileId);
    if (stateTile?.piece_type !== "full") showPartialPreview(tileId);
    else hidePartialPreview();
  }

  function paintTileFromEvent(event) {
    const target = resolvePartialTarget(event);
    const tileId = target?.dataset.tileId || target?.id;
    const tile = tileId ? byId(tileId) : null;
    if (!paintStroke || !tile || paintStroke.ids.has(tileId)) return;
    paintStroke.ids.add(tileId);
    const stateTile = state.project.geometry.tiles.find((value) => value.id === tileId);
    paintStroke.originalFills.set(tileId, stateTile.display_color);
    if (paintStroke.mode === "paint") {
      const color = state.project.color_system.design_colors.find(
        (value) => value.color_id === paintStroke.colorId,
      );
      if (color) tile.style.fill = color.display_color;
    } else {
      tile.style.fill = stateTile.lower_display_color;
    }
  }

  function beginPaintStroke(event) {
    if (paintTool === null || event.button > 0) return;
    if (event.target.closest?.(
      ".artwork-handle-target, .artwork-selection-layer, .artwork-object",
    )) return;
    const tile = event.target.closest?.(
      ".tile-paint-hit.editable, .designer-tile.editable, .partial-parent-hit.editable",
    );
    if (!tile) return;
    event.preventDefault();
    paintStroke = {
      pointerId: event.pointerId,
      ids: new Set(),
      originalFills: new Map(),
      mode: paintTool,
      colorId: activePaintColorId,
    };
    byId("mosaic-canvas").setPointerCapture(event.pointerId);
    paintTileFromEvent(event);
  }

  function movePaintStroke(event) {
    if (!paintStroke || event.pointerId !== paintStroke.pointerId) return;
    const element = document.elementFromPoint(event.clientX, event.clientY);
    paintTileFromEvent({ target: element });
  }

  async function finishPaintStroke(event) {
    if (!paintStroke || event.pointerId !== paintStroke.pointerId) return;
    const svg = byId("mosaic-canvas");
    if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
    const stroke = paintStroke;
    paintStroke = null;
    if (!stroke.ids.size) return;
    const response = await performDesignerMutation(
      "/api/designer/paint",
      {
        mode: stroke.mode,
        color_id: stroke.mode === "paint" ? stroke.colorId : null,
        placement_ids: [...stroke.ids],
      },
      { name: stroke.mode === "paint" ? "Paint tiles" : "Erase paint" },
    );
    if (!response) {
      for (const [id, fill] of stroke.originalFills) byId(id).style.fill = fill;
    }
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
    renderBorderInspector();
    renderArtworkInspector();
    renderPaintInspector();
    if (state.stage === "workspace" && !viewportObserver && "ResizeObserver" in window) {
      viewportObserver = new ResizeObserver(() => fitToWorkspace());
      viewportObserver.observe(byId("canvas-viewport"));
    }
  }

  async function chooseCanvas(canvasId) {
    if (setupTransitionActive) return;
    await performDesignerMutation("/api/designer/canvas", { canvas_id: canvasId }, { name: "Choose canvas" });
  }

  async function chooseTile(tileId, orientation = "point_top") {
    if (setupTransitionActive) return;
    await performDesignerMutation(
      "/api/designer/tile", {
        tile_id: tileId, orientation: state.selected_tile_orientation || orientation,
      }, { name: "Choose tile" },
    );
  }

  async function updateCustomPreview() {
    const across = byId("custom-across").value;
    const down = byId("custom-down").value;
    try {
      const preview = await request("/api/designer/canvas-preview", {
        canvas_id: "custom", tiles_across: Number(across), tiles_down: Number(down),
      }, "Preview custom canvas");
      byId("custom-finished").textContent = (
        `Finished size ${preview.width_in.toFixed(2)} × ${preview.height_in.toFixed(2)} in`
      );
      byId("custom-create").disabled = false;
    } catch (error) {
      byId("custom-finished").textContent = error.message;
      byId("custom-create").disabled = true;
    }
  }

  async function chooseBorder(presetId) {
    await performDesignerMutation("/api/designer/border", { preset_id: presetId }, { name: "Change Border" });
  }

  byId("back").addEventListener("click", async () => {
    if (state.stage === "workspace") {
      await performDesignerMutation("/api/designer/back", {}, { name: "Back" });
    }
  });
  function navigateSetupNeighbor(event) {
    if (setupTransitionActive) return;
    const stage = event.currentTarget.dataset.stage;
    if (!stage || !SETUP_STAGE_ORDER.includes(stage)) return;
    state = { ...state, stage };
    render();
  }
  byId("setup-previous").addEventListener("click", navigateSetupNeighbor);
  const shapePreview = byId("shape-hexagon").querySelector(".hex-preview");
  const shapeOrientationButtons = [
    byId("shape-hexagon").querySelector("[data-shape-orientation=flat_top]"),
    byId("shape-hexagon").querySelector("[data-shape-orientation=point_top]"),
  ];
  const previewShapeOrientation = (orientation) => shapePreview.style.setProperty(
    "--preview-rotation", orientation === "flat_top" ? "30deg" : "0deg",
  );
  for (const button of shapeOrientationButtons) {
    button.addEventListener("pointerenter", () => previewShapeOrientation(button.dataset.shapeOrientation));
    button.addEventListener("focus", () => previewShapeOrientation(button.dataset.shapeOrientation));
    button.addEventListener("pointerleave", () => previewShapeOrientation(selectedShapeOrientation));
    button.addEventListener("blur", () => previewShapeOrientation(selectedShapeOrientation));
    button.addEventListener("click", async () => {
      if (setupTransitionActive) return;
      selectedShapeOrientation = button.dataset.shapeOrientation;
      for (const choice of shapeOrientationButtons) {
        choice.setAttribute("aria-pressed", String(choice === button));
      }
      previewShapeOrientation(selectedShapeOrientation);
      await performDesignerMutation("/api/designer/shape", {
        shape: "hexagon", orientation: selectedShapeOrientation,
      }, { name: "Choose Hexagon" });
    });
  }

  async function createCustomCanvas() {
    await performDesignerMutation("/api/designer/canvas", {
      canvas_id: "custom",
      tiles_across: Number(byId("custom-across").value),
      tiles_down: Number(byId("custom-down").value),
    }, { name: "Create custom canvas" });
  }
  byId("custom-size").addEventListener("click", () => {
    if (setupTransitionActive) return;
    state = { ...state, stage: "custom" };
    byId("custom-lattice").className = `custom-lattice ${state.selected_tile_orientation}`;
    render();
    updateCustomPreview();
  });
  byId("custom-across").addEventListener("input", (event) => {
    customAcross = event.target.value;
    updateCustomPreview();
  });
  byId("custom-down").addEventListener("input", (event) => {
    customDown = event.target.value;
    updateCustomPreview();
  });
  byId("custom-create").addEventListener("click", createCustomCanvas);
  byId("artwork-upload").addEventListener("click", () => {
    artworkUploadPath = "/api/designer/artwork/upload";
    byId("artwork-file").click();
  });
  byId("artwork-file").addEventListener("change", (event) => uploadArtwork(event.target.files[0]));
  byId("artwork-remove").addEventListener("click", () => artworkAction("/api/designer/artwork/remove", {}, "Remove artwork"));
  byId("artwork-reset").addEventListener("click", () => artworkAction("/api/designer/artwork/reset", {}, "Reset artwork"));
  byId("artwork-generate").addEventListener("click", generateArtwork);
  byId("artwork-edit").addEventListener("click", () => artworkAction("/api/designer/artwork/edit", {}, "Edit artwork"));
  byId("paint-mode-restore").addEventListener("click", () => {
    paintTool = "restore";
    renderPaintInspector();
  });
  byId("paint-clear").addEventListener("click", async () => {
    if (!state.project.paint?.override_count) return;
    await performDesignerMutation(
      "/api/designer/paint/clear", {}, { name: "Clear Edits" },
    );
  });
  byId("mosaic-canvas").addEventListener("pointerdown", beginArtworkInteraction);
  byId("mosaic-canvas").addEventListener("pointerdown", beginPaintStroke);
  byId("mosaic-canvas").addEventListener("pointermove", moveArtworkInteraction);
  byId("mosaic-canvas").addEventListener("pointermove", updatePartialPreview);
  byId("mosaic-canvas").addEventListener("pointermove", movePaintStroke);
  byId("mosaic-canvas").addEventListener("pointerleave", hidePartialPreview);
  byId("mosaic-canvas").addEventListener("pointerup", finishArtworkInteraction);
  byId("mosaic-canvas").addEventListener("pointerup", finishPaintStroke);
  byId("mosaic-canvas").addEventListener("pointercancel", finishArtworkInteraction);
  byId("mosaic-canvas").addEventListener("pointercancel", finishPaintStroke);
  byId("mosaic-canvas").addEventListener("dragstart", (event) => event.preventDefault());
  window.addEventListener("resize", fitToWorkspace);

  request("/api/designer", undefined, "Initial Designer load").then((payload) => {
    applyDesignerState(payload);
  })
    .catch((error) => { byId("status").textContent = error.message; });
})();
