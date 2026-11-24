import {
  createMap,
  setMapView,
  getMapView,
  onMapClick,
  onMapMove,
  showPopup,
} from "https://cdn.jsdelivr.net/gh/crootjs/lib@0.0.16/map.js";
import { addMarker } from "https://cdn.jsdelivr.net/gh/crootjs/lib@0.0.16/mapmarker.js";
import { addScaleBar } from "https://cdn.jsdelivr.net/gh/crootjs/lib@0.0.16/mapcontrol.js";
import {
  container,
  onClick,
  getValue,
  setValue,
  setInnerText,
  runAfterDOM,
} from "https://cdn.jsdelivr.net/gh/crootjs/lib@0.0.16/element.js";

const MAP_ID = "map";
const LOCAL_TILE_URL = "./tiles/{z}/{x}/{y}.png";
const OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const MIN_ZOOM = 0;
const MAX_ZOOM = 4;
const MAX_MERCATOR_LAT = 85.05112877980659;

// Titik referensi: Bandung (urutan GeoJSON [lng, lat])
const BANDUNG = {
  name: "Bandung",
  lng: 107.6191,
  lat: -6.9175,
  position: [107.6191, -6.9175],
};

const state = {
  tileUrl: LOCAL_TILE_URL,
  center: BANDUNG.position.slice(),
  zoom: 4,
  lastInspected: BANDUNG.position.slice(),
  lastInspectMode: "REFERENSI",
  tileObserver: null,
  stopMove: null,
  removeScaleBar: null,
  bandungMarker: null,
  tileSyncFrame: 0,
};

const desktopQuery = window.matchMedia("(min-width: 960px)");

/* ==========================================================================
   Matematika Proyeksi Web Mercator (EPSG:3857) -> Indeks Tile {Z, X, Y}
   ========================================================================== */
function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function latLngToTile(lat, lng, zoom) {
  const z = clamp(Math.round(zoom), MIN_ZOOM, MAX_ZOOM);
  const n = Math.pow(2, z);
  const clampedLat = clamp(lat, -MAX_MERCATOR_LAT, MAX_MERCATOR_LAT);
  const latRad = (clampedLat * Math.PI) / 180;

  const x = clamp(Math.floor(((lng + 180) / 360) * n), 0, n - 1);
  const y = clamp(
    Math.floor(
      ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n
    ),
    0,
    n - 1
  );

  return { z, x, y };
}

function resolveTileUrl(template, z, x, y) {
  return template
    .replaceAll("{z}", String(z))
    .replaceAll("{x}", String(x))
    .replaceAll("{y}", String(y));
}

function validateTileTemplate(raw) {
  const trimmed = raw.trim();
  if (!trimmed) {
    return { valid: false, message: "Template URL tile tidak boleh kosong." };
  }

  const required = ["{z}", "{x}", "{y}"];
  const missing = required.filter((token) => !trimmed.includes(token));
  if (missing.length > 0) {
    return {
      valid: false,
      message: `Placeholder wajib belum lengkap: ${missing.join(", ")}.`,
    };
  }

  if (/\s/.test(trimmed)) {
    return { valid: false, message: "Template URL tidak boleh mengandung spasi." };
  }

  const lower = trimmed.toLowerCase();
  if (lower.startsWith("javascript:") || lower.startsWith("data:")) {
    return { valid: false, message: "Skema URL tidak diizinkan." };
  }

  const probeUrl = resolveTileUrl(trimmed, 0, 0, 0);
  try {
    const parsed = new URL(probeUrl, window.location.href);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return { valid: false, message: "Gunakan path relatif atau protokol HTTP/HTTPS." };
    }
  } catch {
    return { valid: false, message: "Format URL tile tidak valid." };
  }

  return { valid: true, url: trimmed };
}

/* ==========================================================================
   Helper UI & Popup DOM (aman dari injeksi HTML)
   ========================================================================== */
function setFeedback(message, tone = "") {
  const el = container("tileUrlFeedback");
  const input = container("tileUrlInput");
  if (!el || !input) return;

  el.textContent = message;
  el.classList.remove("is-error", "is-ok");
  if (tone === "error") {
    el.classList.add("is-error");
    input.setAttribute("aria-invalid", "true");
  } else {
    input.removeAttribute("aria-invalid");
    if (tone === "ok") el.classList.add("is-ok");
  }
}

