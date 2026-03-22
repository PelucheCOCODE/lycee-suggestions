let slides = [];
let currentIdx = -1;
let activePanel = "a";
let timer = null;
let lastUpdated = null;

const panelA = document.getElementById("tv-slide-a");
const panelB = document.getElementById("tv-slide-b");
const container = document.getElementById("tv-container");
const emptyEl = document.getElementById("tv-empty");
const busPanel = document.getElementById("tv-bus-panel");
const busStopsEl = document.getElementById("tv-bus-stops");

let isAutoNewsMode = false;
let lastShownIds = [];
let autonewsLastFetch = 0;
const AUTONEWS_DURATION = 15;
let showBusMode = false;
/** Pagination bus (plein écran TV) */
let tvBusPageCtl = null;
/** Pagination bus (diapo type « bus » dans la présentation) */
let tvSlideBusPageCtl = null;
let currentPriorityAnnouncement = null;

const tvPriorityOverlay = document.getElementById("tv-priority-overlay");
const tvAnnContainer = document.getElementById("tv-announcements");

function esc(s) {
    if (!s) return "";
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
}

async function fetchPriorityAnnouncement() {
    try {
        const res = await fetch("/api/display/priority-announcement");
        const data = await res.json();
        if (data && data.id) {
            currentPriorityAnnouncement = data;
            if (tvPriorityOverlay) {
                tvPriorityOverlay.classList.remove("hidden");
                tvPriorityOverlay.innerHTML = `
                    <div class="display-priority-content">
                        <h1 class="display-priority-title">${esc(data.title)}</h1>
                        ${data.content ? `<div class="display-priority-body">${esc(data.content)}</div>` : ""}
                        ${data.extra_info ? `<div class="display-priority-extra">${esc(data.extra_info)}</div>` : ""}
                    </div>`;
            }
            container?.classList.add("hidden");
            emptyEl?.classList.add("hidden");
            busPanel?.classList.add("hidden");
            if (tvAnnContainer) tvAnnContainer.classList.add("hidden");
            return true;
        }
        currentPriorityAnnouncement = null;
        if (tvPriorityOverlay) tvPriorityOverlay.classList.add("hidden");
        if (tvAnnContainer) tvAnnContainer.classList.remove("hidden");
        return false;
    } catch (e) { return false; }
}

async function fetchTvAnnouncements() {
    if (currentPriorityAnnouncement || !tvAnnContainer) return;
    try {
        const res = await fetch("/api/display/announcements");
        const data = await res.json();
        if (!data || !data.length) {
            tvAnnContainer.innerHTML = "";
            return;
        }
        tvAnnContainer.innerHTML = data.map((a) => {
            const icon = a.style === "urgent" ? "🚨" : a.style === "warning" ? "⚠️" : a.style === "success" ? "✅" : "ℹ️";
            return `<div class="display-announcement display-ann-${a.style || "info"}"><div class="display-ann-icon">${icon}</div><div class="display-ann-text"><strong>${esc(a.title)}</strong>${a.content ? `<span>${esc(a.content)}</span>` : ""}</div></div>`;
        }).join("");
    } catch (e) { /* ignore */ }
}

function destroyTvBusMounts() {
    if (tvBusPageCtl) {
        tvBusPageCtl.destroy();
        tvBusPageCtl = null;
    }
    if (tvSlideBusPageCtl) {
        tvSlideBusPageCtl.destroy();
        tvSlideBusPageCtl = null;
    }
}

