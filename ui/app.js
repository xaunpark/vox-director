/**
 * Vox Studio Dashboard — Frontend Logic
 * Connects to Server-Sent Events (SSE) and REST API for real-time video engine monitoring.
 */

let activeProjectId = null;
let projectData = null;
let eventSource = null;
let reconnectDelay = 2000;

document.addEventListener("DOMContentLoaded", () => {
  initProjectSelector();
  fetchState();
  initSSE();
});

// 1. Fetch Projects & State from REST API
async function fetchState(targetProjectId = null) {
  try {
    const url = targetProjectId ? `/api/state?project=${encodeURIComponent(targetProjectId)}` : "/api/state";
    const res = await fetch(url);
    if (!res.ok) throw new Error(`State error ${res.status}`);
    const data = await res.json();
    updateDashboardUI(data);
  } catch (err) {
    console.error("fetchState failed:", err);
  }
}

// 2. Initialize SSE Connection with Auto-Reconnect
function initSSE() {
  if (eventSource) {
    eventSource.close();
  }

  eventSource = new EventSource("/api/events");

  eventSource.onopen = () => {
    console.log("Connected to Vox Dashboard SSE Server");
    document.getElementById("live-indicator").classList.add("pulse");
    document.getElementById("live-indicator").style.color = "var(--accent-green)";
    reconnectDelay = 2000; // Reset delay
  };

  eventSource.addEventListener("state_update", (e) => {
    try {
      const data = JSON.parse(e.data);
      console.log("SSE State Update received:", data);
      updateDashboardUI(data);
    } catch (err) {
      console.error("SSE parse error:", err);
    }
  });

  eventSource.onerror = (err) => {
    console.warn("SSE connection error, reconnecting in", reconnectDelay, "ms...");
    document.getElementById("live-indicator").classList.remove("pulse");
    document.getElementById("live-indicator").style.color = "var(--text-muted)";
    eventSource.close();
    setTimeout(() => {
      reconnectDelay = Math.min(reconnectDelay * 1.5, 30000);
      initSSE();
      fetchState(activeProjectId);
    }, reconnectDelay);
  };
}

// 3. Populate Project Selector Dropdown
function initProjectSelector() {
  const select = document.getElementById("project-select");
  select.addEventListener("change", (e) => {
    activeProjectId = e.target.value;
    fetchState(activeProjectId);
  });
}

function updateProjectSelector(projects, currentActiveId) {
  const select = document.getElementById("project-select");
  select.innerHTML = "";
  if (!projects || projects.length === 0) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "No projects found in out/";
    select.appendChild(opt);
    return;
  }

  projects.forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = p.name + (p.has_beats ? " (beats.json)" : "");
    if (p.id === currentActiveId) {
      opt.selected = true;
    }
    select.appendChild(opt);
  });
}

// 4. Main UI Render Engine
function updateDashboardUI(data) {
  projectData = data;
  activeProjectId = data.active_id;
  updateProjectSelector(data.projects, data.active_id);

  const doc = data.doc;
  if (!doc) {
    renderEmptyState();
    return;
  }

  // Header & Badges
  document.getElementById("project-title").textContent = doc.project || activeProjectId;
  document.getElementById("badge-aspect").textContent = doc.aspect || "16:9";
  document.getElementById("badge-style").textContent = doc.collage_style || doc.style || "70s-groovy";
  document.getElementById("badge-voice").textContent = doc.narrator ? `Voice: ${doc.narrator}` : "Gemini TTS";

  // Flatten beats into shots list
  const shots = [];
  if (doc.beats) {
    doc.beats.forEach((b) => {
      if (b.shots && Array.isArray(b.shots)) {
        b.shots.forEach((s) => {
          const fullId = s.id && !s.id.startsWith(b.id) ? `${b.id}${s.id}` : `${s.id || b.id}`;
          shots.push({
            ...s,
            full_id: fullId,
            beat_id: b.id,
            narration: b.narration,
            title_text: s.title ? (b.title_en || b.title_cn || "") : ""
          });
        });
      } else {
        const fullId = `${b.id}`;
        shots.push({
          ...b,
          full_id: fullId,
          beat_id: b.id,
          title_text: b.title_en || b.title_cn || ""
        });
      }
    });
  }

  document.getElementById("badge-beats").textContent = `${shots.length} Shots (${doc.beats ? doc.beats.length : 0} Beats)`;
  document.getElementById("shot-count-tag").textContent = `${shots.length} Shots`;

  // Render Pipeline Progress Bar
  updatePipelineProgress(shots, data);

  // Render Left Script Inspector
  renderScriptInspector(doc.beats, shots);

  // Render Right Media Gallery Cards
  renderMediaGrid(shots, data);

  // Render Final Video Player
  renderFinalPlayer(data);
}