function buildPopupNode(title, lat, lng, tile, resolvedUrl) {
  const wrap = document.createElement("div");
  wrap.className = "popup-content";

  const heading = document.createElement("div");
  heading.className = "popup-title";
  heading.textContent = title;

  const coords = document.createElement("div");
  coords.className = "popup-row";
  coords.textContent = `LAT ${lat.toFixed(5)} · LNG ${lng.toFixed(5)}`;

  const zxy = document.createElement("div");
  zxy.className = "popup-row";
  zxy.textContent = `XYZ: Z=${tile.z} / X=${tile.x} / Y=${tile.y}`;

  const link = document.createElement("a");
  link.className = "popup-link";
  link.href = resolvedUrl;
  link.target = "_blank";
  link.rel = "noopener";
  link.textContent = "Buka tile mentah ↗";

  wrap.append(heading, coords, zxy, link);
  return wrap;
}

function updateSourceIndicators() {
  const statusEl = container("sourceStatus");
  const ledEl = container("sourceLed");
  const localBtn = container("useLocalBtn");
  const osmBtn = container("compareOsmBtn");

  const isLocal = state.tileUrl === LOCAL_TILE_URL;
  const isOsm = state.tileUrl === OSM_TILE_URL;

  ledEl.classList.remove("is-local", "is-osm", "is-custom");
  if (isLocal) {
    statusEl.textContent = "LOKAL";
    ledEl.classList.add("is-local");
  } else if (isOsm) {
    statusEl.textContent = "OSM";
    ledEl.classList.add("is-osm");
  } else {
    statusEl.textContent = "CUSTOM";
    ledEl.classList.add("is-custom");
  }

  localBtn.classList.toggle("is-active", isLocal);
  localBtn.setAttribute("aria-pressed", String(isLocal));

  osmBtn.classList.toggle("is-active", isOsm);
  osmBtn.setAttribute("aria-pressed", String(isOsm));
}

function updateZoomControls(zoom) {
  const z = clamp(Math.round(zoom), MIN_ZOOM, MAX_ZOOM);
  const grid = Math.pow(2, z);
  const total = grid * grid;
  setInnerText("zoomMeta", `Z${z} · ${grid}×${grid} (${total})`);

  document.querySelectorAll(".zoom-btn").forEach((btn) => {
    const active = Number(btn.dataset.zoom) === z;
    btn.classList.toggle("is-active", active);
    btn.setAttribute("aria-pressed", String(active));
  });
}

function updateInspection(position, modeLabel = "KLIK PETA") {
  const [lng, lat] = position;
  state.lastInspected = [lng, lat];
  state.lastInspectMode = modeLabel;

  const tile = latLngToTile(lat, lng, state.zoom);
  const rawUrl = resolveTileUrl(state.tileUrl, tile.z, tile.x, tile.y);

  setInnerText("inspectSourceTag", modeLabel);
  setInnerText("inspectLat", lat.toFixed(5));
  setInnerText("inspectLng", lng.toFixed(5));
  setInnerText("inspectZxy", `Z=${tile.z} · X=${tile.x} · Y=${tile.y}`);
  setInnerText("inspectTileUrl", rawUrl);

  const linkEl = container("inspectTileLink");
  if (linkEl) linkEl.href = rawUrl;

  const thumbEl = container("inspectThumb");
  if (thumbEl) {
    thumbEl.src = rawUrl;
    thumbEl.alt = `Pratinjau tile Z=${tile.z} X=${tile.x} Y=${tile.y}`;
  }
}

/* ==========================================================================
   Pelacak Request Tile Aktif pada Viewport CrootJS
   ========================================================================== */
function parseTileCoordsFromSrc(src) {
  const match = src.match(/\/(\d+)\/(\d+)\/(\d+)(?:\.[a-z0-9]+)?(?:\?.*)?$/i);
  if (!match) return null;
  return {
    z: Number(match[1]),
    x: Number(match[2]),
    y: Number(match[3]),
  };
}