async function fetchData() {
    try {
        const hasPriority = await fetchPriorityAnnouncement();
        if (hasPriority) {
            if (timer) clearTimeout(timer);
            timer = setTimeout(fetchData, 15000);
            return;
        }

        fetchTvAnnouncements();

        const exclude = lastShownIds.slice(-2).join(",");
        const url = exclude ? `/api/tv/${TV_SLUG}?exclude=${encodeURIComponent(exclude)}` : `/api/tv/${TV_SLUG}`;
        const res = await fetch(url);
        const data = await res.json();
        const wasBusFullscreen = showBusMode;

        if (data.page_type === "disabled") {
            stopSlideshow();
            container.classList.remove("hidden");
            emptyEl.classList.add("hidden");
            panelA.innerHTML = `<div class="tv-text-slide" style="background:#1e293b;color:#fff;text-align:center;">
                <div class="tv-text-inner">
                    <h1 style="font-size:2rem">${esc(data.message || "Affichage dynamique temporairement désactivé")}</h1>
                </div>
            </div>`;
            panelA.classList.add("tv-slide-active");
            panelB.innerHTML = "";
            panelB.classList.remove("tv-slide-active");
            busPanel.classList.add("hidden");
            if (tvAnnContainer) tvAnnContainer.classList.add("hidden");
            return;
        }
        if (data.show_bus) {
            showBusMode = true;
            stopSlideshow();
            container.classList.add("hidden");
            emptyEl.classList.add("hidden");
            if (tvAnnContainer) tvAnnContainer.classList.add("hidden");
            busPanel.classList.remove("hidden");
            busPanel.classList.add("display-bus-fade-in");
            updateTvBusDisplay();
            return;
        }
        showBusMode = false;
        busPanel.classList.add("hidden");
        busPanel.classList.remove("display-bus-fade-in");
        container.classList.remove("hidden");
        if (tvAnnContainer) tvAnnContainer.classList.remove("hidden");

        if (data.page_type === "autonews") {
            const now = Date.now();
            if (isAutoNewsMode && now - autonewsLastFetch < (AUTONEWS_DURATION - 1) * 1000) return;
            autonewsLastFetch = now;
            isAutoNewsMode = true;
            stopSlideshow();
            if (data.article) {
                container.classList.remove("hidden");
                emptyEl.classList.add("hidden");
                renderAutoNewsSlide(data.article);
                if (data.article.id) lastShownIds = [...lastShownIds.slice(-1), data.article.id];
            } else {
                container.classList.add("hidden");
                emptyEl.classList.remove("hidden");
            }
            return;
        }

        isAutoNewsMode = false;
        if (data.updated_at !== lastUpdated || wasBusFullscreen) {
            lastUpdated = data.updated_at;
            const newSlides = data.slides || [];
            const changed = JSON.stringify(newSlides) !== JSON.stringify(slides);
            slides = newSlides;

            if (!slides.length) {
                container.classList.add("hidden");
                emptyEl.classList.remove("hidden");
                stopSlideshow();
            } else {
                container.classList.remove("hidden");
                emptyEl.classList.add("hidden");
                if (currentIdx < 0 || changed || wasBusFullscreen) {
                    stopSlideshow();
                    startSlideshow();
                }
            }
        }
    } catch (e) { /* ignore */ }
}

function renderAutoNewsSlide(article) {
    const img = article.image_url || "";
    const summary = article.summary || article.excerpt || "";
    const title = article.title || "";
    const bg = "#0f172a";
    const fg = "#ffffff";
    panelA.innerHTML = `<div class="tv-autonews-slide" style="background:${bg};color:${fg}">
        <div class="tv-autonews-inner">
            ${img ? `<div class="tv-autonews-img" style="background-image:url('${esc(img)}')"></div>` : ""}
            <div class="tv-autonews-content">
                <h1 class="tv-autonews-title">${esc(title)}</h1>
                <p class="tv-autonews-summary">${esc(summary)}</p>
            </div>
        </div>
    </div>`;
    panelA.classList.add("tv-slide-active");
    panelB.innerHTML = "";
    panelB.classList.remove("tv-slide-active");
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => fetchData(), AUTONEWS_DURATION * 1000);
}

function startSlideshow() {
    currentIdx = -1;
    nextSlide();
}

function stopSlideshow() {
    if (timer) clearTimeout(timer);
    timer = null;
    destroyTvBusMounts();
    if (busSlideRefreshTimer) { clearInterval(busSlideRefreshTimer); busSlideRefreshTimer = null; }
    currentIdx = -1;
    panelA.className = "tv-slide";
    panelA.innerHTML = "";
    panelB.className = "tv-slide";
    panelB.innerHTML = "";
    activePanel = "a";
}