// 5. Render Pipeline Progress Bar
function updatePipelineProgress(shots, data) {
  const kfCount = Object.keys(data.keyframes || {}).length;
  const clipCount = Object.keys(data.clips || {}).length;
  const totalShots = shots.length || 1;

  // Step 1: Script
  const stepScript = document.getElementById("step-script");
  stepScript.classList.add("active", "complete");

  // Step 2: Keyframes
  const stepKf = document.getElementById("step-keyframes");
  const kfStatus = stepKf.querySelector(".step-status");
  kfStatus.textContent = `${kfCount}/${totalShots}`;
  if (kfCount > 0) stepKf.classList.add("active");
  if (kfCount >= totalShots) stepKf.classList.add("complete");
  else stepKf.classList.remove("complete");

  // Step 3: Clips
  const stepClips = document.getElementById("step-clips");
  const clipStatus = stepClips.querySelector(".step-status");
  clipStatus.textContent = `${clipCount}/${totalShots}`;
  if (clipCount > 0) stepClips.classList.add("active");
  if (clipCount >= totalShots) stepClips.classList.add("complete");
  else stepClips.classList.remove("complete");

  // Step 4: Audio
  const stepAudio = document.getElementById("step-audio");
  const audioStatus = stepAudio.querySelector(".step-status");
  if (data.has_master_audio) {
    stepAudio.classList.add("active", "complete");
    audioStatus.textContent = "Aligned";
  } else {
    stepAudio.classList.remove("active", "complete");
    audioStatus.textContent = "Pending";
  }

  // Step 5: Final
  const stepFinal = document.getElementById("step-final");
  const finalStatus = stepFinal.querySelector(".step-status");
  if (data.has_final) {
    stepFinal.classList.add("active", "complete");
    finalStatus.textContent = "Complete";
  } else {
    stepFinal.classList.remove("active", "complete");
    finalStatus.textContent = "Pending";
  }
}

// 6. Render Script & Shot Inspector List
function renderScriptInspector(beats, shots) {
  const container = document.getElementById("script-inspector");
  container.innerHTML = "";

  if (!shots || shots.length === 0) {
    container.innerHTML = `<div class="empty-state">No beats/shots defined in beats.json</div>`;
    return;
  }

  shots.forEach((s) => {
    const div = document.createElement("div");
    div.className = "shot-inspect-item";
    const sid = s.full_id || (s.id && s.beat_id ? `${s.beat_id}${s.id}` : (s.id || s.beat_id));
    const headline = s.title_text || s.title_en || s.title_cn || "";

    div.innerHTML = `
      <div class="shot-meta-row">
        <span class="shot-id-badge">Shot ${sid} (Beat ${s.beat_id})</span>
        <span class="shot-move-badge">📷 ${s.camera_move || "static"}</span>
      </div>
      ${headline ? `<div class="shot-headline">"${headline}"</div>` : ""}
      <div class="shot-narr">${s.narration || ""}</div>
      <div class="shot-motion">✨ Motion: ${s.element_motion || s.motion || "Standard parallax"}</div>
    `;
    container.appendChild(div);
  });
}