function renderActiveTilesList() {
  const listEl = container("activeTilesList");
  const countEl = container("activeTilesCount");
  const tilePane = document.querySelector("#map .crootjs-map-tiles");
  if (!listEl || !countEl || !tilePane) return;

  const imgs = Array.from(tilePane.querySelectorAll("img.crootjs-map-tile"));
  const entries = imgs.map((img) => {
    const rawAttr = img.getAttribute("src") || img.src;
    const coords = parseTileCoordsFromSrc(rawAttr);
    let status = "LOAD";
    let statusClass = "is-load";

    if (img.dataset.tileState === "err" || (img.complete && img.naturalWidth === 0)) {
      status = "ERR";
      statusClass = "is-err";
    } else if (
      img.dataset.tileState === "ok" ||
      img.classList.contains("crootjs-map-tile-loaded") ||
      (img.complete && img.naturalWidth > 0)
    ) {
      status = "OK";
      statusClass = "is-ok";
    }

    return {
      rawUrl: rawAttr,
      coords,
      status,
      statusClass,
    };
  });

  // Urutkan berdasarkan Z desc, lalu X asc, Y asc
  entries.sort((a, b) => {
    if (!a.coords || !b.coords) return a.rawUrl.localeCompare(b.rawUrl);
    if (b.coords.z !== a.coords.z) return b.coords.z - a.coords.z;
    if (a.coords.x !== b.coords.x) return a.coords.x - b.coords.x;
    return a.coords.y - b.coords.y;
  });

  const loadedCount = entries.filter((e) => e.status === "OK").length;
  countEl.textContent = `${entries.length} aktif · ${loadedCount} OK`;

  listEl.replaceChildren();
  if (entries.length === 0) {
    const empty = document.createElement("li");
    empty.className = "tile-empty";
    empty.textContent = "Belum ada request tile aktif.";
    listEl.appendChild(empty);
    return;
  }

  const fragment = document.createDocumentFragment();
  entries.forEach((entry) => {
    const li = document.createElement("li");
    li.className = "tile-request-item";

    const a = document.createElement("a");
    a.className = "tile-row-link";
    a.href = entry.rawUrl;
    a.target = "_blank";
    a.rel = "noopener";

    const coordSpan = document.createElement("span");
    coordSpan.className = "tile-coord";
    coordSpan.textContent = entry.coords
      ? `${entry.coords.z}/${entry.coords.x}/${entry.coords.y}`
      : "tile";

    const statusSpan = document.createElement("span");
    statusSpan.className = `tile-status ${entry.statusClass}`;
    statusSpan.textContent = entry.status;

    const fileSpan = document.createElement("span");
    fileSpan.className = "tile-file";
    fileSpan.textContent = entry.coords
      ? `${entry.coords.y}.png ↗`
      : "buka ↗";

    a.append(coordSpan, statusSpan, fileSpan);
    li.appendChild(a);
    fragment.appendChild(li);
  });

  listEl.appendChild(fragment);
}

function scheduleActiveTilesSync() {
  if (state.tileSyncFrame) return;
  state.tileSyncFrame = requestAnimationFrame(() => {
    state.tileSyncFrame = 0;
    renderActiveTilesList();
  });
}

function attachTileListeners(img) {
  if (img.dataset.boundTileListener) return;
  img.dataset.boundTileListener = "1";

  img.addEventListener("load", () => {
    img.dataset.tileState = "ok";
    scheduleActiveTilesSync();
  });
  img.addEventListener("error", () => {
    img.dataset.tileState = "err";
    scheduleActiveTilesSync();
  });
}

function observeTilePane() {
  if (state.tileObserver) {
    state.tileObserver.disconnect();
    state.tileObserver = null;
  }

  const tilePane = document.querySelector("#map .crootjs-map-tiles");
  if (!tilePane) return;

  tilePane.querySelectorAll("img.crootjs-map-tile").forEach(attachTileListeners);

  state.tileObserver = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      mutation.addedNodes.forEach((node) => {
        if (node instanceof HTMLImageElement) {
          attachTileListeners(node);
        }
      });
    });
    scheduleActiveTilesSync();
  });

  state.tileObserver.observe(tilePane, {
    childList: true,
    attributes: true,
    attributeFilter: ["class", "src"],
    subtree: true,
  });

  scheduleActiveTilesSync();
}