function nextSlide() {
    if (!slides.length) return;
    currentIdx = (currentIdx + 1) % slides.length;
    const slide = slides[currentIdx];
    if (slide.slide_type !== "bus" && tvSlideBusPageCtl) {
        tvSlideBusPageCtl.destroy();
        tvSlideBusPageCtl = null;
    }
    const incoming = activePanel === "a" ? panelB : panelA;
    const outgoing = activePanel === "a" ? panelA : panelB;

    renderSlide(incoming, slide);

    const transition = slide.transition || "fade";
    incoming.className = `tv-slide tv-transition-${transition}-enter`;

    requestAnimationFrame(() => {
        incoming.classList.add("tv-slide-active");
        incoming.classList.remove(`tv-transition-${transition}-enter`);
        outgoing.classList.remove("tv-slide-active");
        outgoing.classList.add(`tv-transition-${transition}-exit`);

        setTimeout(() => {
            outgoing.className = "tv-slide";
            stopMedia(outgoing);
            outgoing.innerHTML = "";
        }, 800);
    });

    activePanel = activePanel === "a" ? "b" : "a";

    if (timer) clearTimeout(timer);

    if (slides.length === 1) {
        if (slide.slide_type === "bus") scheduleBusSlideRefresh();
        return;
    }

    if (slide.slide_type === "video") {
        const vid = incoming.querySelector("video");
        if (vid && !slide.content?.loop) {
            vid.onended = () => { timer = setTimeout(nextSlide, 500); };
        } else {
            timer = setTimeout(nextSlide, (slide.duration || 15) * 1000);
        }
    } else {
        timer = setTimeout(nextSlide, (slide.duration || 10) * 1000);
    }
}

function stopMedia(el) {
    el.querySelectorAll("video").forEach(v => { v.pause(); v.src = ""; });
}

function renderSlide(el, slide) {
    const c = slide.content || {};
    switch (slide.slide_type) {
        case "image": renderImageSlide(el, c); break;
        case "video": renderVideoSlide(el, c); break;
        case "multi_image": renderMultiImageSlide(el, c); break;
        case "text": renderTextSlide(el, c); break;
        case "suggestions": renderSuggestionsSlide(el, c); break;
        case "autonews": renderAutoNewsSlideInSlot(el); break;
        case "bus": renderBusSlideInSlot(el); break;
        default: el.innerHTML = "";
    }
}

function renderImageSlide(el, c) {
    const fit = c.fit || "cover";
    const url = c.url || "";
    if (!url) { el.innerHTML = ""; return; }
    const overlay = c.overlay_text ? `<div class="tv-img-overlay tv-img-overlay-${c.overlay_position || "bottom"}">${esc(c.overlay_text)}</div>` : "";
    el.innerHTML = `<div class="tv-img-slide" style="background-image:url('${esc(url)}');background-size:${fit};background-position:center;background-repeat:no-repeat;">${overlay}</div>`;
}

function renderVideoSlide(el, c) {
    const url = c.url || "";
    if (!url) { el.innerHTML = ""; return; }
    const muted = c.muted !== false ? "muted" : "";
    const loop = c.loop !== false ? "loop" : "";
    const fit = c.fit || "cover";
    el.innerHTML = `<video class="tv-video-slide" style="object-fit:${fit}" src="${esc(url)}" autoplay ${muted} ${loop} playsinline></video>`;
}

function renderMultiImageSlide(el, c) {
    const urls = c.urls || [];
    const layout = c.layout || "grid";
    const gap = c.gap || 4;
    if (!urls.length) { el.innerHTML = ""; return; }
    const count = urls.length;
    let cls = "tv-multi-grid";
    if (layout === "row") cls = "tv-multi-row";
    else if (layout === "col") cls = "tv-multi-col";
    else if (count === 2) cls = "tv-multi-2";
    else if (count === 3) cls = "tv-multi-3";
    else if (count >= 4) cls = "tv-multi-4";

    el.innerHTML = `<div class="tv-multi-slide ${cls}" style="gap:${gap}px">${urls.map(u =>
        `<div class="tv-multi-img" style="background-image:url('${esc(u)}')"></div>`
    ).join("")}</div>`;
}