// 7. Render Live Media Cards Grid
function renderMediaGrid(shots, data) {
  const container = document.getElementById("media-grid");

  if (!shots || shots.length === 0) {
    container.innerHTML = `<div class="empty-state">No shots to display</div>`;
    return;
  }

  const pid = data.active_id;
  const isInitialBuild = container.querySelectorAll(".shot-media-card").length !== shots.length;

  if (isInitialBuild) {
    container.innerHTML = "";
  }

  shots.forEach((s, idx) => {
    const sid = s.full_id || (s.id && s.beat_id ? `${s.beat_id}${s.id}` : (s.id || s.beat_id));
    const kfName = `kf_${sid}.jpg`;
    const clipName = `clip_${sid}.mp4`;

    const hasKf = data.keyframes && data.keyframes[kfName];
    const hasClip = data.clips && data.clips[clipName];

    let existingCard = isInitialBuild ? null : container.children[idx];
    if (!existingCard) {
      existingCard = document.createElement("div");
      existingCard.className = "shot-media-card";
      existingCard.id = `shot-card-${sid}`;
      container.appendChild(existingCard);
    }

    const mediaBox = existingCard.querySelector(".card-media-box");

    // Don't disturb an actively playing video tag!
    if (mediaBox && mediaBox.querySelector("video")) {
      return;
    }

    let mediaHtml = "";
    let statusPill = "";

    if (hasKf || hasClip) {
      const isUpscaled = s.upscaled || s.resolution === "1920x1080";
      
      if (hasClip && isUpscaled) {
        statusPill = `<span class="status-pill status-hd">✨ 1080p HD</span>`;
      } else if (hasClip) {
        statusPill = `<span class="status-pill status-video">AI Video R2V</span>`;
      } else {
        statusPill = `<span class="status-pill status-kf">Keyframe Poster</span>`;
      }

      const hdTagOverlay = (hasClip && isUpscaled)
        ? `<span class="hd-tag-overlay">1080p HD</span>`
        : ``;

      const playOverlay = hasClip
        ? `<button class="video-play-overlay" onclick="playCardVideo(this, '${pid}', '${clipName}')">▶ Play R2V Video</button>`
        : ``;

      const imgSrc = hasKf ? `/media/${pid}/keyframes/${kfName}` : ``;

      mediaHtml = `
        ${imgSrc ? `<img src="${imgSrc}" alt="Keyframe ${sid}" loading="lazy" />` : `<div class="card-placeholder"><span>🎬 Video Ready</span></div>`}
        ${hdTagOverlay}
        ${playOverlay}
      `;
    } else {
      statusPill = `<span class="status-pill status-pending">Pending</span>`;
      mediaHtml = `
        <div class="card-placeholder">
          <span>⏳ Generating...</span>
        </div>
      `;
    }

    existingCard.innerHTML = `
      <div class="card-media-box" id="media-box-${sid}">
        ${mediaHtml}
      </div>
      <div class="card-body">
        <div class="card-title-row">
          <span class="card-shot-id">Shot ${sid}</span>
          ${statusPill}
        </div>
        <div class="shot-narr" style="font-size:0.75rem;">${(s.narration || "").substring(0, 70)}...</div>
      </div>
    `;
  });
}

// 8. Render Final Master Video Player
function renderFinalPlayer(data) {
  const sec = document.getElementById("final-video-section");
  const player = document.getElementById("final-video-player");

  if (data.has_final) {
    sec.classList.remove("hidden");
    const srcUrl = `/media/${data.active_id}/final.mp4?t=${Date.now()}`;
    if (player.src !== window.location.origin + srcUrl) {
      player.src = srcUrl;
    }
  } else {
    sec.classList.add("hidden");
  }
}

function renderEmptyState() {
  document.getElementById("project-title").textContent = "No Valid Project Selected";
  document.getElementById("script-inspector").innerHTML = `<div class="empty-state">Select a project to inspect...</div>`;
  document.getElementById("media-grid").innerHTML = `<div class="empty-state">No media available...</div>`;
  document.getElementById("final-video-section").classList.add("hidden");
}

// Helper to dynamically load & play video when user clicks "Play R2V Video"
function playCardVideo(btn, pid, clipName) {
  const box = btn.closest(".card-media-box");
  if (!box) return;
  const videoUrl = `/media/${pid}/clips/${clipName}?t=${Date.now()}`;
  box.innerHTML = `
    <video controls autoplay loop muted style="width:100%;height:100%;object-fit:cover;">
      <source src="${videoUrl}" type="video/mp4">
    </video>
  `;
}
