const $ = (id) => document.getElementById(id);
let current = null,
  original = false,
  lastPage = null,
  cropping = false,
  points = [],
  cropImage;
let noticeTimer,
  editDirty = false;
$("metadata").oninput = () => {
  editDirty = true;
};
function notify(message) {
  $("notice").textContent = message;
  $("notice").style.display = "block";
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => ($("notice").style.display = "none"), 6000);
}
async function api(path, method = "GET", body) {
  const response = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    throw Error(
      typeof error.detail === "string"
        ? error.detail
        : JSON.stringify(error.detail),
    );
  }
  return response.json();
}
function action(fn) {
  return async (event) => {
    if (event) event.preventDefault();
    try {
      await fn(event);
    } catch (error) {
      notify(error.message);
    }
  };
}
async function library() {
  const params = new URLSearchParams({ q: $("query").value });
  for (const [id, key] of [
    ["filter-course", "course"],
    ["after", "after"],
    ["before", "before"],
  ])
    if ($(id).value) params.set(key, $(id).value);
  const pages = await api("/api/pages?" + params);
  $("count").textContent =
    `${pages.length} ${pages.length === 1 ? "page" : "pages"}`;
  $("pages").replaceChildren();
  if (!pages.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent =
      "Your next idea starts here. Capture a page or adjust your search.";
    $("pages").append(empty);
  }
  for (const page of pages) {
    const card = document.createElement("button");
    card.className = "card";
    const image = document.createElement("img");
    image.src = `/api/pages/${page.id}/image/corrected`;
    image.alt = "Note preview";
    image.loading = "lazy";
    const title = document.createElement("strong");
    title.textContent = page.effective_title || "Untitled page";
    const description = document.createElement("small");
    description.textContent = `${page.course} · ${page.created.slice(0, 10)} · ${page.inference_status}${page.duplicate_of ? " · Possible duplicate" : ""}`;
    card.append(image, title, description);
    card.onclick = action(() => openPage(page.id));
    $("pages").append(card);
  }
}
async function openPage(id) {
  editDirty = false;
  current = await api("/api/pages/" + id);
  original = false;
  cropping = false;
  points = [];
  $("detail").hidden = false;
  $("page-image").hidden = false;
  $("crop-canvas").hidden = true;
  $("save-crop").hidden = true;
  $("crop-help").textContent = "";
  $("page-image").src =
    `/api/pages/${id}/image/corrected?v=${current.generation}`;
  $("original").textContent = "Show original";
  $("edit-title").value = current.effective_title;
  $("edit-tags").value = current.effective_tags.join(", ");
  $("edit-course").value = current.course;
  $("edit-description").value = current.description;
  renderPageStatus();
  $("detail").scrollIntoView({ behavior: "smooth", block: "start" });
}
function renderPageStatus() {
  $("page-state").textContent =
    `${current.reviewed ? "Reviewed" : "Unreviewed"} · AI: ${current.inference_status}${current.duplicate_of ? " · Possible duplicate" : ""}`;
  $("suggestions").textContent =
    `Model suggestion: ${current.proposed_title || "(none)"} | ${current.proposed_tags.join(", ")}${current.inference_error ? " — " + current.inference_error : ""}`;
  $("metrics").textContent = JSON.stringify(
    { capture: current.metrics, inference: current.inference_metrics },
    null,
    2,
  );
}
$("settings").onsubmit = action(async () => {
  await api("/api/settings", "PUT", {
    course: $("course").value,
    auto_capture: $("automatic").checked,
  });
  notify("Capture settings applied");
});
async function capture(force) {
  $("capture").disabled = $("force").disabled = true;
  try {
    await api("/api/settings", "PUT", {
      course: $("course").value,
      auto_capture: $("automatic").checked,
    });
    const page = await api("/api/capture", "POST", { force });
    await library();
    await openPage(page.id);
    notify("Page saved. Metadata runs independently.");
  } finally {
    $("capture").disabled = $("force").disabled = false;
  }
}
$("capture").onclick = action(() => capture(false));
$("force").onclick = action(() => capture(true));
$("next").onclick = action(() => api("/api/replay/next", "POST"));
$("search").onsubmit = action(library);
$("close-detail").onclick = () => {
  $("detail").hidden = true;
  current = null;
};
$("metadata").onsubmit = action(async () => {
  current = await api(`/api/pages/${current.id}/metadata`, "PUT", {
    title: $("edit-title").value,
    tags: $("edit-tags")
      .value.split(",")
      .map((x) => x.trim())
      .filter(Boolean),
    course: $("edit-course").value,
    description: $("edit-description").value,
  });
  renderPageStatus();
  await library();
  notify("Corrections saved");
});
$("retry").onclick = action(async () => {
  await api(`/api/pages/${current.id}/retry`, "POST");
  notify("Metadata queued");
});
$("original").onclick = () => {
  original = !original;
  $("page-image").src =
    `/api/pages/${current.id}/image/${original ? "original" : "corrected"}?v=${current.generation}`;
  $("original").textContent = original ? "Show corrected" : "Show original";
};
function drawCrop() {
  const canvas = $("crop-canvas"),
    ctx = canvas.getContext("2d");
  ctx.drawImage(cropImage, 0, 0);
  ctx.strokeStyle = "#e68322";
  ctx.fillStyle = "#e68322";
  ctx.lineWidth = Math.max(3, canvas.width / 250);
  ctx.beginPath();
  points.forEach((point, index) => {
    if (index) ctx.lineTo(...point);
    else ctx.moveTo(...point);
  });
  if (points.length === 4) ctx.closePath();
  ctx.stroke();
  points.forEach((p) => {
    ctx.beginPath();
    ctx.arc(...p, canvas.width / 100, 0, Math.PI * 2);
    ctx.fill();
  });
}
$("crop").onclick = action(async () => {
  points = [];
  cropImage = new Image();
  cropImage.src = `/api/pages/${current.id}/image/original`;
  await cropImage.decode();
  const canvas = $("crop-canvas");
  canvas.width = cropImage.naturalWidth;
  canvas.height = cropImage.naturalHeight;
  $("page-image").hidden = true;
  canvas.hidden = false;
  cropping = true;
  $("save-crop").hidden = true;
  $("crop-help").textContent =
    "Click the four page corners around the perimeter. Click again to restart.";
  drawCrop();
});
$("crop-canvas").onclick = (event) => {
  if (!cropping) return;
  if (points.length === 4) points = [];
  const canvas = $("crop-canvas"),
    rect = canvas.getBoundingClientRect();
  points.push([
    ((event.clientX - rect.left) * canvas.width) / rect.width,
    ((event.clientY - rect.top) * canvas.height) / rect.height,
  ]);
  drawCrop();
  $("save-crop").hidden = points.length !== 4;
};
$("save-crop").onclick = action(async () => {
  await api(`/api/pages/${current.id}/corners`, "PUT", { corners: points });
  await openPage(current.id);
  await library();
  notify("New crop saved; original preserved");
});
let tickCount = 0;
async function tick() {
  try {
    const status = await api("/api/status");
    $("next").hidden = status.camera_source !== "replay";
    $("camera-status").textContent = status.camera;
    $("health").textContent =
      `Storage: ${status.storage} · Model: ${status.model}${status.error ? " · " + status.error : ""}${status.capture_error ? " · " + status.capture_error : ""}`;
    $("quality").textContent = status.capture_error
      ? "Capture failed — adjust the page or retry"
      : status.latched
        ? "Saved — remove the page before the next automatic capture"
        : status.quality
          ? `Sharpness ${status.quality.blur.toFixed(0)} · ${status.quality.acceptable ? "Hold steady" : status.quality.reasons.join(", ")}`
          : "Place one page on a contrasting surface";
    if (tickCount === 0) {
      $("course").value = status.course || "Unsorted";
      $("automatic").checked = !!status.auto_capture;
    }
    const response = await fetch("/api/preview");
    if (response.ok) {
      const bitmap = await createImageBitmap(await response.blob());
      const canvas = $("preview");
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(bitmap, 0, 0);
      bitmap.close();
      if (status.corners) {
        ctx.strokeStyle = "#afdeb1";
        ctx.lineWidth = 3;
        ctx.beginPath();
        status.corners.forEach((p, i) =>
          i ? ctx.lineTo(...p) : ctx.moveTo(...p),
        );
        ctx.closePath();
        ctx.stroke();
      }
    }
    if (status.last_page !== lastPage) {
      lastPage = status.last_page;
      await library();
    }
    if (++tickCount % 8 === 0) {
      await library();
      if (current) {
        const refreshed = await api("/api/pages/" + current.id);
        current = refreshed;
        renderPageStatus();
        if (!editDirty) {
          $("edit-title").value = current.effective_title;
          $("edit-tags").value = current.effective_tags.join(", ");
        }
      }
    }
  } catch (error) {
    $("camera-status").textContent = "Connection unavailable";
  }
  setTimeout(tick, 600);
}
library().catch((error) => notify(error.message));
tick();
