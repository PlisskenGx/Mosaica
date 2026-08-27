(() => {
  const SVG_NS = "http://www.w3.org/2000/svg";
  let state = null;
  let viewportObserver = null;
  let artworkInteraction = null;
  let artworkUploadPath = "/api/designer/artwork/upload";
  let generationInFlight = false;
  let mutationQueue = Promise.resolve();
  let paintTool = null;
  let activeTileColorId = null;
  let paintStroke = null;
  let activePartialPreviewId = null;
  let hoveredTileId = null;
  let keyboardTileActive = false;
  let activeGeometrySignature = null;
  let paletteSelectionHandler = null;
  let paletteInvoker = null;
  let selectedShapeOrientation = "point_top";
  let customAcross = 40;
  let customDown = 24;
  let artworkPreviewUrl = null;
  let artworkPreviewSource = null;
  let exportMode = "studio";
  let exportKind = "print_package";
  let flatExportPath = null;
  let exportJobId = null;
  let exportInFlight = false;
  let exportPollTimer = null;
  let exportPreviewToken = 0;
  let exportPreviewReadyMode = null;
  let saveConfirmationTimer = null;
  const byId = (id) => document.getElementById(id);
  const hasOwn = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const orientationLabel = (orientation) => (
    orientation === "straight" ? "Straight"
      : orientation === "flat_top" ? "Flat Top" : "Point Top"
  );
  const tileShapeLabel = (shape) => (
    shape === "hexagon" ? "Hexagon" : shape === "square" ? "Square" : shape
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
    byId("welcome-screen").hidden = stage !== "welcome";
    byId("welcome-screen").inert = stage !== "welcome";
    byId("workspace").hidden = stage !== "workspace";
    byId("back").hidden = stage !== "workspace";
    byId("document-menu-button").hidden = stage !== "workspace";
    byId("document-title").hidden = stage === "welcome";
    if (stage !== "workspace") closeDocumentMenu(false);
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
        <span class="canvas-preview-wrap"><span class="canvas-preview" style="--preview-width:${preset.preview_width_rem}rem;--preview-height:${preset.preview_height_rem}rem"></span></span>
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
      const square = state.selected_tile_shape === "square";
      const dimension = square ? preset.side_length_mm : preset.flat_to_flat_mm;
      const dimensionName = square ? "side" : "flat to flat";
      card.setAttribute("aria-label", `${preset.id.toUpperCase()}, ${dimension} millimeters ${dimensionName}`);
      const relativeSize = 3.2 * dimension / (square ? 20 : 24);
      card.innerHTML = `
        ${preset.recommended ? '<span class="recommended-badge">Recommended</span>' : ''}
        ${square
          ? `<span class="square-preview-wrap"><span class="square-preview" style="width:${relativeSize}rem"></span></span>`
          : `<span class="hex-preview-wrap"><span class="hex-preview ${state.selected_tile_orientation}" style="--hex-size:${relativeSize}rem;--preview-rotation:${state.selected_tile_orientation === "flat_top" ? "30deg" : "0deg"}"></span></span>`}
        <span class="tile-size"><strong>${preset.id.toUpperCase()}</strong><span>${dimension} mm${square ? " side" : ""}</span></span>
        <h2>${preset.title}</h2><p>${preset.summary}</p>`;
      card.addEventListener("click", () => chooseTile(preset.id));
      container.appendChild(card);
    }
  }

  function renderFamilyOrientation() {
    selectedShapeOrientation = state.selected_tile_shape === "hexagon"
      ? state.selected_tile_orientation || "point_top"
      : "point_top";
    const preview = byId("shape-hexagon").querySelector(".hex-preview");
    preview.style.setProperty(
      "--preview-rotation", selectedShapeOrientation === "flat_top" ? "30deg" : "0deg",
    );
    for (const choice of byId("shape-hexagon").querySelectorAll("[data-shape-orientation]")) {
      choice.setAttribute(
        "aria-pressed", String(choice.dataset.shapeOrientation === selectedShapeOrientation),
      );
    }
  }

  function renderWorkspace() {
    if (!state.project) return;
    const project = state.project;
    const geometry = project.geometry;
    const geometrySignature = [
      geometry.orientation, geometry.width_in, geometry.height_in,
      geometry.tiles.length, geometry.keyboard_center_tile_id,
    ].join(":");
    if (activeGeometrySignature !== null && activeGeometrySignature !== geometrySignature) {
      setTileHighlight(null, false);
    }
    activeGeometrySignature = geometrySignature;
    const svg = byId("mosaic-canvas");
    svg.replaceChildren();
    svg.setAttribute("viewBox", `0 0 ${geometry.width_in} ${geometry.height_in}`);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    const groutField = document.createElementNS(SVG_NS, "rect");
    groutField.classList.add("grout-field");
    groutField.setAttribute("x", "0");
    groutField.setAttribute("y", "0");
    groutField.setAttribute("width", geometry.width_in);
    groutField.setAttribute("height", geometry.height_in);
    groutField.style.fill = project.grout.display_color;
    svg.appendChild(groutField);
    const baseLayer = document.createElementNS(SVG_NS, "g");
    baseLayer.classList.add("base-tile-layer");
    const protectedLayer = document.createElementNS(SVG_NS, "g");
    protectedLayer.classList.add("protected-layer");
    const partialAidLayer = document.createElementNS(SVG_NS, "g");
    partialAidLayer.classList.add("partial-aid-layer");
    const tileHoverLayer = document.createElementNS(SVG_NS, "g");
    tileHoverLayer.classList.add("tile-hover-outline-layer");
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
      const ghost = document.createElementNS(SVG_NS, "polygon");
      ghost.classList.add("partial-parent-ghost");
      ghost.dataset.tileId = tile.id;
      ghost.setAttribute("points", (tile.full_vertices_in || tile.vertices_in).map(
        (point) => point.join(","),
      ).join(" "));
      tileHoverLayer.appendChild(ghost);
      if (
        tile.piece_type !== "full" && tile.full_vertices_in
      ) {
        const hit = document.createElementNS(SVG_NS, "polygon");
        hit.classList.add("partial-parent-hit", "editable");
        hit.dataset.tileId = tile.id;
        hit.setAttribute("points", ghost.getAttribute("points"));
        hit.setAttribute("aria-hidden", "true");
        polygon.setAttribute("tabindex", "0");
        polygon.addEventListener("focus", () => showPartialPreview(tile.id));
        polygon.addEventListener("blur", hidePartialPreview);
        partialAidLayer.appendChild(hit);
      }
    }
    // Full parent hexes remain interactive beneath the real visible pieces,
    // including their clipped continuation beyond the finished panel.
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
    // Full-parent hover outlines sit above the physical canvas while visible
    // tile fill remains clipped to the authoritative finished panel.
    svg.appendChild(tileHoverLayer);
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
    restoreTileHighlight();
    requestAnimationFrame(fitToWorkspace);
  }

  function renderWorkspaceStatus(project) {
    const orientationName = orientationLabel(project.tile_orientation);
    const canvasSummary = project.canvas_mode === "custom_grid"
      ? `${project.custom_grid.tiles_across} × ${project.custom_grid.tiles_down} tiles`
      : `${project.canvas_preset.width_in} × ${project.canvas_preset.height_in} in`;
    const statusBar = byId("workspace-status");
    const version = document.createElement("span");
    version.className = "status-version";
    version.textContent = `v${state.app_version}`;
    statusBar.replaceChildren(
      createStatusGroup("status-project-summary", [
        tileShapeLabel(project.tile_shape),
        orientationName,
        project.tile_preset.title,
        canvasSummary,
      ]),
      version,
    );
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
    const groutField = svg.querySelector(".grout-field");
    if (!groutField) throw new Error("Mosaica cannot find the physical grout field.");
    groutField.style.fill = state.project.grout.display_color;
  }

  function renderCompactWorkspace(updateTiles = false) {
    showScreen(state.stage);
    if (updateTiles) updateExistingTiles();
    refreshArtworkLayers();
    renderWorkspaceStatus(state.project);
    renderBorderInspector();
    renderArtworkInspector();
    renderPaintInspector();
    renderGroutInspector();
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
      const primary = state.project.border.channel_mappings.find(
        (channel) => channel.channel_id === "border_primary",
      );
      const secondary = state.project.border.channel_mappings.find(
        (channel) => channel.channel_id === "border_secondary",
      );
      button.style.setProperty("--border-primary", primary.display_color);
      button.style.setProperty("--border-secondary", secondary.display_color);
      button.innerHTML = `<span class="border-preview ${preset.preview_kind}" aria-hidden="true"></span><span>${preset.name}</span>`;
      button.addEventListener("click", () => chooseBorder(preset.id));
      container.appendChild(button);
    }
    const channels = byId("border-colors");
    channels.replaceChildren();
    for (const channel of state.project.border.color_channels) {
      const swatch = document.createElement("button");
      swatch.type = "button";
      swatch.className = "paint-swatch border-color-swatch";
      swatch.style.backgroundColor = channel.display_color;
      swatch.setAttribute("aria-label", `Change ${channel.channel_id.split("_").join(" ")}`);
      swatch.addEventListener("click", () => openDesignPalette(swatch, async (color) => {
        await performDesignerMutation("/api/designer/border/color", {
          channel_id: channel.channel_id,
          color_id: color.color_id,
        }, { name: "Assign Border color" });
      }));
      channels.appendChild(swatch);
    }
  }

  function renderArtworkInspector(previewTransform = null) {
    if (!state.project) return;
    const artwork = state.project.artwork;
    const generated = state.project.generated_artwork;
    byId("artwork-empty").hidden = Boolean(artwork);
    byId("artwork-loaded").hidden = !artwork;
    byId("artwork-selection-state").textContent = artwork?.selected ? "Selected" : "";
    renderArtworkPreview(artwork);
    if (artwork) {
      const transform = previewTransform || artwork.transform;
      byId("artwork-filename").textContent = artwork.source_filename;
      byId("artwork-filename").title = artwork.source_filename;
      byId("artwork-size").textContent = (
        `${transform.width_in.toFixed(2)} × ${transform.height_in.toFixed(2)} in`
      );
      byId("artwork-generate").textContent = generated ? "Regenerate Mosaic" : "Generate Mosaic";
      byId("artwork-edit").disabled = artwork.edit_mode;
      byId("artwork-generation-state").textContent = generated?.needs_regeneration
        ? "Needs update"
        : generated ? "Mosaic Generated" : "";
    }
    const channels = byId("artwork-colors");
    channels.replaceChildren();
    const generatedChannels = generated?.color_channels || [];
    if (generatedChannels.length >= 1 && generatedChannels.length <= 6) {
      for (const channel of generatedChannels) {
        const swatch = document.createElement("button");
        swatch.type = "button";
        swatch.className = "artwork-color-swatch";
        swatch.style.backgroundColor = channel.display_color;
        swatch.setAttribute("aria-label", `Remap Artwork color ${channel.channel_id.split("-").at(-1)}`);
        swatch.addEventListener("click", () => openDesignPalette(swatch, async (color) => {
          await performDesignerMutation("/api/designer/artwork/color", {
            channel_id: channel.channel_id,
            color_id: color.color_id,
          }, { name: "Remap Artwork color" });
        }));
        channels.appendChild(swatch);
      }
    }
  }

  function renderArtworkPreview(artwork) {
    const image = byId("artwork-preview-image");
    if (!artwork) {
      if (artworkPreviewUrl) URL.revokeObjectURL(artworkPreviewUrl);
      artworkPreviewUrl = null;
      artworkPreviewSource = null;
      image.removeAttribute("src");
      image.alt = "";
      return;
    }
    image.alt = `${artwork.source_filename} preview`;
    if (artwork.sanitized_svg === artworkPreviewSource) return;
    if (artworkPreviewUrl) URL.revokeObjectURL(artworkPreviewUrl);
    artworkPreviewSource = artwork.sanitized_svg;
    artworkPreviewUrl = URL.createObjectURL(new Blob(
      [artwork.sanitized_svg], { type: "image/svg+xml" },
    ));
    image.src = artworkPreviewUrl;
  }

  function renderPaintInspector() {
    if (!state.project) return;
    const paint = state.project.paint;
    const canonicalCta = paint.curated_palette.find(
      (color) => color.color_id === "project-color-5",
    );
    if (canonicalCta) {
      document.querySelector(".artwork-control").style.setProperty(
        "--artwork-cta-color", canonicalCta.display_color,
      );
    }
    if (activeTileColorId !== null && !paint.curated_palette.some(
      (color) => color.color_id === activeTileColorId
    )) {
      activeTileColorId = null;
      paintTool = null;
    }
    byId("mosaic-canvas").classList.toggle("paint-active", paintTool !== null);
    byId("paint-clear").disabled = !state.project.paint?.override_count;
    const palette = byId("paint-colors");
    palette.classList.toggle(
      "flat-top", state.project.tile_orientation === "flat_top",
    );
    palette.classList.toggle(
      "point-top", state.project.tile_orientation !== "flat_top",
    );
    palette.replaceChildren();
    for (const [index, color] of paint.curated_palette.entries()) {
      const swatch = document.createElement("button");
      swatch.type = "button";
      swatch.className = "paint-swatch tile-color-swatch";
      swatch.style.backgroundColor = color.display_color;
      swatch.dataset.colorId = color.color_id;
      const shortcut = index < 4 ? ` · ${index + 1}` : "";
      swatch.setAttribute("aria-label", `${color.name}${shortcut}`);
      swatch.title = `${color.name}${shortcut}`;
      if (index < 4) swatch.setAttribute("aria-keyshortcuts", String(index + 1));
      swatch.setAttribute("aria-pressed", String(color.color_id === activeTileColorId));
      swatch.addEventListener("click", () => {
        activeTileColorId = color.color_id;
        paintTool = "paint";
        hideDesignPalette();
        renderPaintInspector();
      });
      palette.appendChild(swatch);
    }
  }

  function renderGroutInspector() {
    if (!state.project) return;
    const swatch = byId("grout-color");
    swatch.style.setProperty("--current-color", state.project.grout.display_color);
    swatch.dataset.colorId = state.project.grout.color_id;
  }

  function hideDesignPalette() {
    const chooser = byId("design-palette");
    chooser.hidden = true;
    chooser.replaceChildren();
    paletteInvoker?.setAttribute("aria-expanded", "false");
    paletteInvoker?.classList.remove("palette-active");
    paletteSelectionHandler = null;
    paletteInvoker = null;
  }

  function openDesignPalette(invoker, onSelect) {
    if (paletteInvoker && paletteInvoker !== invoker) hideDesignPalette();
    paletteSelectionHandler = onSelect;
    paletteInvoker = invoker;
    invoker.setAttribute("aria-expanded", "true");
    invoker.classList.add("palette-active");
    const chooser = byId("design-palette");
    chooser.replaceChildren();
    for (const color of state.project.paint.curated_palette) {
      const swatch = document.createElement("button");
      swatch.type = "button";
      swatch.className = "paint-palette-swatch";
      swatch.style.backgroundColor = color.display_color;
      swatch.setAttribute("aria-label", color.name);
      swatch.title = color.name;
      swatch.addEventListener("click", async () => {
        const handler = paletteSelectionHandler;
        const returnFocus = paletteInvoker;
        hideDesignPalette();
        returnFocus?.focus();
        if (handler) await handler(color);
      });
      chooser.appendChild(swatch);
    }
    chooser.hidden = false;
    const anchor = invoker.getBoundingClientRect();
    const popup = chooser.getBoundingClientRect();
    const margin = 8;
    const preferredLeft = anchor.right - popup.width;
    const left = Math.max(margin, Math.min(preferredLeft, innerWidth - popup.width - margin));
    const below = anchor.bottom + margin;
    const top = below + popup.height <= innerHeight - margin
      ? below
      : Math.max(margin, anchor.top - popup.height - margin);
    chooser.style.left = `${left}px`;
    chooser.style.top = `${top}px`;
    chooser.querySelector("button")?.focus();
  }

  function validateDesignerState(payload, requireGenerated = false) {
    if (payload?.payload_kind === "document_state") {
      if (!payload.document || !hasOwn(payload.document, "dirty")) {
        throw new Error("Mosaica returned incomplete document state.");
      }
      return;
    }
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
        || !payload.grout
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
      || !["welcome", "shape", "canvas", "tile", "workspace"].includes(payload.stage)
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
      || !project.grout
      || !Array.isArray(project.geometry?.tiles)
      || !Array.isArray(project.color_counts)
      || (requireGenerated && !project.generated_artwork)
    ) {
      throw new Error("Mosaica returned incomplete project state.");
    }
  }

  function applyDesignerState(payload, requireGenerated = false) {
    validateDesignerState(payload, requireGenerated);
    if (payload.payload_kind === "document_state") {
      state = { ...state, document: payload.document };
      byId("document-name").textContent = state.document.title;
      byId("document-edited").hidden = !state.document.dirty;
      if (payload.saved) {
        window.clearTimeout(saveConfirmationTimer);
        byId("save-confirmation").textContent = "Saved";
        saveConfirmationTimer = window.setTimeout(() => {
          byId("save-confirmation").textContent = "";
        }, 1800);
      }
      return;
    } else if (payload.payload_kind === "artwork_state") {
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
          grout: payload.grout,
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
    const direct = initialTarget || event.target.closest?.(
      ".tile-paint-hit.editable, .designer-tile.editable, .partial-parent-hit.editable",
    );
    if (direct) return direct;
    if (event.clientX === undefined || event.clientY === undefined) return null;
    const svg = byId("mosaic-canvas");
    const matrix = svg.getScreenCTM();
    if (!matrix) return null;
    const pointer = svg.createSVGPoint();
    pointer.x = event.clientX;
    pointer.y = event.clientY;
    const physical = pointer.matrixTransform(matrix.inverse());
    const matched = state.project.geometry.tiles.find((tile) => (
      tile.editable && pointInPolygon(
        physical.x, physical.y, tileInteractionVertices(tile),
      )
    ));
    return matched ? byId(matched.id) : null;
  }

  function tileInteractionVertices(tile) {
    return tile.full_vertices_in || tile.vertices_in;
  }

  function pointInPolygon(x, y, vertices) {
    let inside = false;
    for (let index = 0, previous = vertices.length - 1; index < vertices.length; previous = index++) {
      const [x1, y1] = vertices[index];
      const [x2, y2] = vertices[previous];
      const crosses = (y1 > y) !== (y2 > y)
        && x < ((x2 - x1) * (y - y1)) / (y2 - y1) + x1;
      if (crosses) inside = !inside;
    }
    return inside;
  }

  function updateTileHover(event) {
    const blocked = event.target.closest?.(
      ".artwork-handle-target, .artwork-selection-layer, .artwork-object",
    );
    const target = blocked ? null : resolvePartialTarget(event);
    const tileId = target?.dataset.tileId || target?.id || null;
    setTileHighlight(tileId, false);
  }

  function setTileHighlight(tileId, fromKeyboard) {
    if (tileId === hoveredTileId && keyboardTileActive === fromKeyboard) return;
    if (hoveredTileId) {
      byId(hoveredTileId)?.classList.remove("hovered");
      document.querySelector(
        `.partial-parent-ghost[data-tile-id="${hoveredTileId}"]`,
      )?.classList.remove("hover-visible");
    }
    hoveredTileId = tileId;
    keyboardTileActive = Boolean(tileId && fromKeyboard);
    if (hoveredTileId) {
      byId(hoveredTileId)?.classList.add("hovered");
      document.querySelector(
        `.partial-parent-ghost[data-tile-id="${hoveredTileId}"]`,
      )?.classList.add("hover-visible");
    }
  }

  function restoreTileHighlight() {
    if (!hoveredTileId || !state?.project?.geometry?.tiles.some(
      (tile) => tile.id === hoveredTileId && tile.editable,
    )) {
      hoveredTileId = null;
      keyboardTileActive = false;
      return;
    }
    byId(hoveredTileId)?.classList.add("hovered");
    document.querySelector(
      `.partial-parent-ghost[data-tile-id="${hoveredTileId}"]`,
    )?.classList.add("hover-visible");
  }

  function clearTileHover() {
    if (keyboardTileActive) return;
    if (hoveredTileId) {
      byId(hoveredTileId)?.classList.remove("hovered");
      document.querySelector(
        `.partial-parent-ghost[data-tile-id="${hoveredTileId}"]`,
      )?.classList.remove("hover-visible");
    }
    hoveredTileId = null;
    keyboardTileActive = false;
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

  function paintTileFromEvent(event) {
    const target = resolvePartialTarget(event);
    const tileId = target?.dataset.tileId || target?.id;
    const tile = tileId ? byId(tileId) : null;
    if (!paintStroke || !tile || paintStroke.ids.has(tileId)) return;
    paintStroke.ids.add(tileId);
    const stateTile = state.project.geometry.tiles.find((value) => value.id === tileId);
    paintStroke.originalFills.set(tileId, stateTile.display_color);
    setTileHighlight(tileId, false);
    if (paintStroke.mode === "erase") {
      tile.style.fill = stateTile.lower_display_color;
      return;
    }
    const color = state.project.paint.curated_palette.find(
      (value) => value.color_id === paintStroke.colorId,
    );
    if (color) tile.style.fill = color.display_color;
  }

  function beginPaintStroke(event) {
    if (paintTool === null || event.button > 0) return;
    if (event.target.closest?.(
      ".artwork-handle-target, .artwork-selection-layer, .artwork-object",
    )) return;
    const tile = resolvePartialTarget(event);
    if (!tile) return;
    event.preventDefault();
    paintStroke = {
      pointerId: event.pointerId,
      ids: new Set(),
      originalFills: new Map(),
      mode: event.shiftKey ? "erase" : paintTool,
      colorId: event.shiftKey ? null : activeTileColorId,
    };
    byId("canvas-viewport").setPointerCapture(event.pointerId);
    paintTileFromEvent(event);
  }

  function movePaintStroke(event) {
    if (!paintStroke || event.pointerId !== paintStroke.pointerId) return;
    paintTileFromEvent(event);
  }

  async function finishPaintStroke(event) {
    if (!paintStroke || event.pointerId !== paintStroke.pointerId) return;
    const viewport = byId("canvas-viewport");
    if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
    const stroke = paintStroke;
    paintStroke = null;
    if (!stroke.ids.size) return;
    const erasing = stroke.mode === "erase";
    const response = await performDesignerMutation(
      erasing ? "/api/designer/paint/erase" : "/api/designer/paint",
      erasing ? { placement_ids: [...stroke.ids] } : {
        mode: stroke.mode,
        color_id: stroke.colorId,
        placement_ids: [...stroke.ids],
      },
      { name: erasing ? "Reset tile colors" : "Assign tile colors" },
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

  function fullHexInteractionBounds(geometry) {
    const bounds = {
      minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity,
    };
    for (const tile of geometry.tiles) {
      for (const [x, y] of tile.full_vertices_in || tile.vertices_in) {
        bounds.minX = Math.min(bounds.minX, x);
        bounds.maxX = Math.max(bounds.maxX, x);
        bounds.minY = Math.min(bounds.minY, y);
        bounds.maxY = Math.max(bounds.maxY, y);
      }
    }
    return bounds;
  }

  function fitToWorkspace() {
    if (!state?.project) return;
    const viewport = byId("canvas-viewport");
    const svg = byId("mosaic-canvas");
    const style = getComputedStyle(viewport);
    const horizontalPadding = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
    const verticalPadding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
    const geometry = state.project.geometry;
    const interaction = fullHexInteractionBounds(geometry);
    const fitted = calculateFitSize(
      viewport.clientWidth,
      viewport.clientHeight,
      interaction.maxX - interaction.minX,
      interaction.maxY - interaction.minY,
      horizontalPadding,
      verticalPadding,
    );
    if (fitted.scale > 0 && Number.isFinite(fitted.scale)) {
      svg.style.width = `${geometry.width_in * fitted.scale}px`;
      svg.style.height = `${geometry.height_in * fitted.scale}px`;
      const offsetX = (
        (interaction.minX + interaction.maxX - geometry.width_in) / 2
      ) * fitted.scale;
      const offsetY = (
        (interaction.minY + interaction.maxY - geometry.height_in) / 2
      ) * fitted.scale;
      svg.style.transform = `translate(${-offsetX}px, ${-offsetY}px)`;
    }
  }

  function render() {
    showScreen(state.stage);
    if (state.stage !== "workspace") {
      setTileHighlight(null, false);
      activeGeometrySignature = null;
    }
    renderCanvasPresets();
    renderTilePresets();
    renderFamilyOrientation();
    renderWorkspace();
    renderBorderInspector();
    renderArtworkInspector();
    renderPaintInspector();
    renderGroutInspector();
    if (state.stage === "workspace" && !viewportObserver && "ResizeObserver" in window) {
      viewportObserver = new ResizeObserver(() => fitToWorkspace());
      viewportObserver.observe(byId("canvas-viewport"));
    }
  }

  function setExportMode(mode) {
    exportMode = mode;
    for (const card of document.querySelectorAll("[data-export-mode]")) {
      const selected = card.dataset.exportMode === mode;
      card.classList.toggle("selected", selected);
      card.setAttribute("aria-checked", String(selected));
    }
  }

  function drawExportPanelMap(summary) {
    const svg = byId("export-panel-map");
    svg.replaceChildren();
    const width = summary.finished_mosaic.width_mm;
    const height = summary.finished_mosaic.height_mm;
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    for (const panel of summary.panels) {
      const [x0, y0, x1, y1] = panel.bounds_mm;
      const rect = document.createElementNS(SVG_NS, "rect");
      rect.setAttribute("x", x0);
      rect.setAttribute("y", y0);
      rect.setAttribute("width", x1 - x0);
      rect.setAttribute("height", y1 - y0);
      rect.classList.add("export-panel-cell");
      svg.appendChild(rect);
      const label = document.createElementNS(SVG_NS, "text");
      label.setAttribute("x", (x0 + x1) / 2);
      label.setAttribute("y", (y0 + y1) / 2);
      label.classList.add("export-panel-label");
      label.textContent = panel.panel_id;
      svg.appendChild(label);
    }
  }

  function renderExportSummary(summary) {
    const finished = summary.finished_mosaic;
    const tile = summary.tile;
    byId("export-summary-copy").innerHTML = `
      <span>${summary.mode.display_name.toUpperCase()} MODE</span>
      <strong>${summary.panel_count} PRINT ${summary.panel_count === 1 ? "PANEL" : "PANELS"}</strong>
      <span>${finished.width_in.toFixed(1)} × ${finished.height_in.toFixed(1)} in finished mosaic</span>
      <span>${tile.preset_id.toUpperCase()} · ${tile.flat_to_flat_mm} mm · ${orientationLabel(tile.orientation)}</span>
      <span>${summary.palette_color_count} tile ${summary.palette_color_count === 1 ? "color" : "colors"} · ${summary.safe_envelope_mm.width} × ${summary.safe_envelope_mm.height} mm envelope</span>`;
    drawExportPanelMap(summary);
  }

  function setExportError(message = "") {
    byId("export-error").textContent = message;
  }

  function setExportControl(controlState) {
    const control = byId("export-generate");
    const preparing = controlState === "preparing";
    control.dataset.state = controlState;
    control.classList.toggle("is-loading", preparing);
    control.setAttribute("aria-busy", String(preparing));
    control.disabled = preparing;
    control.textContent = {
      preparing: "Preparing Export…",
      ready: "Generate Export",
      error: "Retry Preparation",
    }[controlState];
  }

  async function loadExportPreview() {
    const token = ++exportPreviewToken;
    const requestedMode = exportMode;
    exportPreviewReadyMode = null;
    setExportControl("preparing");
    byId("export-summary").classList.add("is-preparing");
    byId("export-summary").setAttribute("aria-busy", "true");
    setExportError();
    try {
      const summary = await request(
        "/api/designer/export/preview", { mode: requestedMode }, "Preview fabrication export",
      );
      if (token !== exportPreviewToken || requestedMode !== exportMode) return;
      renderExportSummary(summary);
      exportPreviewReadyMode = requestedMode;
      byId("export-summary").classList.remove("is-preparing");
      byId("export-summary").setAttribute("aria-busy", "false");
      setExportControl("ready");
    } catch (error) {
      if (token !== exportPreviewToken || requestedMode !== exportMode) return;
      byId("export-summary").classList.remove("is-preparing");
      byId("export-summary").setAttribute("aria-busy", "false");
      setExportControl("error");
      setExportError(error.message);
    }
  }

  function showExportStep(step) {
    byId("export-chooser").hidden = step !== "chooser";
    byId("export-configure").hidden = step !== "configure";
    byId("export-progress").hidden = step !== "progress";
    byId("export-success").hidden = step !== "success";
    byId("export-file-success").hidden = step !== "file-success";
    byId("export-close").disabled = step === "progress";
  }

  function closeExportDialog() {
    if (exportInFlight) return;
    if (exportPollTimer) window.clearTimeout(exportPollTimer);
    exportPollTimer = null;
    byId("export-dialog").close();
  }

  function openExportDialog() {
    setExportMode("studio");
    exportKind = "print_package";
    byId("export-title").textContent = "Export your mosaic";
    exportJobId = null;
    flatExportPath = null;
    showExportStep("chooser");
    const square = state.project?.tile_family === "square";
    for (const card of document.querySelectorAll(".export-format-cards.fabrication [data-export-format]")) {
      card.classList.toggle("family-unavailable", square);
      card.setAttribute("aria-disabled", String(square));
      card.title = square ? "Square fabrication is not yet available." : "";
      card.querySelector("span").textContent = square
        ? "Square fabrication is not yet available."
        : (card.dataset.exportFormat === "stl"
          ? "Universal mesh fabrication files"
          : "Recommended for multicolor 3D printing");
    }
    byId("export-dialog").showModal();
    byId("export-chooser").querySelector("button")?.focus();
  }

  async function openFabricationExport(kind) {
    exportKind = kind;
    byId("export-title").textContent = kind === "stl"
      ? "Export STL package" : "Export your mosaic";
    showExportStep("configure");
    await loadExportPreview();
  }

  async function exportFlatDesign(format) {
    try {
      const result = await request(
        "/api/designer/export/file", { format }, `Export ${format.toUpperCase()}`,
      );
      if (result.cancelled) return;
      flatExportPath = result.path;
      byId("export-file-success-title").textContent = `${format.toUpperCase()} exported`;
      byId("export-file-success-summary").textContent = result.filename;
      showExportStep("file-success");
    } catch (error) {
      byId("status").textContent = error.message;
    }
  }

  function showExportSuccess(result) {
    showExportStep("success");
    const stl = result.kind === "stl";
    byId("export-success").querySelector("h2").textContent = stl
      ? "Your STL package is ready" : "Your mosaic is ready to print";
    byId("export-success-summary").textContent = (
      `${result.panel_count} ${result.panel_count === 1 ? "panel" : "panels"} prepared in ${result.mode_display_name} mode.`
    );
    const files = byId("export-success-files");
    files.replaceChildren();
    const descriptions = stl ? [
      `${result.stl_count} named STL ${result.stl_count === 1 ? "file" : "files"}`,
      `Manifest: ${result.manifest}`,
    ] : [
      `${result.three_mf_count} multipart 3MF ${result.three_mf_count === 1 ? "file" : "files"}`,
      `Print Guide: ${result.print_guide}`,
      `Manifest: ${result.manifest}`,
    ];
    for (const text of descriptions) {
      const item = document.createElement("li");
      item.textContent = text;
      files.appendChild(item);
    }
  }

  async function pollExportJob() {
    try {
      const job = await request(
        `/api/designer/export/status?id=${encodeURIComponent(exportJobId)}`,
        undefined,
        "Check fabrication export",
      );
      if (job.progress?.message) {
        byId("export-activity").textContent = job.progress.message;
      }
      if (job.status === "running") {
        exportPollTimer = window.setTimeout(pollExportJob, 500);
        return;
      }
      exportInFlight = false;
      if (job.status === "complete") {
        showExportSuccess(job.result);
      } else {
        showExportStep("configure");
        setExportControl("ready");
        setExportError(job.error || "Mosaica could not complete the fabrication export.");
      }
    } catch (error) {
      exportInFlight = false;
      showExportStep("configure");
      setExportControl("ready");
      setExportError(error.message);
    }
  }

  async function generateDesignerExport() {
    if (exportInFlight) return;
    if (byId("export-generate").dataset.state === "error") {
      await loadExportPreview();
      return;
    }
    if (exportPreviewReadyMode !== exportMode) return;
    exportInFlight = true;
    byId("export-activity").textContent = "Preparing your fabrication package…";
    showExportStep("progress");
    try {
      const job = await request(
        "/api/designer/export/start", { mode: exportMode, kind: exportKind },
        `Start ${exportKind === "stl" ? "STL" : "Print Package"} export`,
      );
      exportJobId = job.job_id;
      pollExportJob();
    } catch (error) {
      exportInFlight = false;
      showExportStep("configure");
      setExportError(error.message);
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
      if (!state.selected_tile_id) {
        byId("custom-finished").textContent = `Finished size ${Number(across).toFixed(2)} × ${Number(down).toFixed(2)} in`;
        byId("custom-create").disabled = !(Number(across) > 0 && Number(down) > 0);
        return;
      }
      const preview = await request("/api/designer/canvas-preview", {
        canvas_id: "custom", width_in: Number(across), height_in: Number(down),
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

  function closeDocumentMenu(returnFocus = true) {
    const menu = byId("document-menu");
    if (menu.hidden) return;
    menu.hidden = true;
    byId("document-menu-button").setAttribute("aria-expanded", "false");
    if (returnFocus) byId("document-menu-button").focus();
  }

  function openDocumentMenu() {
    const menu = byId("document-menu");
    menu.hidden = false;
    byId("document-menu-button").setAttribute("aria-expanded", "true");
    menu.querySelector("button:not(:disabled)")?.focus();
  }

  async function openMosaic() {
    closeDocumentMenu(false);
    const discard = !state?.document?.dirty || window.confirm(
      "You have unsaved changes. Opening another project will discard them.",
    );
    if (!discard) return;
    await performDesignerMutation(
      "/api/designer/project/open",
      { discard_unsaved: Boolean(state?.document?.dirty) },
      { name: "Open Mosaic" },
    );
  }

  byId("back").addEventListener("click", async () => {
    if (state.stage === "workspace") {
      const discard = !state.document.dirty || window.confirm(
        "You have unsaved changes. Returning to setup will discard them.",
      );
      if (!discard) return;
      await performDesignerMutation(
        "/api/designer/back",
        { discard_unsaved: Boolean(state.document.dirty) },
        { name: "Back" },
      );
    }
  });
  byId("welcome-new").addEventListener("click", async () => {
    await performDesignerMutation(
      "/api/designer/new", {}, { name: "New Mosaic" },
    );
  });
  byId("welcome-open").addEventListener("click", openMosaic);
  byId("document-menu-button").addEventListener("click", () => {
    if (byId("document-menu").hidden) openDocumentMenu();
    else closeDocumentMenu();
  });
  byId("document-menu").addEventListener("keydown", (event) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const items = [...byId("document-menu").querySelectorAll("button:not(:disabled)")];
    const current = items.indexOf(document.activeElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
      : (current + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items[next]?.focus();
    event.preventDefault();
  });
  byId("export-action").addEventListener("click", () => {
    closeDocumentMenu(false);
    openExportDialog();
  });
  byId("save-action").addEventListener("click", () => {
    closeDocumentMenu(false);
    performDesignerMutation(
      "/api/designer/project/save", {}, { name: "Save project" },
    );
  });
  byId("save-as-action").addEventListener("click", () => {
    closeDocumentMenu(false);
    performDesignerMutation(
      "/api/designer/project/save-as", {}, { name: "Save project as" },
    );
  });
  byId("open-action").addEventListener("click", openMosaic);
  byId("export-close").addEventListener("click", closeExportDialog);
  byId("export-done").addEventListener("click", closeExportDialog);
  byId("export-file-done").addEventListener("click", closeExportDialog);
  byId("export-file-open-folder").addEventListener("click", async () => {
    if (!flatExportPath) return;
    await request(
      "/api/designer/export/file/open", { path: flatExportPath }, "Open export folder",
    );
  });
  byId("export-generate").addEventListener("click", generateDesignerExport);
  for (const card of document.querySelectorAll("[data-export-format]")) {
    card.addEventListener("click", () => {
      if (card.getAttribute("aria-disabled") === "true") return;
      const format = card.dataset.exportFormat;
      if (["svg", "png", "jpg"].includes(format)) exportFlatDesign(format);
      else openFabricationExport(format);
    });
  }
  for (const card of document.querySelectorAll("[data-export-mode]")) {
    card.addEventListener("click", () => {
      if (exportInFlight) return;
      setExportMode(card.dataset.exportMode);
      loadExportPreview();
    });
  }
  byId("export-open-folder").addEventListener("click", async () => {
    try {
      await request(
        "/api/designer/export/open", { id: exportJobId }, "Open fabrication export folder",
      );
    } catch (error) {
      byId("export-success-summary").textContent = error.message;
    }
  });
  byId("export-dialog").addEventListener("cancel", (event) => {
    if (exportInFlight) event.preventDefault();
  });
  function navigateSetupNeighbor(event) {
    if (setupTransitionActive) return;
    const stage = event.currentTarget.dataset.stage;
    if (!stage || !SETUP_STAGE_ORDER.includes(stage)) return;
    state = { ...state, stage };
    render();
  }
  byId("setup-previous").addEventListener("click", navigateSetupNeighbor);
  const shapeOrientationButtons = [
    byId("shape-hexagon").querySelector("[data-shape-orientation=flat_top]"),
    byId("shape-hexagon").querySelector("[data-shape-orientation=point_top]"),
  ];
  const shapePreview = byId("shape-hexagon").querySelector(".hex-preview");
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
  byId("shape-square").addEventListener("click", async () => {
    if (setupTransitionActive) return;
    await performDesignerMutation("/api/designer/shape", {
      shape: "square", orientation: "straight",
    }, { name: "Choose Square" });
  });

  async function createCustomCanvas() {
    await performDesignerMutation("/api/designer/canvas", {
      canvas_id: "custom",
      width_in: Number(byId("custom-across").value),
      height_in: Number(byId("custom-down").value),
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
  byId("paint-clear").addEventListener("click", async () => {
    if (!state.project.paint?.override_count) return;
    await performDesignerMutation(
      "/api/designer/paint/clear", {}, { name: "Clear Edits" },
    );
  });
  byId("grout-color").addEventListener("click", (event) => {
    openDesignPalette(event.currentTarget, async (color) => {
      await performDesignerMutation("/api/designer/grout", {
        color_id: color.color_id,
      }, { name: "Assign Grout color" });
    });
  });
  document.addEventListener("pointerdown", (event) => {
    if (!byId("document-menu").hidden
        && !byId("document-menu").contains(event.target)
        && !byId("document-menu-button").contains(event.target)) {
      closeDocumentMenu(false);
    }
    const chooser = byId("design-palette");
    if (chooser.hidden || chooser.contains(event.target) || paletteInvoker?.contains(event.target)) return;
    const invoker = paletteInvoker;
    hideDesignPalette();
    invoker?.focus();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !byId("document-menu").hidden) {
      event.preventDefault();
      closeDocumentMenu();
      return;
    }
    if (event.key !== "Escape" || byId("design-palette").hidden) return;
    const invoker = paletteInvoker;
    hideDesignPalette();
    invoker?.focus();
  });
  function designerShortcutAvailable(event) {
    if (state?.stage !== "workspace" || !state.project) return false;
    if (event.metaKey || event.ctrlKey || event.altKey) return false;
    if (event.target.closest?.(
      "input, textarea, select, [contenteditable=true], [contenteditable=plaintext-only]",
    )) return false;
    return !document.querySelector(
      "dialog[open], [role=dialog]:not([hidden]), [role=menu]:not([hidden])",
    );
  }

  async function editHighlightedTile(erase = false) {
    if (!hoveredTileId || (!erase && !activeTileColorId)) return false;
    await performDesignerMutation(
      erase ? "/api/designer/paint/erase" : "/api/designer/paint",
      erase ? { placement_ids: [hoveredTileId] } : {
        mode: "paint",
        color_id: activeTileColorId,
        placement_ids: [hoveredTileId],
      },
      { name: erase ? "Reset tile color" : "Assign tile color" },
    );
    keyboardTileActive = true;
    restoreTileHighlight();
    return true;
  }

  document.addEventListener("keydown", (event) => {
    if (!designerShortcutAvailable(event)) return;
    if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
      const current = hoveredTileId || state.project.geometry.keyboard_center_tile_id;
      const destination = hoveredTileId
        ? state.project.geometry.keyboard_navigation?.[current]?.[event.key]
        : current;
      if (destination) setTileHighlight(destination, true);
      event.preventDefault();
      return;
    }
    if (/^[1-4]$/.test(event.key)) {
      const color = state.project.paint.curated_palette[Number(event.key) - 1];
      if (!color) return;
      activeTileColorId = color.color_id;
      paintTool = "paint";
      renderPaintInspector();
      event.preventDefault();
      return;
    }
    if (event.key === "Enter" && !event.repeat) {
      const consumed = event.shiftKey
        ? Boolean(hoveredTileId)
        : Boolean(hoveredTileId && activeTileColorId);
      if (!consumed) return;
      event.preventDefault();
      editHighlightedTile(event.shiftKey);
    }
  });
  const canvasViewport = byId("canvas-viewport");
  byId("mosaic-canvas").addEventListener("pointerdown", beginArtworkInteraction);
  byId("mosaic-canvas").addEventListener("pointermove", moveArtworkInteraction);
  byId("mosaic-canvas").addEventListener("pointerup", finishArtworkInteraction);
  byId("mosaic-canvas").addEventListener("pointercancel", finishArtworkInteraction);
  byId("mosaic-canvas").addEventListener("dragstart", (event) => event.preventDefault());
  canvasViewport.addEventListener("pointerdown", beginPaintStroke);
  canvasViewport.addEventListener("pointermove", updateTileHover);
  canvasViewport.addEventListener("pointermove", movePaintStroke);
  canvasViewport.addEventListener("pointerleave", hidePartialPreview);
  canvasViewport.addEventListener("pointerleave", clearTileHover);
  canvasViewport.addEventListener("pointerup", finishPaintStroke);
  canvasViewport.addEventListener("pointercancel", finishPaintStroke);
  // Artwork rotation remains deferred until the project has authoritative rotation state.
  window.addEventListener("resize", fitToWorkspace);

  request("/api/designer", undefined, "Initial Designer load").then((payload) => {
    applyDesignerState(payload);
  })
    .catch((error) => { byId("status").textContent = error.message; });
})();