function renderTextSlide(el, c) {
    const bg = c.background_color || "#1e293b";
    const color = c.text_color || "#ffffff";
    const sz = c.font_size || "4rem";
    const align = c.text_align || "center";
    const bgImg = c.background_url ? `background-image:url('${esc(c.background_url)}');background-size:cover;` : "";
    el.innerHTML = `<div class="tv-text-slide" style="background:${bg};color:${color};text-align:${align};${bgImg}">
        <div class="tv-text-inner">
            ${c.title ? `<h1 style="font-size:${sz}">${esc(c.title)}</h1>` : ""}
            ${c.subtitle ? `<h2>${esc(c.subtitle)}</h2>` : ""}
            ${c.body ? `<p>${esc(c.body)}</p>` : ""}
        </div>
    </div>`;
}

async function renderSuggestionsSlide(el, c) {
    const limit = c.limit || 10;
    const bg = c.background_color || "#0f172a";
    const fg = c.text_color || "#ffffff";
    try {
        const res = await fetch("/api/display/suggestions");
        const suggestions = await res.json();
        const list = suggestions.slice(0, limit);
        el.innerHTML = `<div class="tv-suggestions-slide" style="background:${bg};color:${fg}">
            <div class="tv-sugg-header"><span class="tv-sugg-icon">💡</span><span>Boîte à Idées</span></div>
            <div class="tv-sugg-grid">${list.map(s => `
                <div class="tv-sugg-card" style="border-color:${fg}15">
                    <span class="tv-sugg-title">${esc(s.title)}</span>
                    <span class="tv-sugg-votes">♥ ${s.vote_count}</span>
                </div>`).join("")}
            </div>
        </div>`;
    } catch (e) {
        el.innerHTML = `<div class="tv-text-slide" style="background:${bg};color:${fg}"><div class="tv-text-inner"><h1>Suggestions</h1><p>Chargement...</p></div></div>`;
    }
}

function tvBusDeparturesHtml(data) {
    if (!data.available || !data.departures?.length) return "";
    const deps = data.departures;
    const theory = data.source === "gtfs_static" || data.source === "test";
    const table =
        typeof busBoardTableHtml === "function"
            ? busBoardTableHtml(deps, { showHead: true })
            : "";
    const foot = theory ? `<p class="bus-dep-footnote">Horaires théoriques (GTFS)</p>` : "";
    return `${table}${foot}`;
}

async function renderBusSlideInSlot(el) {
    try {
        if (tvSlideBusPageCtl) {
            tvSlideBusPageCtl.destroy();
            tvSlideBusPageCtl = null;
        }
        const url =
            typeof busDisplayApiUrl === "function"
                ? busDisplayApiUrl("/api/display/bus")
                : "/api/display/bus";
        const res = await fetch(url);
        const data = await res.json();
        if (typeof busLogPayload === "function") busLogPayload(data, "tv-slide");
        const deps = (data.departures || []).slice();
        const theory = data.source === "gtfs_static" || data.source === "test";
        const foot = theory ? `<p class="bus-dep-footnote">Horaires théoriques (GTFS)</p>` : "";
        if (!data.available || !deps.length) {
            el.innerHTML = `<div class="tv-bus-slide-in-slot tv-bus-slide-empty"><p>Prochains bus indisponibles</p></div>`;
            return;
        }
        el.innerHTML = `<div class="tv-bus-slide-in-slot"><div class="tv-bus-viewport-slot bus-dep-viewport"></div>${foot}</div>`;
        const viewport = el.querySelector(".tv-bus-viewport-slot");
        if (viewport && typeof busBoardPagesMount === "function") {
            tvSlideBusPageCtl = busBoardPagesMount(viewport, deps, { pageSwitchMs: 12000 });
        } else {
            const inner = tvBusDeparturesHtml(data);
            el.innerHTML = inner
                ? `<div class="tv-bus-slide-in-slot">${inner}</div>`
                : `<div class="tv-bus-slide-in-slot tv-bus-slide-empty"><p>Prochains bus indisponibles</p></div>`;
        }
    } catch (e) {
        el.innerHTML = `<div class="tv-text-slide" style="background:#0a0f1a;color:#fff"><div class="tv-text-inner"><h1>PROCHAINS BUS</h1><p>Chargement...</p></div></div>`;
    }
}