/* ==========================================================================
   Inisialisasi & Pemasangan Ulang Peta CrootJS
   ========================================================================== */
function mountMap(tileUrl, center = state.center, zoom = state.zoom) {
  if (state.stopMove) {
    state.stopMove();
    state.stopMove = null;
  }
  if (state.removeScaleBar) {
    state.removeScaleBar();
    state.removeScaleBar = null;
  }
  if (state.bandungMarker) {
    state.bandungMarker.remove();
    state.bandungMarker = null;
  }

  state.tileUrl = tileUrl;
  state.center = center.slice();
  state.zoom = clamp(zoom, MIN_ZOOM, MAX_ZOOM);

  const isOsm = tileUrl === OSM_TILE_URL;

  createMap(MAP_ID, {
    center: state.center,
    zoom: state.zoom,
    minZoom: MIN_ZOOM,
    maxZoom: MAX_ZOOM,
    tiles: tileUrl,
    attribution: isOsm
      ? undefined
      : "Basemap Monokrom · Natural Earth 50m (EPSG:3857)",
    zoomControl: true,
    scrollZoom: true,
  });

  state.removeScaleBar = addScaleBar(MAP_ID, "bottom-left");

  // Marker titik referensi Bandung
  const bandungTile = latLngToTile(BANDUNG.lat, BANDUNG.lng, state.zoom);
  const bandungTileUrl = resolveTileUrl(
    state.tileUrl,
    bandungTile.z,
    bandungTile.x,
    bandungTile.y
  );

  state.bandungMarker = addMarker(MAP_ID, BANDUNG.position, {
    color: "#d97706",
    label: "Bandung",
    title: "Titik Referensi: Bandung (-6.9175, 107.6191)",
    popup: buildPopupNode(
      "TITIK REFERENSI // BANDUNG",
      BANDUNG.lat,
      BANDUNG.lng,
      bandungTile,
      bandungTileUrl
    ),
    onClick: () => {
      updateInspection(BANDUNG.position, "REFERENSI");
    },
  });

  state.stopMove = onMapMove(MAP_ID, (view) => {
    state.center = view.center.slice();
    state.zoom = view.zoom;

    const zRound = clamp(Math.round(view.zoom), MIN_ZOOM, MAX_ZOOM);
    setInnerText(
      "liveCenter",
      `LAT ${view.center[1].toFixed(4)} · LNG ${view.center[0].toFixed(4)} · Z${zRound}`
    );
    updateZoomControls(view.zoom);
    updateInspection(state.lastInspected, state.lastInspectMode);
    scheduleActiveTilesSync();
  });

  onMapClick(MAP_ID, (position) => {
    const [lng, lat] = position;
    const view = getMapView(MAP_ID);
    const z = view ? view.zoom : state.zoom;
    const tile = latLngToTile(lat, lng, z);
    const rawUrl = resolveTileUrl(state.tileUrl, tile.z, tile.x, tile.y);

    updateInspection(position, "KLIK PETA");
    showPopup(
      MAP_ID,
      position,
      buildPopupNode("INSPEKSI KOORDINAT", lat, lng, tile, rawUrl)
    );
  });

  updateSourceIndicators();
  updateZoomControls(state.zoom);
  setInnerText(
    "liveCenter",
    `LAT ${state.center[1].toFixed(4)} · LNG ${state.center[0].toFixed(4)} · Z${Math.round(state.zoom)}`
  );
  updateInspection(state.lastInspected, state.lastInspectMode);
  observeTilePane();
}

/* ==========================================================================
   Kontrol Drawer Kiri (Responsif Desktop & Mobile)
   ========================================================================== */
function setDrawerState(open) {
  const drawer = container("controlDrawer");
  const toggleBtn = container("drawerToggle");
  const backdrop = container("drawerBackdrop");
  if (!drawer || !toggleBtn || !backdrop) return;

  const isDesktop = desktopQuery.matches;
  toggleBtn.setAttribute("aria-expanded", String(open));

  if (isDesktop) {
    drawer.classList.remove("is-open");
    drawer.classList.toggle("is-collapsed", !open);
    backdrop.hidden = true;
  } else {
    drawer.classList.remove("is-collapsed");
    drawer.classList.toggle("is-open", open);
    backdrop.hidden = !open;
  }
}

