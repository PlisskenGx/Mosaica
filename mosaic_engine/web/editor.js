(() => {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const selectedIds = new Set();
  let state = null;
  let activeId = null;
  let proposalState = null;
  let proposalIndex = 0;
  let proposalAlternativeIndex = 0;
  let proposalView = "proposed";

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
    if (!response.ok) {
      const error = new Error(payload.error || "Request failed");
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function render() {
    byId("project-name").textContent = state.project.path;
    renderMosaic();
    renderPalette();
    renderCounts();
    renderSelection();
    renderProposalPanel();
    byId("dirty").textContent = state.dirty ? "Unsaved changes" : "";
  }

  function renderMosaic() {
    const svg = byId("mosaic");
    svg.replaceChildren();
    svg.setAttribute("viewBox", `0 0 ${state.panel.width_in} ${state.panel.height_in}`);
    svg.setAttribute("aria-label", "Editable tile mosaic");
    svg.classList.toggle("proposal-difference", proposalView === "difference");
    const candidate = currentCandidate();
    const proposal = currentProposal();
    const changes = new Map(
      (proposal?.changes || []).map((change) => [change.tile_id, change])
    );
    const regionIds = new Set(candidate?.tile_ids || []);

    for (const tile of state.tiles) {
      const polygon = document.createElementNS(SVG_NS, "polygon");
      polygon.id = tile.id;
      polygon.dataset.tileId = tile.id;
      polygon.setAttribute("points", tile.vertices_in.map((point) => point.join(",")).join(" "));
      const change = changes.get(tile.id);
      const displayIndex = (
        proposalView === "proposed" && change
          ? change.proposed_index
          : tile.effective_index
      );
      polygon.setAttribute("fill", state.palette[displayIndex].hex);
      polygon.classList.add("tile", tile.editable ? "editable" : "protected");
      if (tile.override_index !== null) polygon.classList.add("manual-override");
      if (regionIds.has(tile.id)) polygon.classList.add("proposal-region");
      if (change && proposalView !== "current") {
        polygon.classList.add(
          change.change_kind === "foreground_addition"
            ? "proposal-addition"
            : "proposal-removal"
        );
      }
      if (selectedIds.has(tile.id)) polygon.classList.add("selected");
      if (tile.id === activeId) polygon.classList.add("active");
      polygon.setAttribute("tabindex", "0");
      polygon.addEventListener("click", (event) => selectTile(tile, event.shiftKey));
      polygon.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.stopPropagation();
          selectTile(tile, event.shiftKey);
        }
      });
      svg.appendChild(polygon);
    }
  }

  function currentCandidate() {
    return proposalState?.candidates?.[proposalIndex] || null;
  }

  function currentProposal() {
    return currentCandidate()?.ranked_alternatives?.[proposalAlternativeIndex] || null;
  }

  function renderProposalPanel() {
    const candidates = proposalState?.candidates || [];
    const candidate = currentCandidate();
    const proposal = currentProposal();
    byId("proposal-loading").hidden = proposalState !== null;
    byId("proposal-empty").hidden = !proposalState || candidates.length > 0;
    byId("proposal-content").hidden = !candidate || !proposal;
    if (!proposalState) return;
    const session = proposalState.session;
    byId("proposal-session").textContent = (
      `Session: ${session.accepted} accepted · ${session.rejected} rejected · `
      + `${session.skipped} skipped`
    );
    byId("proposal-reset").hidden = !Object.keys(session.states).length;
    if (!candidate || !proposal) return;

    byId("proposal-position").textContent = `${proposalIndex + 1} / ${candidates.length}`;
    byId("proposal-id").textContent = (
      `${candidate.candidate_id}${candidate.review_status ? ` · ${candidate.review_status}` : ""}`
    );
    byId("proposal-reason").textContent = candidate.reasons.join("; ");
    byId("proposal-tiles").textContent = proposal.affected_tile_ids.join(", ");
    byId("proposal-change-count").textContent = String(proposal.changes.length);
    byId("proposal-score").textContent = (
      `${proposal.baseline_score.toFixed(4)} → ${proposal.alternative_score.toFixed(4)}`
    );
    byId("proposal-delta").textContent = (
      `${proposal.score_delta >= 0 ? "+" : ""}${proposal.score_delta.toFixed(4)}`
    );
    const select = byId("proposal-alternative");
    select.replaceChildren();
    candidate.ranked_alternatives.forEach((alternative, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = `#${alternative.rank} ${alternative.alternative} (${alternative.score_delta >= 0 ? "+" : ""}${alternative.score_delta.toFixed(4)})`;
      option.selected = index === proposalAlternativeIndex;
      select.appendChild(option);
    });
    const components = byId("proposal-components");
    components.replaceChildren();
    const labels = {
      source_agreement: "Source agreement",
      directional_continuity: "Directional continuity",
      stroke_width_consistency: "Stroke width",
      boundary_regularity: "Boundary regularity",
      negative_space_preservation: "Negative space",
      minimal_change: "Minimal changes",
      topology_preservation: "Topology",
    };
    Object.entries(labels).forEach(([key, label]) => {
      const row = document.createElement("div");
      row.className = "proposal-component";
      row.innerHTML = `<span>${label}</span><span>${proposal.baseline_breakdown[key].toFixed(3)} → ${proposal.alternative_breakdown[key].toFixed(3)}</span>`;
      components.appendChild(row);
    });
    ["current", "proposed", "difference"].forEach((mode) => {
      byId(`proposal-${mode}`).classList.toggle("active", proposalView === mode);
    });
  }

  function setProposalView(mode) {
    proposalView = mode;
    render();
  }

  function availableCandidateIndices() {
    return (proposalState?.candidates || [])
      .map((candidate, index) => ({ candidate, index }))
      .filter(({ candidate }) => !["rejected", "accepted"].includes(candidate.review_status))
      .map(({ index }) => index);
  }

  function moveProposal(direction) {
    const available = availableCandidateIndices();
    if (!available.length) return;
    let position = available.indexOf(proposalIndex);
    if (position < 0) position = direction > 0 ? -1 : 0;
    proposalIndex = available[(position + direction + available.length) % available.length];
    proposalAlternativeIndex = 0;
    render();
  }

  async function reviewAction(action) {
    const candidate = currentCandidate();
    if (!candidate) return;
    proposalState = await request(
      `/api/proposals/${candidate.candidate_id}/${action}`,
      { method: "POST", body: "{}" },
    );
    moveProposal(1);
  }

  async function resetReview() {
    proposalState = await request("/api/proposals/reset", {
      method: "POST", body: "{}",
    });
    proposalIndex = 0;
    proposalAlternativeIndex = 0;
    render();
  }

  async function acceptProposal(confirmConflicts = false) {
    const candidate = currentCandidate();
    const proposal = currentProposal();
    if (!candidate || !proposal) return;
    try {
      const response = await request(
        `/api/proposals/${candidate.candidate_id}/${proposal.alternative}/accept`,
        {
          method: "POST",
          body: JSON.stringify({ confirm_conflicts: confirmConflicts }),
        },
      );
      state = response.project;
      proposalState = await request("/api/proposals");
      moveProposal(1);
    } catch (error) {
      if (
        error.payload?.conflicts?.length
        && !confirmConflicts
        && window.confirm(
          `This proposal conflicts with ${error.payload.conflicts.length} existing manual override(s). Replace them?`
        )
      ) {
        await acceptProposal(true);
        return;
      }
      byId("status").textContent = error.message;
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

    if (event.target?.closest?.("button")) return;

    if (event.key === "[") {
      event.preventDefault();
      moveProposal(-1);
      return;
    }
    if (event.key === "]") {
      event.preventDefault();
      moveProposal(1);
      return;
    }
    if (event.key === "Enter" && currentProposal()) {
      event.preventDefault();
      acceptProposal();
      return;
    }
    if (event.key.toLowerCase() === "r" && currentCandidate()) {
      event.preventDefault();
      reviewAction("reject");
      return;
    }
    if (event.key.toLowerCase() === "s" && currentCandidate()) {
      event.preventDefault();
      reviewAction("skip");
      return;
    }

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
  byId("proposal-previous").addEventListener("click", () => moveProposal(-1));
  byId("proposal-next").addEventListener("click", () => moveProposal(1));
  byId("proposal-current").addEventListener("click", () => setProposalView("current"));
  byId("proposal-proposed").addEventListener("click", () => setProposalView("proposed"));
  byId("proposal-difference").addEventListener("click", () => setProposalView("difference"));
  byId("proposal-accept").addEventListener("click", () => acceptProposal());
  byId("proposal-reject").addEventListener("click", () => reviewAction("reject"));
  byId("proposal-skip").addEventListener("click", () => reviewAction("skip"));
  byId("proposal-reset").addEventListener("click", resetReview);
  byId("proposal-alternative").addEventListener("change", (event) => {
    proposalAlternativeIndex = Number(event.target.value);
    render();
  });
  document.addEventListener("keydown", handleShortcut);

  window.addEventListener("beforeunload", (event) => {
    if (!state?.dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });

  request("/api/project")
    .then((payload) => {
      state = payload;
      render();
      return request("/api/proposals");
    })
    .then((payload) => {
      proposalState = payload;
      render();
    })
    .catch((error) => {
      byId("proposal-loading").hidden = true;
      byId("proposal-error").hidden = false;
      byId("proposal-error").textContent = error.message;
      if (!state) byId("status").textContent = error.message;
    });
})();
