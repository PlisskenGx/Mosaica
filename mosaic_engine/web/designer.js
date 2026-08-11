(() => {
  const SVG_NS = "http://www.w3.org/2000/svg";
  let state = null;
  let viewportObserver = null;
  let artworkInteraction = null;
  let artworkUploadPath = "/api/designer/artwork/upload";
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
    const baseLayer = document.createElementNS(SVG_NS, "g");
    baseLayer.classList.add("base-tile-layer");
    const protectedLayer = document.createElementNS(SVG_NS, "g");
    protectedLayer.classList.add("protected-layer");
    for (const tile of geometry.tiles) {
      const polygon = document.createElementNS(SVG_NS, "polygon");
      polygon.id = tile.id;
      polygon.classList.add("designer-tile");
      if (tile.piece_type !== "full") polygon.classList.add("cut");
      if (tile.border_owned) polygon.classList.add("border-owned");
      if (tile.artwork_available) polygon.classList.add("artwork-available");
      polygon.style.fill = tile.display_color;
      polygon.setAttribute("points", tile.vertices_in.map((point) => point.join(",")).join(" "));
      polygon.setAttribute("aria-label", `${tile.piece_type} tile, row ${tile.row + 1}, column ${tile.column + 1}`);
      (tile.protected ? protectedLayer : baseLayer).appendChild(polygon);
    }
    svg.appendChild(baseLayer);
    renderArtwork(svg, project.artwork);
    svg.appendChild(protectedLayer);
    const boundary = document.createElementNS(SVG_NS, "rect");
    boundary.classList.add("panel-boundary");
    boundary.setAttribute("x", "0");
    boundary.setAttribute("y", "0");
    boundary.setAttribute("width", geometry.width_in);
    boundary.setAttribute("height", geometry.height_in);
    svg.appendChild(boundary);
    renderArtworkSelection(svg, project.artwork);

    const plateEstimate = project.print_plate_estimate;
    const statusBar = byId("workspace-status");
    statusBar.replaceChildren(
      createStatusGroup("status-physical-setup", [
        `${geometry.width_in} × ${geometry.height_in} in`,
        project.tile_preset.id.toUpperCase(),
        `${project.tile_preset.flat_to_flat_mm} mm`,
        `${project.grout_mm} mm grout`,
      ]),
      createStatusGroup("status-production", [
        `${geometry.visible_piece_count.toLocaleString()} pieces`,
        `Est. ${plateEstimate.estimated_minimum_plates} plates`,
      ]),
    );
    renderColorCounts(statusBar, project.color_counts);
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
    imported.setAttribute("preserveAspectRatio", "xMidYMid meet");
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
    for (const [corner, x, y] of artworkCorners(transform)) {
      const target = document.createElementNS(SVG_NS, "circle");
      target.classList.add("artwork-handle-target");
      target.dataset.corner = corner;
      target.setAttribute("cx", x);
      target.setAttribute("cy", y);
      target.setAttribute("r", ".16");
      target.setAttribute("aria-label", `Scale artwork from ${corner} corner`);
      const visible = document.createElementNS(SVG_NS, "circle");
      visible.classList.add("artwork-handle");
      visible.dataset.corner = corner;
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
    const corners = new Map(artworkCorners(transform).map((value) => [value[0], value]));
    for (const name of ["nw", "ne", "se", "sw"]) {
      const corner = corners.get(name);
      const target = svg.querySelector(`.artwork-handle-target[data-corner="${name}"]`);
      const visible = svg.querySelector(`.artwork-handle[data-corner="${name}"]`);
      for (const handle of [target, visible]) {
        if (!handle) continue;
        handle.setAttribute("cx", corner[1]);
        handle.setAttribute("cy", corner[2]);
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
    group.setAttribute("aria-label", "Visible pieces by physical color");
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
      button.innerHTML = `<span class="border-preview ${preset.preview_kind}" aria-hidden="true"></span><span>${preset.name}</span>`;
      button.addEventListener("click", () => chooseBorder(preset.id));
      container.appendChild(button);
    }
    byId("border-lock-state").textContent = (
      `${state.project.border.counts.protected.toLocaleString()} tiles locked`
    );
  }

  function renderArtworkInspector() {
    if (!state.project) return;
    const artwork = state.project.artwork;
    byId("artwork-empty").hidden = Boolean(artwork);
    byId("artwork-loaded").hidden = !artwork;
    byId("artwork-selection-state").textContent = artwork?.selected ? "Selected" : "";
    if (artwork) {
      byId("artwork-filename").textContent = artwork.source_filename;
      byId("artwork-size").textContent = (
        `${artwork.transform.width_in.toFixed(2)} × ${artwork.transform.height_in.toFixed(2)} in`
      );
    }
  }

  function applyArtworkPayload(payload) {
    state.project.artwork = payload.artwork;
    state.document = payload.document;
    render();
  }

  async function uploadArtwork(file) {
    if (!file) return;
    try {
      const svgContent = await file.text();
      const payload = await request(artworkUploadPath, {
        filename: file.name,
        svg_content: svgContent,
      });
      applyArtworkPayload(payload);
    } catch (error) {
      byId("status").textContent = error.message;
    } finally {
      byId("artwork-file").value = "";
      artworkUploadPath = "/api/designer/artwork/upload";
    }
  }

  async function artworkAction(path, body = {}) {
    try {
      const payload = await request(path, body);
      applyArtworkPayload(payload);
    } catch (error) {
      byId("status").textContent = error.message;
    }
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
        artworkAction("/api/designer/artwork/selection", { selected: false });
      }
      return;
    }
    event.preventDefault();
    if (!state.project.artwork.selected) {
      artworkAction("/api/designer/artwork/selection", { selected: true });
      return;
    }
    const svg = byId("mosaic-canvas");
    const start = canvasPoint(event);
    const transform = { ...state.project.artwork.transform };
    artworkInteraction = {
      pointerId: event.pointerId,
      mode: handle ? "scale" : "move",
      corner: handle?.dataset.corner,
      start,
      transform,
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
    } else {
      transform = scaledArtworkTransform(
        artworkInteraction.transform,
        artworkInteraction.corner,
        point,
        state.project.artwork.source_aspect_ratio,
      );
    }
    state.project.artwork.transform = transform;
    updateArtworkVisual(transform);
    renderArtworkInspector();
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

  async function finishArtworkInteraction(event) {
    if (!artworkInteraction || event.pointerId !== artworkInteraction.pointerId) return;
    const svg = byId("mosaic-canvas");
    if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
    artworkInteraction = null;
    await artworkAction(
      "/api/designer/artwork/transform",
      state.project.artwork.transform,
    );
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

  async function chooseBorder(presetId) {
    try {
      state = await request("/api/designer/border", { preset_id: presetId });
      render();
    } catch (error) {
      byId("status").textContent = error.message;
    }
  }

  byId("back").addEventListener("click", async () => {
    try { state = await request("/api/designer/back", {}); render(); }
    catch (error) { byId("status").textContent = error.message; }
  });
  byId("artwork-upload").addEventListener("click", () => {
    artworkUploadPath = "/api/designer/artwork/upload";
    byId("artwork-file").click();
  });
  byId("artwork-replace").addEventListener("click", () => {
    artworkUploadPath = "/api/designer/artwork/replace";
    byId("artwork-file").click();
  });
  byId("artwork-file").addEventListener("change", (event) => uploadArtwork(event.target.files[0]));
  byId("artwork-remove").addEventListener("click", () => artworkAction("/api/designer/artwork/remove"));
  byId("artwork-reset").addEventListener("click", () => artworkAction("/api/designer/artwork/reset"));
  byId("mosaic-canvas").addEventListener("pointerdown", beginArtworkInteraction);
  byId("mosaic-canvas").addEventListener("pointermove", moveArtworkInteraction);
  byId("mosaic-canvas").addEventListener("pointerup", finishArtworkInteraction);
  byId("mosaic-canvas").addEventListener("pointercancel", finishArtworkInteraction);
  byId("mosaic-canvas").addEventListener("dragstart", (event) => event.preventDefault());
  window.addEventListener("resize", fitToWorkspace);

  request("/api/designer").then((payload) => { state = payload; render(); })
    .catch((error) => { byId("status").textContent = error.message; });
})();