function isDrawerOpen() {
  const drawer = container("controlDrawer");
  if (!drawer) return false;
  return desktopQuery.matches
    ? !drawer.classList.contains("is-collapsed")
    : drawer.classList.contains("is-open");
}

/* ==========================================================================
   Muat Manifest Static Tile Backend
   ========================================================================== */
async function loadManifest() {
  try {
    const res = await fetch("./tiles/manifest.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const kb = (data.total_bytes / 1024).toFixed(1);
    setInnerText(
      "manifestSummary",
      `${data.total_tiles} tile PNG · Z${data.min_zoom}–Z${data.max_zoom} · ${data.projection} · ${kb} KB`
    );
  } catch {
    setInnerText(
      "manifestSummary",
      "Manifest tidak dapat dibaca (jalankan lewat server HTTP/GitHub Pages)."
    );
  }
}

/* ==========================================================================
   Bootstrap Aplikasi
   ========================================================================== */
runAfterDOM(() => {
  // Status awal drawer: terbuka di desktop, tertutup di tablet/mobile agar peta lega
  setDrawerState(desktopQuery.matches);

  desktopQuery.addEventListener("change", (event) => {
    setDrawerState(event.matches);
  });

  onClick("drawerToggle", () => {
    setDrawerState(!isDrawerOpen());
  });

  onClick("drawerBackdrop", () => {
    setDrawerState(false);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !desktopQuery.matches && isDrawerOpen()) {
      setDrawerState(false);
      container("drawerToggle")?.focus();
    }
  });

  // Form ganti template URL tile
  const form = container("tileUrlForm");
  form?.addEventListener("submit", (event) => {
    event.preventDefault();
    const raw = getValue("tileUrlInput");
    const check = validateTileTemplate(raw);
    if (!check.valid) {
      setFeedback(check.message, "error");
      return;
    }
    const view = getMapView(MAP_ID);
    mountMap(
      check.url,
      view ? view.center : state.center,
      view ? view.zoom : state.zoom
    );
    setFeedback("Template URL tile diterapkan.", "ok");
  });

  onClick("useLocalBtn", () => {
    setValue("tileUrlInput", LOCAL_TILE_URL);
    const view = getMapView(MAP_ID);
    mountMap(
      LOCAL_TILE_URL,
      view ? view.center : state.center,
      view ? view.zoom : state.zoom
    );
    setFeedback("Beralih ke backend statis lokal (./tiles/{z}/{x}/{y}.png).", "ok");
  });

  onClick("compareOsmBtn", () => {
    setValue("tileUrlInput", OSM_TILE_URL);
    const view = getMapView(MAP_ID);
    mountMap(
      OSM_TILE_URL,
      view ? view.center : state.center,
      view ? view.zoom : state.zoom
    );
    setFeedback("Mode pembanding OpenStreetMap aktif (Z0–Z4).", "ok");
  });

  onClick("focusBandungBtn", () => {
    setMapView(MAP_ID, BANDUNG.position, 4);
    updateInspection(BANDUNG.position, "REFERENSI");
    const tile = latLngToTile(BANDUNG.lat, BANDUNG.lng, 4);
    const rawUrl = resolveTileUrl(state.tileUrl, tile.z, tile.x, tile.y);
    showPopup(
      MAP_ID,
      BANDUNG.position,
      buildPopupNode("TITIK REFERENSI // BANDUNG", BANDUNG.lat, BANDUNG.lng, tile, rawUrl)
    );
  });

  // Tombol perpindahan level zoom 0..4
  document.querySelectorAll(".zoom-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetZoom = Number(btn.dataset.zoom);
      const view = getMapView(MAP_ID);
      const center = view ? view.center : state.center;
      setMapView(MAP_ID, center, targetZoom);
    });
  });

  // Pasang peta perdana dengan backend lokal
  mountMap(LOCAL_TILE_URL, BANDUNG.position, 4);
  loadManifest();
});