async function renderAutoNewsSlideInSlot(el) {
    try {
        const res = await fetch("/api/autonews");
        const article = await res.json();
        if (!article) {
            el.innerHTML = `<div class="tv-text-slide" style="background:#0f172a;color:#fff"><div class="tv-text-inner"><h1>AutoNews</h1><p>Aucun article disponible</p></div></div>`;
            return;
        }
        const img = article.image_url || "";
        const summary = article.summary || article.excerpt || "";
        const title = article.title || "";
        const bg = "#0f172a";
        const fg = "#ffffff";
        el.innerHTML = `<div class="tv-autonews-slide" style="background:${bg};color:${fg}">
            <div class="tv-autonews-inner">
                ${img ? `<div class="tv-autonews-img" style="background-image:url('${esc(img)}')"></div>` : ""}
                <div class="tv-autonews-content">
                    <h1 class="tv-autonews-title">${esc(title)}</h1>
                    <p class="tv-autonews-summary">${esc(summary)}</p>
                </div>
            </div>
        </div>`;
    } catch (e) {
        el.innerHTML = `<div class="tv-text-slide" style="background:#0f172a;color:#fff"><div class="tv-text-inner"><h1>AutoNews</h1><p>Chargement...</p></div></div>`;
    }
}

async function updateTvBusDisplay() {
    if (!busStopsEl) return;
    try {
        const url =
            typeof busDisplayApiUrl === "function"
                ? busDisplayApiUrl("/api/display/bus")
                : "/api/display/bus";
        const res = await fetch(url);
        const data = await res.json();
        if (typeof busLogPayload === "function") busLogPayload(data, "tv-panel");
        renderTvBusPanel(data);
    } catch (e) { /* ignore */ }
}

function renderTvBusPanel(data) {
    if (!busStopsEl) return;
    if (tvBusPageCtl) {
        tvBusPageCtl.destroy();
        tvBusPageCtl = null;
    }
    const deps = (data.departures || []).slice();
    const theory = data.source === "gtfs_static" || data.source === "test";
    const foot = theory ? `<p class="bus-dep-footnote">Horaires théoriques (GTFS)</p>` : "";
    if (!data.available || !deps.length) {
        busStopsEl.innerHTML = `<div class="tv-bus-empty"><p>Aucun départ à afficher</p></div>`;
        return;
    }
    busStopsEl.innerHTML = `<div class="tv-bus-inner"><div id="tv-bus-viewport" class="bus-dep-viewport"></div>${foot}</div>`;
    const viewport = document.getElementById("tv-bus-viewport");
    if (viewport && typeof busBoardPagesMount === "function") {
        tvBusPageCtl = busBoardPagesMount(viewport, deps, { pageSwitchMs: 12000 });
    } else {
        const inner = tvBusDeparturesHtml(data);
        busStopsEl.innerHTML = inner || `<div class="tv-bus-empty"><p>Aucun départ à afficher</p></div>`;
    }
}

let busSlideRefreshTimer = null;

function scheduleBusSlideRefresh() {
    if (busSlideRefreshTimer) clearInterval(busSlideRefreshTimer);
    if (slides.length !== 1 || slides[0]?.slide_type !== "bus") return;
    const activeEl = activePanel === "a" ? panelA : panelB;
    busSlideRefreshTimer = setInterval(() => {
        if (slides.length === 1 && slides[0]?.slide_type === "bus" && activeEl) {
            renderBusSlideInSlot(activeEl);
        }
    }, 30000);
}

fetchData();
setInterval(() => {
    fetchData();
    if (showBusMode) updateTvBusDisplay();
}, 3000);
setInterval(() => {
    if (showBusMode) updateTvBusDisplay();
}, 30000);
