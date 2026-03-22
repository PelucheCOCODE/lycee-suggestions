const API = {
    async get(url) {
        const res = await fetch(url, { credentials: "same-origin" });
        if (res.status === 401) { window.location.href = "/admin"; throw new Error("401"); }
        return res.json();
    },
    async post(url, data) {
        const res = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
            credentials: "same-origin",
        });
        let payload = {};
        try {
            payload = await res.json();
        } catch (e) {
            payload = { error: "Réponse serveur invalide" };
        }
        return { data: payload, status: res.status };
    },
    async postFile(url, fd) {
        const res = await fetch(url, { method: "POST", body: fd, credentials: "same-origin" });
        let payload = {};
        try {
            payload = await res.json();
        } catch (e) {
            payload = { error: "Réponse invalide" };
        }
        return { data: payload, status: res.status };
    },
    async put(url, data) {
        const res = await fetch(url, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
            credentials: "same-origin",
        });
        let payload = {};
        try {
            payload = await res.json();
        } catch (e) {
            payload = { error: "Réponse serveur invalide" };
        }
        return { data: payload, status: res.status };
    },
    async delete(url) {
        const res = await fetch(url, { method: "DELETE", credentials: "same-origin" });
        let payload = {};
        try {
            payload = await res.json();
        } catch (e) {
            payload = {};
        }
        return { data: payload, status: res.status };
    },
};

const STATUSES = ["En attente", "En étude", "Acceptée", "Refusée", "En cours de mise en place", "Terminée"];
const CATEGORIES = ["Cantine", "Infrastructure", "Vie scolaire", "Pédagogie", "Numérique", "Bien-être", "Autre"];
const CATEGORY_COLORS = { "Cantine": "#f59e0b", "Infrastructure": "#6366f1", "Vie scolaire": "#3b82f6", "Pédagogie": "#8b5cf6", "Numérique": "#06b6d4", "Bien-être": "#22c55e", "Autre": "#94a3b8" };
const STATUS_COLORS = { "En attente": "#94a3b8", "En étude": "#3b82f6", "Acceptée": "#22c55e", "Refusée": "#ef4444", "En cours de mise en place": "#f59e0b", "Terminée": "#8b5cf6" };
const CALIB_STATUS_LABELS = { pending: "En attente", processed: "Traité", validated: "Validé", rejected: "Rejeté" };
const CALIB_STATUS_COLORS = { pending: "#94a3b8", processed: "#3b82f6", validated: "#22c55e", rejected: "#ef4444" };

let allSuggestions = [];
let chartCategories = null;
let chartStatuses = null;
let allCalibExamples = [];
let calibFilter = "";
let calibBatchFilter = "";

// ==================== Init ====================

async function init() {
    setupNavigation();
    setupFilters();
    setupLocations();
    setupSuggestionProcessing();
    setupCalibration();
    setupTrace();
    setupCalibrationVerify();
    setupCvlOfficialInfo();
    setupAnnouncements();
    setupSettings();
    setupLLMResources();
    setupDisplayManager();
    setupCvlProposal();
    setupBus();
    setupMusicPoll();
    setupSpotifyApiCard();
    setupSuggestionsHub();
    setupBackup();
    setupHistoryModal();
    setupLogsSection();
    setupDilemmas();
    setupArchiveDetailModal();
    await loadSettings();
    if (!restoreAdminSectionFromStorage()) {
        await loadDashboard();
    }
    loadPriorityBanner();
    setInterval(() => {
        const dash = document.getElementById("section-dashboard");
        if (dash?.classList.contains("active")) loadDashboard();
    }, 60000);
    setInterval(loadPriorityBanner, 15000);
    setInterval(() => {
        const sug = document.getElementById("section-suggestions");
        if (sug?.classList.contains("active")) loadAdminSuggestions();
    }, 9000);
    setInterval(() => {
        const ann = document.getElementById("section-announcements");
        if (ann?.classList.contains("active")) loadAnnouncements();
    }, 8000);
    if (location.hash.startsWith("#suggestion=")) {
        const sid = parseInt(location.hash.replace("#suggestion=", ""), 10);
        if (Number.isFinite(sid)) {
            setTimeout(() => openSuggestionFocusFromLog(sid), 500);
        }
    }
}

const ADMIN_SECTION_LOADERS = {
    suggestions: () => loadAdminSuggestions(),
    logs: () => loadLogs(),
    locations: () => loadLocations(),
    dashboard: () => loadDashboard(),
    engagement: () => loadEngagement(),
    dilemmas: () => loadDilemmas(),
    calibration: () => loadCalibration(),
    trace: () => setupTrace(),
    "calibration-verify": () => loadCalibrationVerify(),
    announcements: () => loadAnnouncements(),
    "llm-resources": () => loadLLMResources(),
    "display-manager": () => loadDisplayManager(),
    "cvl-proposal": () => loadCvlProposal(),
    "cvl-official-info": () => loadCvlOfficialInfo(),
    bus: () => loadBusSettings(),
    "music-poll": () => loadMusicPollAdmin(),
    backup: () => loadBackup(),
    development: () => loadDevelopment(),
};

function activateAdminSection(section) {
    const item = document.querySelector(`.nav-item[data-section="${section}"]`);
    const secEl = document.getElementById(`section-${section}`);
    if (!item || !secEl) return;
    document.querySelectorAll(".nav-item").forEach((n) => n.classList.remove("active"));
    item.classList.add("active");
    document.querySelectorAll(".admin-section").forEach((s) => s.classList.remove("active"));
    secEl.classList.add("active");
    try {
        sessionStorage.setItem("admin_active_section", section);
    } catch (e) {
        /* private mode */
    }
    const loader = ADMIN_SECTION_LOADERS[section];
    if (loader) loader();
}

function restoreAdminSectionFromStorage() {
    try {
        const saved = sessionStorage.getItem("admin_active_section");
        if (saved && document.querySelector(`.nav-item[data-section="${saved}"]`)) {
            activateAdminSection(saved);
            return true;
        }
    } catch (e) {
        /* ignore */
    }
    return false;
}

function setupNavigation() {
    document.querySelectorAll(".nav-item").forEach((item) => {
        item.addEventListener("click", () => {
            activateAdminSection(item.dataset.section);
        });
    });
}

function setupFilters() {
    const catS = document.getElementById("admin-filter-category");
    CATEGORIES.forEach((c) => { const o = document.createElement("option"); o.value = c; o.textContent = c; catS.appendChild(o); });
    const stS = document.getElementById("admin-filter-status");
    STATUSES.forEach((s) => { const o = document.createElement("option"); o.value = s; o.textContent = s; stS.appendChild(o); });
    catS.addEventListener("change", renderAdminSuggestions);
    stS.addEventListener("change", renderAdminSuggestions);
    document.getElementById("admin-search").addEventListener("input", renderAdminSuggestions);
}

function setupLocations() {
    document.getElementById("add-location-btn").addEventListener("click", addLocation);
    document.getElementById("location-name-input").addEventListener("keydown", (e) => { if (e.key === "Enter") addLocation(); });
}

function setupSuggestionProcessing() {
    document.getElementById("process-pending-btn").addEventListener("click", async () => {
        const btn = document.getElementById("process-pending-btn");
        btn.disabled = true;
        btn.textContent = "Traitement en cours...";
        try {
            const { data } = await API.post("/api/admin/suggestions/process-pending");
            btn.textContent = `${data.processed} traitee(s)`;
            setTimeout(() => { btn.textContent = "Traiter les suggestions en attente"; btn.disabled = false; }, 2000);
            loadAdminSuggestions();
        } catch (e) {
            btn.textContent = "Erreur";
            setTimeout(() => { btn.textContent = "Traiter les suggestions en attente"; btn.disabled = false; }, 2000);
        }
    });
}

// ==================== Dashboard ====================

async function loadDashboard() {
    try { const stats = await API.get("/api/admin/stats"); renderDashboard(stats); } catch (e) { console.error(e); }
}
let lastDashboardStats = null;
function renderDashboard(stats) {
    document.getElementById("stat-total").textContent = stats.total;
    document.getElementById("stat-votes").textContent = stats.top_voted.reduce((s, x) => s + x.vote_count, 0);
    document.getElementById("stat-accepted").textContent = stats.by_status["Acceptée"] || 0;
    document.getElementById("stat-pending").textContent = stats.by_status["En attente"] || 0;
    const dataKey = JSON.stringify({ cat: stats.by_category, st: stats.by_status });
    if (lastDashboardStats !== dataKey) {
        lastDashboardStats = dataKey;
        renderCategoryChart(stats.by_category);
        renderStatusChart(stats.by_status);
    }
    renderTopVoted(stats.top_voted);
    const cvM = document.getElementById("chart-suggestions-month");
    if (cvM && typeof Chart !== "undefined" && stats.suggestions_per_month) {
        if (chartSuggestionsMonth) chartSuggestionsMonth.destroy();
        const pm = stats.suggestions_per_month;
        chartSuggestionsMonth = new Chart(cvM, {
            type: "bar",
            data: {
                labels: pm.map((x) => x.month),
                datasets: [
                    {
                        label: "Suggestions créées",
                        data: pm.map((x) => x.count),
                        backgroundColor: "#6366f1",
                        borderRadius: 6,
                    },
                ],
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false } },
                scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } },
            },
        });
    }
}
function renderCategoryChart(d) {
    const cv = document.getElementById("chart-categories");
    const l = Object.keys(d).filter((k) => d[k] > 0);
    if (chartCategories) chartCategories.destroy();
    chartCategories = new Chart(cv, { type: "doughnut", data: { labels: l, datasets: [{ data: l.map((k) => d[k]), backgroundColor: l.map((k) => CATEGORY_COLORS[k] || "#94a3b8"), borderWidth: 0 }] }, options: { responsive: true, plugins: { legend: { position: "bottom" } } } });
}
function renderStatusChart(d) {
    const cv = document.getElementById("chart-statuses");
    const l = Object.keys(d).filter((k) => d[k] > 0);
    if (chartStatuses) chartStatuses.destroy();
    chartStatuses = new Chart(cv, { type: "bar", data: { labels: l, datasets: [{ data: l.map((k) => d[k]), backgroundColor: l.map((k) => STATUS_COLORS[k] || "#94a3b8"), borderWidth: 0, borderRadius: 6 }] }, options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } } } });
}
function renderTopVoted(s) {
    const c = document.getElementById("top-voted-list");
    if (!s.length) { c.innerHTML = '<p class="empty-msg">Aucune suggestion</p>'; return; }
    c.innerHTML = s.map((x) => `<div class="mini-suggestion-item"><span class="mini-suggestion-title">${esc(x.title)}</span><span class="mini-suggestion-votes">♥ ${x.vote_count}</span></div>`).join("");
}

let chartEngMood = null;
let chartEngPresence = null;
let chartEngCards = null;
let chartEngActivity = null;
let chartSuggestionsMonth = null;
let chartMoodMonth = null;
let chartMoodHour = null;
let chartImpMonth = null;

let quillDev = null;
let devSaveTimer = null;

async function loadEngagement() {
    try {
        const stats = await API.get("/api/admin/engagement-stats");
        renderEngagement(stats);
    } catch (e) {
        console.error(e);
    }
}

function renderEngagement(s) {
    const ref = s.reference || {};
    const refEl = document.getElementById("engagement-reference");
    if (refEl) {
        refEl.innerHTML = `
            <dl class="eng-ref-dl">
                <dt>Fuseau horaire</dt><dd>${esc(String(ref.timezone || ""))}</dd>
                <dt>Jour (agrégation)</dt><dd>${esc(String(ref.day_today || ""))}</dd>
                <dt>Seuil « enflammé » (outline)</dt><dd>${ref.hot_threshold ?? 70} / 100</dd>
                <dt>Score activité</dt><dd>${esc(String(ref.activity_score_formula || ""))}</dd>
                <dt>% popularité (idée simple)</dt><dd>${esc(String(ref.popularity_pct_simple || ""))}</dd>
                <dt>% popularité (débat)</dt><dd>${esc(String(ref.popularity_pct_debate || ""))}</dd>
                <dt>Percentile « plus actifs »</dt><dd>${esc(String(ref.percentile || ""))}</dd>
            </dl>`;
    }
    const elImp = document.getElementById("eng-stat-imp");
    const elGuess = document.getElementById("eng-stat-guess");
    const elAcc = document.getElementById("eng-stat-guess-acc");
    const elMsg = document.getElementById("eng-stat-msg");
    if (elImp) elImp.textContent = s.importance_votes_total ?? "—";
    if (elGuess) elGuess.textContent = s.guess_total ?? "—";
    if (elAcc) elAcc.textContent = s.guess_accuracy_pct != null ? `${s.guess_accuracy_pct}%` : "—";
    if (elMsg) elMsg.textContent = s.community_messages_last_7d ?? "—";

    const moods = s.moods_today || {};
    const moodLabels = Object.keys(moods);
    const moodCv = document.getElementById("chart-eng-mood");
    if (moodCv && typeof Chart !== "undefined") {
        if (chartEngMood) chartEngMood.destroy();
        chartEngMood = new Chart(moodCv, {
            type: "bar",
            data: {
                labels: moodLabels.length ? moodLabels : ["—"],
                datasets: [{ label: "Réponses", data: moodLabels.length ? moodLabels.map((k) => moods[k]) : [0], backgroundColor: "#6366f1", borderRadius: 6 }],
            },
            options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } } },
        });
    }

    const pres = s.presence_by_day || [];
    const presCv = document.getElementById("chart-eng-presence");
    if (presCv && typeof Chart !== "undefined") {
        if (chartEngPresence) chartEngPresence.destroy();
        chartEngPresence = new Chart(presCv, {
            type: "line",
            data: {
                labels: pres.map((p) => p.day),
                datasets: [{ label: "Sessions uniques / jour", data: pres.map((p) => p.count), borderColor: "#f97316", backgroundColor: "rgba(249,115,22,0.15)", fill: true, tension: 0.25 }],
            },
            options: { responsive: true, plugins: { legend: { display: true } }, scales: { y: { beginAtZero: true } } },
        });
    }

    const cards = s.cards_done_today_by_type || {};
    const cKeys = Object.keys(cards);
    const cardsCv = document.getElementById("chart-eng-cards");
    if (cardsCv && typeof Chart !== "undefined") {
        if (chartEngCards) chartEngCards.destroy();
        const colors = ["#22c55e", "#3b82f6", "#f59e0b", "#a855f7", "#ec4899", "#14b8a6"];
        chartEngCards = new Chart(cardsCv, {
            type: "doughnut",
            data: {
                labels: cKeys.length ? cKeys : ["—"],
                datasets: [{ data: cKeys.length ? cKeys.map((k) => cards[k]) : [0], backgroundColor: cKeys.map((_, i) => colors[i % colors.length]), borderWidth: 0 }],
            },
            options: { responsive: true, plugins: { legend: { position: "bottom" } } },
        });
    }

    const actCv = document.getElementById("chart-eng-activity");
    if (actCv && typeof Chart !== "undefined") {
        if (chartEngActivity) chartEngActivity.destroy();
        chartEngActivity = new Chart(actCv, {
            type: "bar",
            data: {
                labels: ["Swipes moy. / pers.", "Likes moy. / pers."],
                datasets: [{ data: [s.avg_swipes_today || 0, s.avg_likes_today || 0], backgroundColor: ["#0ea5e9", "#e11d48"], borderRadius: 8 }],
            },
            options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } },
        });
    }

    const top = document.getElementById("engagement-top-importance");
    const topList = s.top_by_importance || [];
    if (top) {
        if (!topList.length) top.innerHTML = '<p class="empty-msg">Aucune donnée d’importance</p>';
        else {
            top.innerHTML = topList.map((x) => `<div class="mini-suggestion-item"><span class="mini-suggestion-title">${esc(x.title)}</span><span class="mini-suggestion-votes">🔥 ${Number(x.importance_score || 0).toFixed(1)} · ♥ ${x.vote_count}</span></div>`).join("");
        }
    }

    const moodColors = { bien: "#22c55e", bof: "#94a3b8", fatigue: "#f97316", stresse: "#dc2626" };
    const mbm = s.mood_by_month || {};
    const monthsM = Object.keys(mbm).sort();
    const moodKeysSet = new Set();
    monthsM.forEach((mk) => Object.keys(mbm[mk] || {}).forEach((k) => moodKeysSet.add(k)));
    const moodKeys = Array.from(moodKeysSet);
    const cvMm = document.getElementById("chart-mood-month");
    if (cvMm && typeof Chart !== "undefined") {
        if (chartMoodMonth) chartMoodMonth.destroy();
        chartMoodMonth = new Chart(cvMm, {
            type: "bar",
            data: {
                labels: monthsM.length ? monthsM : ["—"],
                datasets: moodKeys.length
                    ? moodKeys.map((mood) => ({
                          label: mood,
                          data: monthsM.length ? monthsM.map((mk) => (mbm[mk] && mbm[mk][mood]) || 0) : [0],
                          backgroundColor: moodColors[mood] || "#64748b",
                          stack: "m",
                      }))
                    : [{ label: "—", data: [0], backgroundColor: "#e2e8f0" }],
            },
            options: {
                responsive: true,
                plugins: { legend: { position: "bottom" } },
                scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true, ticks: { stepSize: 1 } } },
            },
        });
    }

    const mbh = s.mood_by_hour || {};
    const cvMh = document.getElementById("chart-mood-hour");
    if (cvMh && typeof Chart !== "undefined") {
        if (chartMoodHour) chartMoodHour.destroy();
        const hours = Array.from({ length: 24 }, (_, i) => String(i));
        chartMoodHour = new Chart(cvMh, {
            type: "line",
            data: {
                labels: hours.map((h) => `${h}h`),
                datasets: moodKeys.length
                    ? moodKeys.map((mood) => ({
                          label: mood,
                          data: hours.map((h) => (mbh[h] && mbh[h][mood]) || 0),
                          borderColor: moodColors[mood] || "#64748b",
                          backgroundColor: "transparent",
                          tension: 0.25,
                          fill: false,
                      }))
                    : [{ label: "—", data: hours.map(() => 0), borderColor: "#cbd5e1" }],
            },
            options: {
                responsive: true,
                plugins: { legend: { position: "bottom" } },
                scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } },
            },
        });
    }

    const imb = s.importance_votes_by_month || {};
    const impMonths = Object.keys(imb).sort();
    const cvIm = document.getElementById("chart-imp-month");
    if (cvIm && typeof Chart !== "undefined") {
        if (chartImpMonth) chartImpMonth.destroy();
        chartImpMonth = new Chart(cvIm, {
            type: "bar",
            data: {
                labels: impMonths.length ? impMonths : ["—"],
                datasets: [
                    {
                        label: "Votes importance",
                        data: impMonths.length ? impMonths.map((m) => imb[m] || 0) : [0],
                        backgroundColor: "#a855f7",
                        borderRadius: 6,
                    },
                ],
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false } },
                scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } },
            },
        });
    }
}

function setupDilemmas() {
    const form = document.getElementById("dilemma-form");
    if (!form || form.dataset.bound) return;
    form.dataset.bound = "1";
    const statusEl = document.getElementById("dilemma-form-status");
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const title = document.getElementById("dilemma-title").value.trim();
        const option_a = document.getElementById("dilemma-opt-a").value.trim();
        const option_b = document.getElementById("dilemma-opt-b").value.trim();
        const scheduled_day = document.getElementById("dilemma-day").value;
        if (!scheduled_day) {
            if (statusEl) statusEl.textContent = "Choisis une date.";
            return;
        }
        const { data, status } = await API.post("/api/admin/dilemmas", { title, option_a, option_b, scheduled_day });
        if (status === 201) {
            if (statusEl) statusEl.textContent = "Créé.";
            form.reset();
            setTimeout(() => { if (statusEl) statusEl.textContent = ""; }, 2800);
            loadDilemmas();
        } else if (statusEl) statusEl.textContent = (data && data.error) || "Erreur";
    });
    const list = document.getElementById("dilemma-list");
    if (list && !list.dataset.dilemmaBound) {
        list.dataset.dilemmaBound = "1";
        list.addEventListener("click", async (e) => {
            const del = e.target.closest("[data-action='delete']");
            const save = e.target.closest("[data-action='save']");
            if (del) {
                const id = parseInt(del.dataset.id, 10);
                if (!confirm("Supprimer ce dilemme ? Les votes associés seront aussi supprimés.")) return;
                const { status } = await API.delete(`/api/admin/dilemmas/${id}`);
                if (status === 200) loadDilemmas();
                return;
            }
            if (save) {
                const id = parseInt(save.dataset.id, 10);
                const card = save.closest(".admin-dilemma-card");
                if (!card) return;
                const title = card.querySelector(".admin-dilemma-edit-title").value.trim();
                const option_a = card.querySelector(".admin-dilemma-edit-a").value.trim();
                const option_b = card.querySelector(".admin-dilemma-edit-b").value.trim();
                const scheduled_day = card.querySelector(".admin-dilemma-edit-day").value;
                const { data, status } = await API.put(`/api/admin/dilemmas/${id}`, { title, option_a, option_b, scheduled_day });
                if (status === 200) loadDilemmas();
                else alert((data && data.error) || "Erreur");
            }
        });
    }
}

async function loadDilemmas() {
    const container = document.getElementById("dilemma-list");
    if (!container) return;
    try {
        const rows = await API.get("/api/admin/dilemmas");
        renderDilemmaList(rows);
    } catch (e) {
        console.error(e);
        container.innerHTML = '<p class="empty-msg">Erreur de chargement</p>';
    }
}

function renderDilemmaList(rows) {
    const el = document.getElementById("dilemma-list");
    if (!el) return;
    if (!rows.length) {
        el.innerHTML = '<p class="empty-msg">Aucun dilemme planifié.</p>';
        return;
    }
    const v = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
    el.innerHTML = rows.map((d) => `
        <article class="admin-dilemma-card" data-id="${d.id}">
            <header class="admin-dilemma-card-head">
                <span class="admin-dilemma-day-pill">${esc(d.scheduled_day)}</span>
                <button type="button" class="btn btn-sm btn-secondary" data-action="delete" data-id="${d.id}">Supprimer</button>
            </header>
            <div class="admin-dilemma-form-grid">
                <div class="form-group admin-dilemma-span-full">
                    <label>Question</label>
                    <input type="text" class="admin-dilemma-edit-title" value="${v(d.title)}" maxlength="220">
                </div>
                <div class="form-group">
                    <label>Option A</label>
                    <input type="text" class="admin-dilemma-edit-a" value="${v(d.option_a)}" maxlength="500">
                </div>
                <div class="form-group">
                    <label>Option B</label>
                    <input type="text" class="admin-dilemma-edit-b" value="${v(d.option_b)}" maxlength="500">
                </div>
                <div class="form-group">
                    <label>Date</label>
                    <input type="date" class="admin-dilemma-edit-day" value="${v(d.scheduled_day)}">
                </div>
            </div>
            <button type="button" class="btn btn-primary btn-sm" data-action="save" data-id="${d.id}">Enregistrer</button>
        </article>
    `).join("");
}

// ==================== Développement (notes Quill) ====================

function loadDevelopment() {
    const el = document.getElementById("dev-notes-editor");
    if (!el || typeof Quill === "undefined") return;
    if (quillDev) return;
    quillDev = new Quill("#dev-notes-editor", {
        theme: "snow",
        modules: {
            toolbar: [
                [{ header: [1, 2, 3, false] }],
                ["bold", "italic", "underline", "strike"],
                [{ list: "ordered" }, { list: "bullet" }],
                [{ color: [] }, { background: [] }],
                ["blockquote", "code-block"],
                ["link"],
                ["clean"],
            ],
        },
    });
    API.get("/api/admin/dev-notes")
        .then((d) => {
            if (d && d.html) quillDev.root.innerHTML = d.html;
        })
        .catch(() => {});
    quillDev.on("text-change", () => {
        clearTimeout(devSaveTimer);
        devSaveTimer = setTimeout(async () => {
            const st = document.getElementById("dev-notes-save-status");
            if (st) st.textContent = "Enregistrement…";
            try {
                await API.put("/api/admin/dev-notes", { html: quillDev.root.innerHTML });
                if (st) st.textContent = "Enregistré.";
                setTimeout(() => {
                    if (st) st.textContent = "";
                }, 2000);
            } catch (e) {
                if (st) st.textContent = "Erreur d’enregistrement.";
            }
        }, 900);
    });
}

// ==================== Historique — modale détail ====================

function setupArchiveDetailModal() {
    document.getElementById("archive-detail-close")?.addEventListener("click", closeArchiveDetailModal);
    document.getElementById("archive-detail-backdrop")?.addEventListener("click", closeArchiveDetailModal);
}

function closeArchiveDetailModal() {
    document.getElementById("archive-detail-modal")?.classList.add("hidden");
    document.body.style.overflow = "";
}

function buildArchiveDetailHtml(d) {
    const arch = d.archive;
    const live = d.live;
    const sid = arch?.suggestion_id || live?.id;
    if (live) {
        const sub = live.subtitle || "";
        let html = `<p class="context-hint">#${sid} · ${esc(live.category || "")} · ${esc(live.status || "")}</p>`;
        html += `<div class="form-group"><label>Résumé / sous-titre (IA)</label><div class="archive-detail-box">${esc(sub)}</div></div>`;
        html += `<div class="form-group"><label>Texte original</label><div class="archive-detail-box">${esc(live.original_text || "")}</div></div>`;
        html += `<div class="form-group"><label>Titre</label><input type="text" id="archive-modal-title" class="control-select" style="width:100%;max-width:100%;box-sizing:border-box" value="${esc(live.title || "")}" maxlength="200"></div>`;
        html += `<div class="form-group"><label>Statut</label><select id="archive-modal-status" class="control-select">${STATUSES.map((st) => `<option value="${esc(st)}" ${live.status === st ? "selected" : ""}>${esc(st)}</option>`).join("")}</select></div>`;
        html += `<div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:12px;align-items:center;">`;
        html += `<button type="button" class="btn btn-primary" id="archive-modal-save-status">Appliquer le statut</button>`;
        html += `<button type="button" class="btn btn-secondary" id="archive-modal-save-title">Mettre à jour le titre</button>`;
        html += `<a class="btn btn-secondary" href="/api/admin/suggestions/${sid}/pdf" target="_blank" rel="noopener">Télécharger PDF</a>`;
        if (live.status === "Terminée" || live.status === "Refusée" || live.status === "En cours de mise en place") {
            html += `<button type="button" class="btn btn-primary" id="archive-modal-republish">Remettre en ligne (Acceptée)</button>`;
        }
        html += `</div>`;
        return html;
    }
    if (arch) {
        let html = `<p class="context-hint">Cette suggestion n’existe plus dans la base active (supprimée ou import).</p>`;
        html += `<div class="form-group"><strong>Titre (archive)</strong><div>${esc(arch.title)}</div></div>`;
        html += `<div class="form-group"><label>Texte original</label><div class="archive-detail-box">${esc(arch.original_text || "")}</div></div>`;
        if (arch.deleted_at) {
            html += `<button type="button" class="btn btn-primary" id="archive-modal-restore-from-archive">Restaurer depuis l’archive</button>`;
        }
        return html;
    }
    return '<p class="empty-msg">Aucune donnée.</p>';
}

function bindArchiveDetailForm(sid, d) {
    const live = d.live;
    document.getElementById("archive-modal-save-status")?.addEventListener("click", async () => {
        if (!live) return;
        const st = document.getElementById("archive-modal-status")?.value;
        const { data, status } = await API.put(`/api/admin/suggestions/${sid}/status`, { status: st });
        if (status >= 400) {
            alert((data && data.error) || "Erreur");
            return;
        }
        await loadSuggestionArchive();
        closeArchiveDetailModal();
    });
    document.getElementById("archive-modal-save-title")?.addEventListener("click", async () => {
        if (!live) return;
        const t = document.getElementById("archive-modal-title")?.value?.trim();
        if (!t || t.length < 3) {
            alert("Titre trop court");
            return;
        }
        const { status } = await API.put(`/api/admin/suggestions/${sid}/title`, { title: t });
        if (status >= 400) {
            alert("Erreur");
            return;
        }
        await loadSuggestionArchive();
        closeArchiveDetailModal();
    });
    document.getElementById("archive-modal-republish")?.addEventListener("click", async () => {
        const { data, status } = await API.put(`/api/admin/suggestions/${sid}/status`, { status: "Acceptée" });
        if (status >= 400) {
            alert((data && data.error) || "Erreur");
            return;
        }
        await loadSuggestionArchive();
        closeArchiveDetailModal();
    });
    document.getElementById("archive-modal-restore-from-archive")?.addEventListener("click", (e) => {
        e.preventDefault();
        restoreSuggestionFromArchive(sid);
        closeArchiveDetailModal();
    });
}

async function openArchiveDetailModal(sid) {
    const modal = document.getElementById("archive-detail-modal");
    const body = document.getElementById("archive-detail-body");
    if (!modal || !body) return;
    body.innerHTML = '<p class="empty-msg">Chargement…</p>';
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
    try {
        const d = await API.get(`/api/admin/suggestions/${sid}/detail`);
        body.innerHTML = buildArchiveDetailHtml(d);
        bindArchiveDetailForm(sid, d);
    } catch (e) {
        body.innerHTML = '<p class="empty-msg">Erreur de chargement.</p>';
    }
}

// ==================== Logs ====================

let logsLastId = 0;
let logsPollInterval = null;

const LOG_EVENT_LABELS = {
    suggestion_submitted: "Soumis",
    suggestion_accepted: "Validé",
    suggestion_rejected: "Refus (traitement)",
    filter_blocked: "Refus (filtre)",
    argument_rejected: "Arg. refusé",
    status_changed: "Statut",
    troll_blocked: "Anti-troll",
    announcement_created: "Annonce IA",
    proposal_argument_submitted: "Débat CVL",
    proposal_argument_accepted: "Débat accepté",
    proposal_argument_rejected: "Débat refusé",
    suggestion_argument_accepted: "Arg. accepté",
    suggestion_deleted: "Supprimé",
    backup_restored: "Backup",
};

const LOG_EVENT_COLORS = {
    suggestion_submitted: "#3b82f6",
    suggestion_accepted: "#22c55e",
    suggestion_rejected: "#ef4444",
    filter_blocked: "#ea580c",
    argument_rejected: "#dc2626",
    status_changed: "#8b5cf6",
    troll_blocked: "#991b1b",
    announcement_created: "#06b6d4",
    proposal_argument_submitted: "#0d9488",
    proposal_argument_accepted: "#059669",
    proposal_argument_rejected: "#be123c",
    suggestion_argument_accepted: "#16a34a",
    suggestion_deleted: "#64748b",
    backup_restored: "#475569",
};

const LOG_REFUSAL_TYPES = new Set([
    "suggestion_rejected", "filter_blocked", "argument_rejected",
    "proposal_argument_rejected", "troll_blocked",
]);

function parseLogDetailJson(detailStr) {
    if (!detailStr || typeof detailStr !== "string") return null;
    const t = detailStr.trim();
    if (!t.startsWith("{")) return null;
    try {
        return JSON.parse(t);
    } catch (e) {
        return null;
    }
}

function logEntryHtml(l) {
    const label = LOG_EVENT_LABELS[l.event_type] || l.event_type;
    const color = LOG_EVENT_COLORS[l.event_type] || "#94a3b8";
    const timeFull = l.created_at
        ? new Date(l.created_at).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "medium" })
        : "";
    const refusal = LOG_REFUSAL_TYPES.has(l.event_type);
    const rowClass = refusal ? "log-item log-item--refusal" : "log-item";
    const detailStr = l.detail && String(l.detail).trim();
    const detailJson = parseLogDetailJson(detailStr);
    let detailHtml = "";
    if (detailStr) {
        const detailTitle = refusal ? "Motif, extrait ou texte concerné" : "Détail";
        let bodyInner = esc(l.detail);
        if (detailJson && (detailJson.title || detailJson.new_status)) {
            bodyInner = [
                detailJson.new_status ? `<div class="log-detail-line"><strong>Statut</strong> · ${esc(detailJson.new_status)}</div>` : "",
                detailJson.title ? `<div class="log-detail-line log-detail-line--title">${esc(detailJson.title)}</div>` : "",
            ]
                .filter(Boolean)
                .join("");
        } else {
            bodyInner = `<div class="log-detail-body">${esc(l.detail)}</div>`;
        }
        detailHtml = `<div class="log-detail-block"><span class="log-detail-title">${detailTitle}</span>${bodyInner}</div>`;
    }
    let actionHtml = "";
    if (detailJson && detailJson.suggestion_id) {
        actionHtml = `<div class="log-detail-actions"><button type="button" class="btn btn-sm btn-secondary log-open-suggestion-btn" data-sid="${detailJson.suggestion_id}">Fiche suggestion #${detailJson.suggestion_id}</button></div>`;
    }
    const metaBits = [];
    if (l.ip) metaBits.push(`IP : ${esc(l.ip)}`);
    if (l.visitor_id) metaBits.push(`Visiteur : ${esc(l.visitor_id)}`);
    const metaHtml = metaBits.length ? `<div class="log-meta">${metaBits.join(" · ")}</div>` : "";
    return `<div class="${rowClass}" data-id="${l.id}"><div class="log-head"><span class="log-badge" style="background:${color}">${esc(label)}</span><time class="log-datetime" datetime="${l.created_at || ""}">${timeFull}</time></div><div class="log-msg">${esc(l.message)}</div>${detailHtml}${actionHtml}${metaHtml}</div>`;
}

async function loadLogs() {
    const el = document.getElementById("logs-list");
    if (!el) return;
    try {
        const logs = await API.get("/api/admin/activity-logs?limit=150");
        el.innerHTML = logs.length ? logs.map((l) => logEntryHtml(l)).join("") : '<p class="empty-msg">Aucun log pour le moment</p>';
        if (logs.length) logsLastId = Math.max(...logs.map((l) => l.id));
        if (!logsPollInterval) logsPollInterval = setInterval(pollLogs, 3000);
    } catch (e) { el.innerHTML = '<p class="empty-msg">Erreur de chargement</p>'; }
}

async function pollLogs() {
    const section = document.getElementById("section-logs");
    if (!section || !section.classList.contains("active")) return;
    try {
        const logs = await API.get(`/api/admin/activity-logs?since_id=${logsLastId}&limit=50`);
        if (!logs.length) return;
        const el = document.getElementById("logs-list");
        logs.reverse().forEach((l) => {
            const div = document.createElement("div");
            div.innerHTML = logEntryHtml(l).trim();
            const node = div.firstElementChild;
            if (!node) return;
            node.classList.add("log-item-new");
            el.insertBefore(node, el.firstChild);
            if (el.querySelector(".empty-msg")) el.querySelector(".empty-msg").remove();
        });
        logsLastId = Math.max(...logs.map((l) => l.id));
    } catch (e) { /* ignore */ }
}

let suggestionArchiveRows = [];

function buildActivityLogsExportParams() {
    const preset = document.getElementById("logs-export-preset")?.value;
    const params = new URLSearchParams();
    if (preset === "today") {
        const day = document.getElementById("logs-export-day")?.value || new Date().toISOString().slice(0, 10);
        params.set("day", day);
    } else if (preset === "24h") {
        params.set("hours", "24");
    } else if (preset === "7d") {
        params.set("hours", "168");
    } else if (preset === "custom") {
        const from = document.getElementById("logs-export-from")?.value;
        const to = document.getElementById("logs-export-to")?.value;
        if (from) params.set("from", new Date(from).toISOString());
        if (to) params.set("to", new Date(to).toISOString());
    }
    return params;
}

async function downloadActivityLogsExport(format) {
    const params = buildActivityLogsExportParams();
    params.set("format", format);
    const res = await fetch(`/api/admin/activity-logs/export?${params}`, { credentials: "same-origin" });
    if (res.status === 401) { window.location.href = "/admin"; return; }
    if (!res.ok) return;
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `activity-logs.${format === "csv" ? "csv" : "json"}`;
    a.click();
    URL.revokeObjectURL(a.href);
}

async function loadSuggestionArchive() {
    const el = document.getElementById("archive-list");
    if (!el) return;
    const q = document.getElementById("archive-search")?.value?.trim() || "";
    const st = document.getElementById("archive-filter-status")?.value || "";
    const del = document.getElementById("archive-deleted-only")?.checked;
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (st) params.set("status", st);
    if (del) params.set("deleted_only", "1");
    try {
        suggestionArchiveRows = await API.get(`/api/admin/suggestion-archive?${params}`);
        renderSuggestionArchive();
    } catch (e) {
        el.innerHTML = '<p class="empty-msg">Erreur de chargement</p>';
    }
}

function renderSuggestionArchive() {
    const el = document.getElementById("archive-list");
    if (!el) return;
    if (!suggestionArchiveRows.length) {
        el.innerHTML = '<p class="empty-msg">Aucune entrée dans l’historique</p>';
        return;
    }
    el.innerHTML = suggestionArchiveRows.map((r) => {
        const del = r.deleted_at ? '<span class="archive-badge archive-badge-del">Supprimée</span>' : "";
        const stClass = r.status === "Refusée" ? "archive-st archive-st--refused" : "archive-st";
        const showMotif = r.reject_reason || r.status === "Refusée";
        const motifHtml = showMotif
            ? `<div class="archive-reason archive-reason--prominent"><strong>Motif du refus (filtre / IA / admin)</strong><div class="archive-motif-text">${r.reject_reason ? esc(r.reject_reason) : '<span class="archive-motif-missing">(aucun motif enregistré dans la base)</span>'}</div></div>`
            : "";
        const orig = r.original_text || "";
        const restore =
            r.deleted_at
                ? `<button type="button" class="btn btn-sm btn-primary archive-restore-btn" data-sid="${r.suggestion_id}">Remettre en ligne</button>`
                : "";
        return `<div class="archive-card archive-card--clickable${r.status === "Refusée" ? " archive-card--refused" : ""}" data-sid="${r.suggestion_id}" title="Cliquer pour le détail (statut, PDF, titre…)">
            <div class="archive-card-head"><span class="archive-id">#${r.suggestion_id}</span> ${del}<span class="${stClass}">${esc(r.status)}</span>${restore}</div>
            <div class="archive-title">${esc(r.title)}</div>
            <div class="archive-meta">${esc(r.category)} · ${r.vote_count ?? 0} soutien${(r.vote_count || 0) !== 1 ? "s" : ""}</div>
            ${motifHtml}
            <div class="archive-original-label">Texte original soumis</div>
            <div class="archive-original-text">${esc(orig)}</div>
        </div>`;
    }).join("");
    el.querySelectorAll(".archive-card--clickable").forEach((card) => {
        card.addEventListener("click", (e) => {
            if (e.target.closest(".archive-restore-btn")) return;
            openArchiveDetailModal(parseInt(card.dataset.sid, 10));
        });
    });
    el.querySelectorAll(".archive-restore-btn").forEach((btn) => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            restoreSuggestionFromArchive(parseInt(btn.dataset.sid, 10));
        });
    });
}

async function restoreSuggestionFromArchive(sid) {
    if (sid == null || Number.isNaN(sid)) return;
    if (!confirm(`Remettre la suggestion #${sid} en ligne ? Elle réapparaîtra comme « En attente » (votes remis à zéro).`)) return;
    try {
        const { data, status } = await API.post("/api/admin/suggestion-archive/restore", { suggestion_id: sid });
        if (status >= 400) {
            alert((data && data.error) || "Erreur");
            return;
        }
        await loadSuggestionArchive();
        await loadAdminSuggestions();
    } catch (e) {
        alert("Erreur réseau.");
    }
}

async function downloadArchiveExport(format) {
    const params = new URLSearchParams();
    const sid = document.getElementById("archive-export-sid")?.value;
    const day = document.getElementById("archive-export-day")?.value;
    const from = document.getElementById("archive-export-from")?.value;
    const to = document.getElementById("archive-export-to")?.value;
    if (sid) params.set("suggestion_id", sid);
    else if (day) params.set("day", day);
    else {
        if (from) params.set("from", new Date(from).toISOString());
        if (to) params.set("to", new Date(to).toISOString());
    }
    params.set("format", format);
    const res = await fetch(`/api/admin/suggestion-archive/export?${params}`, { credentials: "same-origin" });
    if (res.status === 401) { window.location.href = "/admin"; return; }
    if (!res.ok) return;
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `suggestion-archive.${format === "csv" ? "csv" : "json"}`;
    a.click();
    URL.revokeObjectURL(a.href);
}

function setupLogsSection() {
    document.querySelectorAll(".logs-tab").forEach((tab) => {
        tab.addEventListener("click", () => {
            const id = tab.dataset.logsTab;
            document.querySelectorAll(".logs-tab").forEach((t) => {
                t.classList.remove("active");
                t.setAttribute("aria-selected", "false");
            });
            tab.classList.add("active");
            tab.setAttribute("aria-selected", "true");
            document.getElementById("logs-panel-stream")?.classList.toggle("hidden", id !== "stream");
            document.getElementById("logs-panel-archive")?.classList.toggle("hidden", id !== "archive");
            if (id === "stream") loadLogs();
            if (id === "archive") loadSuggestionArchive();
        });
    });
    const st = document.getElementById("archive-filter-status");
    if (st && st.options.length <= 1) {
        STATUSES.forEach((s) => {
            const o = document.createElement("option");
            o.value = s;
            o.textContent = s;
            st.appendChild(o);
        });
    }
    document.getElementById("archive-refresh")?.addEventListener("click", loadSuggestionArchive);
    let archT;
    document.getElementById("archive-search")?.addEventListener("input", () => {
        clearTimeout(archT);
        archT = setTimeout(loadSuggestionArchive, 400);
    });
    document.getElementById("archive-filter-status")?.addEventListener("change", loadSuggestionArchive);
    document.getElementById("archive-deleted-only")?.addEventListener("change", loadSuggestionArchive);

    const preset = document.getElementById("logs-export-preset");
    const syncLogExportUi = () => {
        const v = preset?.value;
        document.getElementById("logs-export-day")?.classList.toggle("hidden", v !== "today");
        document.getElementById("logs-export-from")?.classList.toggle("hidden", v !== "custom");
        document.getElementById("logs-export-to")?.classList.toggle("hidden", v !== "custom");
    };
    preset?.addEventListener("change", syncLogExportUi);
    const dayEl = document.getElementById("logs-export-day");
    if (dayEl && !dayEl.value) dayEl.value = new Date().toISOString().slice(0, 10);
    syncLogExportUi();

    document.getElementById("logs-export-csv")?.addEventListener("click", () => downloadActivityLogsExport("csv"));
    document.getElementById("logs-export-json")?.addEventListener("click", () => downloadActivityLogsExport("json"));
    document.getElementById("archive-export-csv")?.addEventListener("click", () => downloadArchiveExport("csv"));
    document.getElementById("archive-export-json")?.addEventListener("click", () => downloadArchiveExport("json"));

    document.getElementById("logs-list")?.addEventListener("click", (e) => {
        const btn = e.target.closest(".log-open-suggestion-btn");
        if (!btn) return;
        const sid = parseInt(btn.dataset.sid, 10);
        if (Number.isFinite(sid)) void openSuggestionFocusFromLog(sid);
    });
}

// ==================== Suggestions ====================

async function loadAdminSuggestions() {
    try {
        allSuggestions = await API.get("/api/admin/suggestions");
        renderAdminSuggestions();
        await loadSuggestionsHub();
    } catch (e) {
        console.error(e);
    }
}

function navigateToAdminSection(section) {
    document.querySelector(`[data-section="${section}"]`)?.click();
}

async function openSuggestionFocusFromLog(sid) {
    const panel = document.getElementById("suggestion-focus-panel");
    if (!panel) return;
    navigateToAdminSection("suggestions");
    panel.classList.remove("hidden");
    panel.innerHTML = `<div class="suggestion-focus-loading">Chargement…</div>`;
    try {
        history.replaceState(null, "", `${location.pathname}${location.search}#suggestion=${sid}`);
    } catch (e) {
        location.hash = `suggestion=${sid}`;
    }
    requestAnimationFrame(() => {
        try {
            panel.scrollIntoView({ behavior: "smooth", block: "start" });
        } catch (e) {
            panel.scrollIntoView();
        }
    });
    try {
        const data = await API.get(`/api/admin/suggestions/${sid}/stats`);
        const s = data.suggestion;
        const st = data.stats;
        const imp = s.ai_proportion != null ? Math.round(s.ai_proportion * 100) : null;
        const fais = s.ai_feasibility != null ? Math.round(s.ai_feasibility * 100) : null;
        const cout = s.ai_cost != null ? Math.round(s.ai_cost * 100) : null;
        panel.innerHTML = `
<div class="suggestion-focus-inner">
  <div class="suggestion-focus-head">
    <h2 class="suggestion-focus-title">#${s.id} — ${esc(s.title)}</h2>
    <button type="button" class="btn btn-ghost suggestion-focus-close" aria-label="Fermer">×</button>
  </div>
  <div class="suggestion-focus-meta">
    <span class="badge badge-category">${esc(s.category)}</span>
    <span class="badge badge-status">${esc(s.status)}</span>
  </div>
  ${s.subtitle ? `<p class="suggestion-focus-sub">${esc(s.subtitle)}</p>` : ""}
  <p class="suggestion-focus-original">« ${esc(s.original_text)} »</p>
  <div class="suggestion-focus-stats-grid">
    <div class="suggestion-focus-stat"><span class="sf-val">${st.total_votes}</span><span class="sf-lbl">Votes / soutiens</span></div>
    <div class="suggestion-focus-stat"><span class="sf-val">${st.votes_for}</span><span class="sf-lbl">Pour</span></div>
    <div class="suggestion-focus-stat"><span class="sf-val">${st.votes_against}</span><span class="sf-lbl">Contre</span></div>
    <div class="suggestion-focus-stat"><span class="sf-val">${st.arguments_total}</span><span class="sf-lbl">Arguments</span></div>
    <div class="suggestion-focus-stat"><span class="sf-val">${st.arguments_pending}</span><span class="sf-lbl">Arguments en attente</span></div>
    <div class="suggestion-focus-stat"><span class="sf-val">${st.importance_ratings_count ?? "—"}</span><span class="sf-lbl">Notes d’importance</span></div>
    <div class="suggestion-focus-stat"><span class="sf-val">${st.importance_average_level != null ? st.importance_average_level : "—"}</span><span class="sf-lbl">Moy. importance (1–4)</span></div>
  </div>
  <div class="suggestion-focus-ai">
    <p><strong>Score importance (agrégé) :</strong> ${s.importance_score != null ? Number(s.importance_score).toFixed(1) : "—"} / 100</p>
    <p><strong>Évaluation IA :</strong> impact ${imp != null ? imp + " %" : "—"} · faisabilité ${fais != null ? fais + " %" : "—"} · coût ${cout != null ? cout + " %" : "—"}</p>
  </div>
  <div class="suggestion-focus-actions">
    <a class="btn btn-sm btn-secondary" href="/api/admin/suggestions/${sid}/pdf" target="_blank" rel="noopener" download>Télécharger PDF</a>
    <button type="button" class="btn btn-sm btn-secondary suggestion-focus-history-btn" data-sid="${sid}">Historique</button>
  </div>
</div>`;
        panel.querySelector(".suggestion-focus-close")?.addEventListener("click", () => {
            panel.classList.add("hidden");
            panel.innerHTML = "";
            try {
                history.replaceState(null, "", location.pathname + location.search);
            } catch (e) { /* ignore */ }
        });
        panel.querySelector(".suggestion-focus-history-btn")?.addEventListener("click", () => {
            openHistoryModal("suggestion", sid);
        });
    } catch (e) {
        console.error(e);
        panel.innerHTML = `<p class="empty-msg">Impossible de charger la suggestion.</p>`;
    }
}

async function loadSuggestionsHub() {
    const hub = document.getElementById("admin-suggestions-hub");
    if (!hub) return;
    try {
        const settings = await API.get("/api/admin/settings");
        const tp = document.getElementById("hub-toggle-official-proposal");
        const ti = document.getElementById("hub-toggle-cvl-info");
        const tm = document.getElementById("hub-toggle-music-poll");
        const trb = document.getElementById("hub-toggle-ringtone-banner");
        if (tp) tp.checked = settings.feature_official_proposal_enabled !== "false";
        if (ti) ti.checked = settings.feature_cvl_official_info_enabled !== "false";
        if (tm) tm.checked = settings.feature_music_poll_enabled !== "false";
        if (trb) trb.checked = settings.feature_ringtone_banner_enabled === "true";

        const [plist, infos, mPolls] = await Promise.all([
            API.get("/api/admin/official-proposals"),
            API.get("/api/admin/cvl-official-info"),
            API.get("/api/admin/music-polls"),
        ]);
        const proposals = Array.isArray(plist) ? plist : [];
        const activeP = proposals.find((x) => x.active);
        const sp = document.getElementById("hub-summary-official-proposal");
        if (sp) {
            sp.textContent = activeP
                ? `Affichée : #${activeP.id} (${activeP.status || ""}) — ${activeP.vote_for ?? 0} pour / ${activeP.vote_against ?? 0} contre`
                : proposals.length
                  ? `Aucune active (${proposals.length} au catalogue — utiliser Publier dans l’éditeur)`
                  : "Aucune proposition créée";
        }

        const infolist = Array.isArray(infos) ? infos : [];
        const activeCount = infolist.filter((x) => x.active !== false).length;
        const si = document.getElementById("hub-summary-cvl-info");
        if (si) {
            si.textContent =
                infolist.length === 0
                    ? "Aucune information"
                    : `${activeCount} active(s) / ${infolist.length} au total`;
        }

        const polls = (mPolls && mPolls.polls) || [];
        const spotifyOk = mPolls && mPolls.spotify_configured;
        const activePoll = polls.find((p) => p.is_active);
        const sm = document.getElementById("hub-summary-music-poll");
        if (sm) {
            sm.textContent = !spotifyOk
                ? "Spotify non configuré — voir la carte API dans « Sondage musique »"
                : activePoll
                  ? `Sondage actif : « ${activePoll.title} » (${activePoll.track_count} morceaux)`
                  : polls.length
                    ? `${polls.length} sondage(s) enregistré(s), aucun actif`
                    : "Aucun sondage créé";
        }
    } catch (e) {
        console.error(e);
    }
}

function setupSuggestionsHub() {
    document.getElementById("hub-toggle-official-proposal")?.addEventListener("change", async (e) => {
        await API.put("/api/admin/settings", { feature_official_proposal_enabled: e.target.checked ? "true" : "false" });
    });
    document.getElementById("hub-toggle-cvl-info")?.addEventListener("change", async (e) => {
        await API.put("/api/admin/settings", { feature_cvl_official_info_enabled: e.target.checked ? "true" : "false" });
    });
    document.getElementById("hub-toggle-music-poll")?.addEventListener("change", async (e) => {
        await API.put("/api/admin/settings", { feature_music_poll_enabled: e.target.checked ? "true" : "false" });
    });
    document.getElementById("hub-toggle-ringtone-banner")?.addEventListener("change", async (e) => {
        await setRingtoneBannerEnabledFromUi(e.target.checked);
    });
    document.getElementById("hub-btn-open-cvl-proposal")?.addEventListener("click", () => navigateToAdminSection("cvl-proposal"));
    document.getElementById("hub-btn-open-cvl-info")?.addEventListener("click", () => navigateToAdminSection("cvl-official-info"));
    document.getElementById("hub-btn-open-music-poll")?.addEventListener("click", () => navigateToAdminSection("music-poll"));
    document.getElementById("hub-btn-close-cvl-proposal")?.addEventListener("click", async () => {
        if (!confirm("Retirer la proposition officielle de l’affichage côté élèves ?")) return;
        await API.post("/api/admin/official-proposal/close", {});
        await loadSuggestionsHub();
    });
    document.getElementById("hub-btn-publish-cvl-proposal")?.addEventListener("click", async () => {
        const { data, status } = await API.post("/api/admin/official-proposal/publish", {});
        if (status >= 400) {
            alert((data && data.error) || "Erreur");
            return;
        }
        await loadSuggestionsHub();
    });
}
function renderAdminSuggestions() {
    const cf = document.getElementById("admin-filter-category").value;
    const sf = document.getElementById("admin-filter-status").value;
    const q = document.getElementById("admin-search").value.toLowerCase();
    let f = allSuggestions;
    if (cf) f = f.filter((s) => s.category === cf);
    if (sf) f = f.filter((s) => s.status === sf);
    if (q) f = f.filter((s) => s.title.toLowerCase().includes(q) || s.original_text.toLowerCase().includes(q));
    const c = document.getElementById("admin-suggestions-list");
    if (!f.length) { c.innerHTML = '<p class="empty-msg">Aucune suggestion trouvée</p>'; return; }
    c.innerHTML = f.map((s) => {
        const opts = STATUSES.map((st) => `<option value="${st}"${st === s.status ? " selected" : ""}>${st}</option>`).join("");
        const isPending = s.status === "En attente";
        const imp = s.ai_proportion != null ? Math.round((s.ai_proportion || 0) * 100) : null;
        const fais = s.ai_feasibility != null ? Math.round((s.ai_feasibility || 0.5) * 100) : null;
        const cout = s.ai_cost != null ? Math.round((s.ai_cost || 0.5) * 100) : null;
        const aiDebat = imp != null ? (s.ai_needs_debate ? "Oui" : "Non") : null;
        const aiEvalHtml = imp != null ? `
            <div class="admin-ai-eval">
                <span title="Impact">Impact ${imp}%</span>
                <span title="Faisabilité">Fais. ${fais}%</span>
                <span title="Coût">Coût ${cout}%</span>
                <span class="admin-ai-debat">Débat IA: ${aiDebat}</span>
            </div>
        ` : "";
        const debatToggle = !isPending ? `
            <label class="admin-debat-toggle">
                <input type="checkbox" class="admin-debat-cb" data-id="${s.id}" ${s.needs_debate ? "checked" : ""}>
                Forcer débat
            </label>
        ` : "";
        return `<div class="admin-suggestion-card${isPending ? " admin-card-pending" : ""}">
            <div class="admin-card-top"><span class="admin-card-title">${esc(s.title)}</span><button class="btn-icon edit-title-btn" data-id="${s.id}" title="Modifier le titre">✎</button><span class="badge badge-votes">${s.vote_count} soutien${s.vote_count !== 1 ? "s" : ""}</span></div>
            ${s.subtitle ? `<div class="admin-card-subtitle">${esc(s.subtitle)}<button class="btn-icon edit-subtitle-btn" data-id="${s.id}" title="Modifier le sous-titre">✎</button></div>` : ""}
            <div class="admin-card-original">"${esc(s.original_text)}"</div>
            ${aiEvalHtml}
            <div class="admin-card-actions">
                <div class="admin-card-meta"><span class="badge badge-category">${s.category}</span><span class="badge badge-status" data-status="${s.status}">${s.status}</span>${isPending ? '<span class="badge badge-pending">En attente</span>' : ""}${debatToggle}</div>
                ${isPending ? `<button class="btn btn-sm btn-primary process-single-btn" data-id="${s.id}">Traiter</button>` : ""}
                <select class="status-select" data-id="${s.id}">${opts}</select>
                <select class="location-select" data-id="${s.id}"><option value="">-- Lieu --</option></select>
                <button class="btn btn-sm btn-ghost history-btn" data-id="${s.id}" data-type="suggestion" title="Historique">Historique</button>
                <a class="btn btn-sm btn-ghost pdf-btn" href="/api/admin/suggestions/${s.id}/pdf" data-id="${s.id}" download target="_blank" title="Télécharger PDF">PDF</a>
                <button class="btn btn-sm btn-ghost add-vote-btn" data-id="${s.id}" title="Ajouter des soutiens (dev)">+1 vote</button>
                <button class="btn btn-sm btn-ghost recalib-btn" data-id="${s.id}" title="Recalibrer">Calibrer</button>
                <button class="delete-btn" data-id="${s.id}">Supprimer</button>
            </div></div>`;
    }).join("");
    c.querySelectorAll(".process-single-btn").forEach((btn) => {
        btn.addEventListener("click", async () => {
            btn.disabled = true; btn.textContent = "...";
            await API.post(`/api/admin/suggestions/${btn.dataset.id}/process`);
            loadAdminSuggestions();
        });
    });
    c.querySelectorAll(".add-vote-btn").forEach((btn) => {
        btn.addEventListener("click", async () => {
            const count = parseInt(prompt("Nombre de soutiens a ajouter :", "1"));
            if (!count || count < 1) return;
            btn.disabled = true; btn.textContent = "...";
            await API.post(`/api/admin/suggestions/${btn.dataset.id}/add-vote`, { count });
            loadAdminSuggestions();
        });
    });
    c.querySelectorAll(".status-select").forEach((sel) => { sel.addEventListener("change", async () => { await API.put(`/api/admin/suggestions/${sel.dataset.id}/status`, { status: sel.value }); loadAdminSuggestions(); }); });
    c.querySelectorAll(".location-select").forEach((sel) => { sel.addEventListener("change", async () => { await API.put(`/api/admin/suggestions/${sel.dataset.id}/location`, { location_id: sel.value ? parseInt(sel.value) : null }); }); });
    c.querySelectorAll(".delete-btn").forEach((btn) => { btn.addEventListener("click", async () => { if (confirm("Supprimer ?")) { await API.delete(`/api/admin/suggestions/${btn.dataset.id}`); loadAdminSuggestions(); } }); });
    c.querySelectorAll(".edit-title-btn").forEach((btn) => {
        btn.addEventListener("click", async () => {
            const s = allSuggestions.find((x) => x.id === parseInt(btn.dataset.id));
            const currentTitle = s ? s.title : "";
            const newTitle = prompt("Nouveau titre :", currentTitle);
            if (newTitle == null || !newTitle.trim()) return;
            try {
                await API.put(`/api/admin/suggestions/${btn.dataset.id}/title`, { title: newTitle.trim() });
                loadAdminSuggestions();
            } catch (e) { alert("Erreur"); }
        });
    });
    c.querySelectorAll(".edit-subtitle-btn").forEach((btn) => {
        btn.addEventListener("click", async () => {
            const s = allSuggestions.find((x) => x.id === parseInt(btn.dataset.id));
            const current = s ? (s.subtitle || "") : "";
            const newVal = prompt("Sous-titre (laisser vide pour supprimer) :", current);
            if (newVal == null) return;
            try {
                await API.put(`/api/admin/suggestions/${btn.dataset.id}/subtitle`, { subtitle: newVal.trim() });
                loadAdminSuggestions();
            } catch (e) { alert("Erreur"); }
        });
    });
    c.querySelectorAll(".recalib-btn").forEach((btn) => {
        btn.addEventListener("click", async () => {
            const s = allSuggestions.find((x) => x.id === parseInt(btn.dataset.id));
            if (s && confirm(`Ajouter "${s.title}" à la calibration ?`)) {
                await API.post(`/api/admin/suggestions/${s.id}/recalibrate`, { title: s.title, keywords: s.keywords, category: s.category, location: s.location_name || "" });
                alert("Ajouté à la calibration !");
            }
        });
    });
    c.querySelectorAll(".history-btn").forEach((btn) => {
        btn.addEventListener("click", () => openHistoryModal("suggestion", parseInt(btn.dataset.id)));
    });
    c.querySelectorAll(".admin-debat-cb").forEach((cb) => {
        cb.addEventListener("change", async () => {
            const id = parseInt(cb.dataset.id);
            const needsDebate = cb.checked;
            try {
                await API.put(`/api/admin/suggestions/${id}/needs-debate`, { needs_debate: needsDebate });
                const s = allSuggestions.find((x) => x.id === id);
                if (s) s.needs_debate = needsDebate;
            } catch (e) { alert("Erreur"); }
        });
    });
    loadLocations();
}

// ==================== Locations ====================

async function loadLocations() {
    try {
        const locs = await API.get("/api/admin/locations");
        renderLocations(locs);
        document.querySelectorAll(".location-select").forEach((sel) => {
            const locId = sel.dataset.locationId || "";
            sel.innerHTML = '<option value="">— Lieu —</option>' + locs.map((l) => `<option value="${l.id}">${esc(l.name)}</option>`).join("");
            sel.value = locId ? String(locId) : "";
        });
    } catch (e) { console.error(e); }
}
function renderLocations(locs) {
    const c = document.getElementById("locations-list");
    if (!locs.length) { c.innerHTML = '<p class="empty-msg">Aucun lieu configure</p>'; return; }
    c.innerHTML = locs.map((l) => {
        const placements = (l.placements || []).map((p) => {
            const pid = typeof p === "object" ? p.id : null;
            const pname = typeof p === "object" ? p.name : p;
            if (pid) return `<span class="placement-tag"><span>${esc(pname)}</span><button class="placement-remove" data-pid="${pid}">x</button></span>`;
            return `<span class="placement-tag">${esc(pname)}</span>`;
        }).join("");
        return `<div class="location-item">
            <div class="location-info">
                <div class="location-header-row">
                    <span class="location-name">${esc(l.name)}</span>
                    <span class="location-count">${l.suggestion_count} demande${l.suggestion_count !== 1 ? "s" : ""}</span>
                </div>
                <div class="location-placements">
                    ${placements}
                    <div class="placement-add">
                        <input type="text" class="placement-input" placeholder="+ Emplacement (ex: salle de dance)" data-lid="${l.id}">
                        <button class="btn btn-sm btn-ghost placement-add-btn" data-lid="${l.id}">Ajouter</button>
                    </div>
                </div>
            </div>
            <button class="location-delete-btn" data-id="${l.id}">Suppr.</button>
        </div>`;
    }).join("");
    c.querySelectorAll(".location-delete-btn").forEach((b) => { b.addEventListener("click", async () => { if (confirm("Supprimer ?")) { await API.delete(`/api/admin/locations/${b.dataset.id}`); loadLocations(); } }); });
    c.querySelectorAll(".placement-add-btn").forEach((btn) => {
        btn.addEventListener("click", async () => {
            const lid = parseInt(btn.dataset.lid);
            const inp = btn.closest(".placement-add").querySelector(".placement-input");
            const name = inp.value.trim();
            if (!name) return;
            const { status } = await API.post(`/api/admin/locations/${lid}/placements`, { name });
            if (status === 201) { inp.value = ""; loadLocations(); }
        });
    });
    c.querySelectorAll(".placement-input").forEach((inp) => {
        inp.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                const btn = inp.closest(".placement-add").querySelector(".placement-add-btn");
                btn.click();
            }
        });
    });
    c.querySelectorAll(".placement-remove").forEach((b) => {
        b.addEventListener("click", async () => {
            if (confirm("Supprimer cet emplacement ?")) { await API.delete(`/api/admin/placements/${b.dataset.pid}`); loadLocations(); }
        });
    });
}
async function addLocation() {
    const inp = document.getElementById("location-name-input");
    if (!inp.value.trim()) return;
    const { status } = await API.post("/api/admin/locations", { name: inp.value.trim() });
    if (status === 201) { inp.value = ""; loadLocations(); }
}

// ==================== Calibration ====================

function setupCalibration() {
    document.getElementById("calib-file-input").addEventListener("change", handleCalibImport);
    document.getElementById("calib-process-all").addEventListener("click", processAllCalib);
    document.getElementById("calib-validate-all").addEventListener("click", validateAllCalib);
    document.getElementById("calib-export").addEventListener("click", exportCalib);
    document.getElementById("calib-ai-suggest").addEventListener("click", aiSuggest);
    document.getElementById("calib-generate-json")?.addEventListener("click", generateJsonCalib);
    document.getElementById("calib-batch-filter").addEventListener("change", (e) => {
        calibBatchFilter = e.target.value;
        loadCalibration();
    });
    document.querySelectorAll("[data-calib-status]").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.querySelectorAll("[data-calib-status]").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            calibFilter = btn.dataset.calibStatus;
            renderCalibList();
        });
    });

    // Sub-tabs
    document.querySelectorAll(".calib-tab").forEach((tab) => {
        tab.addEventListener("click", () => {
            document.querySelectorAll(".calib-tab").forEach((t) => t.classList.remove("active"));
            document.querySelectorAll(".calib-tab-content").forEach((c) => c.classList.remove("active"));
            tab.classList.add("active");
            document.getElementById(`tab-${tab.dataset.tab}`).classList.add("active");
            if (tab.dataset.tab === "calib-debat") loadCalibrationDebat();
            if (tab.dataset.tab === "calib-details") loadCalibrationDetails();
            if (tab.dataset.tab === "calib-rapport") loadCalibrationRapport();
        });
    });

    setupCalibrationDetails();
    setupCalibrationRapport();

    // Context
    document.getElementById("context-save-btn").addEventListener("click", saveContext);

    // Prompt
    document.getElementById("prompt-generate-btn").addEventListener("click", generatePrompt);
    document.getElementById("prompt-copy-btn").addEventListener("click", copyPrompt);
}

async function loadCalibration() {
    try {
        let url = "/api/admin/calibration";
        const params = [];
        if (calibBatchFilter) params.push(`batch=${calibBatchFilter}`);
        if (params.length) url += "?" + params.join("&");

        const [examples, stats, batches] = await Promise.all([
            API.get(url),
            API.get("/api/admin/calibration/stats"),
            API.get("/api/admin/calibration/batches"),
        ]);
        allCalibExamples = examples;
        document.getElementById("calib-total").textContent = stats.total;
        document.getElementById("calib-pending").textContent = stats.pending;
        document.getElementById("calib-processed").textContent = stats.processed;
        document.getElementById("calib-validated").textContent = stats.validated;

        // Batch dropdown
        const sel = document.getElementById("calib-batch-filter");
        const curVal = sel.value;
        sel.innerHTML = '<option value="">Tous les imports</option>';
        batches.forEach((b) => {
            const d = b.created_at ? new Date(b.created_at).toLocaleDateString("fr-FR") : "";
            const o = document.createElement("option");
            o.value = b.batch_id;
            o.textContent = `${d} (${b.count} msgs) — ${b.batch_id}`;
            sel.appendChild(o);
        });
        sel.value = curVal;

        renderCalibList();

        // Load context + prompts
        const [ctx, prompts] = await Promise.all([
            API.get("/api/admin/context"),
            API.get("/api/admin/calibration/prompts").catch(() => ({})),
        ]);
        document.getElementById("context-textarea").value = ctx.context || "";
        renderContextPrompts(prompts);
    } catch (e) { console.error(e); }
}

function renderContextPrompts(prompts) {
    const c = document.getElementById("context-prompts-list");
    if (!c) return;
    if (!prompts || !Object.keys(prompts).length) {
        c.innerHTML = '<p class="empty-msg">Aucun prompt chargé</p>';
        return;
    }
    c.innerHTML = Object.entries(prompts).map(([key, p]) => `
        <div class="context-prompt-block">
            <div class="context-prompt-header">
                <strong>${esc(p.name || key)}</strong>
                <button type="button" class="btn btn-sm btn-ghost context-prompt-copy" data-prompt="${esc(key)}">Copier</button>
            </div>
            <pre class="context-prompt-text">${esc(p.prompt || "")}</pre>
        </div>
    `).join("");
    c.querySelectorAll(".context-prompt-copy").forEach((btn) => {
        btn.addEventListener("click", () => {
            const block = btn.closest(".context-prompt-block");
            const pre = block?.querySelector(".context-prompt-text");
            if (pre) {
                navigator.clipboard.writeText(pre.textContent);
                btn.textContent = "Copié !";
                setTimeout(() => { btn.textContent = "Copier"; }, 1500);
            }
        });
    });
}

function renderCalibList() {
    let filtered = allCalibExamples;
    if (calibFilter) filtered = filtered.filter((e) => e.status === calibFilter);
    const c = document.getElementById("calib-list");
    if (!filtered.length) { c.innerHTML = '<p class="empty-msg">Aucun exemple</p>'; return; }

    c.innerHTML = filtered.map((e) => {
        const sc = CALIB_STATUS_COLORS[e.status] || "#94a3b8";
        const catOpts = CATEGORIES.map((cat) => `<option value="${cat}"${cat === e.category ? " selected" : ""}>${cat}</option>`).join("");

        const decisionVal = e.status === "validated" ? "accepted" : e.status === "rejected" ? "rejected" : "";

        return `<div class="calib-card" data-id="${e.id}">
            <div class="calib-card-left">
                <div class="calib-original">"${esc(e.original_text)}"</div>
                <div class="calib-inline-edit">
                    <input type="text" class="calib-title-input" value="${esc(e.title || "")}" placeholder="Titre reformulé..." data-id="${e.id}">
                    <div class="calib-inline-row">
                        <select class="calib-cat-select" data-id="${e.id}">${catOpts}</select>
                        <input type="text" class="calib-kw-input" value="${esc((e.keywords || []).join(", "))}" placeholder="Mots-clés..." data-id="${e.id}">
                        <input type="text" class="calib-loc-input" value="${esc(e.location || "")}" placeholder="Lieu..." data-id="${e.id}">
                    </div>
                    <div class="calib-forbidden-row">
                        <span class="forbidden-label">Mots a retirer :</span>
                        <input type="text" class="calib-fw-input" value="${esc((e.forbidden_words || []).join(", "))}" placeholder="ptn, merde, fdp..." data-id="${e.id}">
                    </div>
                </div>
            </div>
                <div class="calib-card-right">
                <div class="calib-decision">
                    <label class="calib-decision-label">Decision :</label>
                    <select class="calib-decision-select" data-id="${e.id}">
                        <option value="" ${decisionVal === "" ? "selected" : ""}>-- En attente --</option>
                        <option value="accepted" ${decisionVal === "accepted" ? "selected" : ""}>Acceptee</option>
                        <option value="rejected" ${decisionVal === "rejected" ? "selected" : ""}>Refusee</option>
                    </select>
                </div>
                <div class="calib-card-btns">
                    ${e.status === "pending" ? `<button class="btn btn-sm btn-primary calib-process-btn" data-id="${e.id}" title="Analyser avec IA">Analyser</button>` : ""}
                    <button class="btn btn-sm btn-ghost calib-delete-btn" data-id="${e.id}" title="Supprimer">Suppr.</button>
                </div>
            </div>
        </div>`;
    }).join("");

    // Inline save on change for all fields
    c.querySelectorAll(".calib-title-input, .calib-kw-input, .calib-loc-input, .calib-fw-input").forEach((inp) => {
        inp.addEventListener("change", () => inlineSaveCalib(parseInt(inp.dataset.id)));
    });
    c.querySelectorAll(".calib-cat-select").forEach((sel) => {
        sel.addEventListener("change", () => inlineSaveCalib(parseInt(sel.dataset.id)));
    });

    // Decision dropdown
    c.querySelectorAll(".calib-decision-select").forEach((sel) => {
        sel.addEventListener("change", async () => {
            const id = parseInt(sel.dataset.id);
            const card = sel.closest(".calib-card");
            const fw = card.querySelector(".calib-fw-input")?.value.split(",").map((w) => w.trim()).filter(Boolean) || [];

            await inlineSaveCalib(id);

            if (sel.value === "accepted") {
                await API.post(`/api/admin/calibration/${id}/validate`, { forbidden_words: fw });
            } else if (sel.value === "rejected") {
                await API.post(`/api/admin/calibration/${id}/reject`, { forbidden_words: fw });
            }
            loadCalibration();
        });
    });

    // Process button
    c.querySelectorAll(".calib-process-btn").forEach((btn) => {
        btn.addEventListener("click", async () => { btn.disabled = true; btn.textContent = "..."; await API.post(`/api/admin/calibration/${btn.dataset.id}/process`); loadCalibration(); });
    });

    // Delete button
    c.querySelectorAll(".calib-delete-btn").forEach((btn) => {
        btn.addEventListener("click", async () => { if (confirm("Supprimer ?")) { await API.delete(`/api/admin/calibration/${btn.dataset.id}`); loadCalibration(); } });
    });
}

async function inlineSaveCalib(id) {
    const card = document.querySelector(`.calib-card[data-id="${id}"]`);
    if (!card) return;
    const title = card.querySelector(".calib-title-input")?.value || "";
    const kw = card.querySelector(".calib-kw-input")?.value.split(",").map((k) => k.trim()).filter(Boolean) || [];
    const cat = card.querySelector(".calib-cat-select")?.value || "";
    const loc = card.querySelector(".calib-loc-input")?.value || "";
    await API.put(`/api/admin/calibration/${id}`, { title, keywords: kw, category: cat, location: loc });
}

// Calibration débat
async function loadCalibrationDebat() {
    try {
        const items = await API.get("/api/admin/calibration-debat");
        const c = document.getElementById("calib-debat-list");
        if (!items.length) {
            c.innerHTML = '<p class="empty-msg">Aucun exemple. Ajoutez des propositions pour guider l\'IA.</p>';
            return;
        }
        c.innerHTML = items.map((e) => `
            <div class="calib-debat-item" data-id="${e.id}">
                <span class="calib-debat-prop">"${esc(e.proposition)}"</span>
                <span class="calib-debat-badge ${e.needs_debate ? 'debate-yes' : 'debate-no'}">${e.needs_debate ? "Débat" : "Soutiens"}</span>
                <button class="btn btn-sm btn-ghost calib-debat-del" data-id="${e.id}">Suppr.</button>
            </div>
        `).join("");
        c.querySelectorAll(".calib-debat-del").forEach((btn) => {
            btn.addEventListener("click", async () => {
                if (confirm("Supprimer cet exemple ?")) {
                    await API.delete(`/api/admin/calibration-debat/${btn.dataset.id}`);
                    loadCalibrationDebat();
                }
            });
        });
    } catch (e) { console.error(e); }
}

document.getElementById("calib-debat-add-btn")?.addEventListener("click", async () => {
    const prop = document.getElementById("calib-debat-proposition")?.value?.trim();
    if (!prop) return;
    const needs = document.getElementById("calib-debat-needs")?.checked ?? false;
    await API.post("/api/admin/calibration-debat", { proposition: prop, needs_debate: needs });
    document.getElementById("calib-debat-proposition").value = "";
    loadCalibrationDebat();
});

// Calibration débat : prompt + import
document.getElementById("calib-debat-prompt-generate")?.addEventListener("click", async () => {
    const count = document.getElementById("calib-debat-prompt-count")?.value || 40;
    const { prompt } = await API.get(`/api/admin/calibration-debat/prompt?count=${count}`);
    const zone = document.getElementById("calib-debat-prompt-output");
    const ta = document.getElementById("calib-debat-prompt-text");
    if (ta) ta.value = prompt;
    if (zone) zone.classList.remove("hidden");
});
document.getElementById("calib-debat-prompt-copy")?.addEventListener("click", async () => {
    const ta = document.getElementById("calib-debat-prompt-text");
    if (!ta?.value) return;
    await navigator.clipboard.writeText(ta.value);
    const s = document.getElementById("calib-debat-prompt-copy-status");
    if (s) { s.textContent = "Copié !"; setTimeout(() => (s.textContent = ""), 2000); }
});
document.getElementById("calib-debat-file-input")?.addEventListener("change", async (e) => {
    const input = e.target;
    if (!input.files?.length) return;
    const file = input.files[0];
    document.getElementById("calib-debat-file-name").textContent = file.name;
    const fd = new FormData();
    fd.append("file", file);
    const { data, status } = await API.postFile("/api/admin/calibration-debat/import", fd);
    if (status === 200) {
        alert(`${data.imported} exemples importés dans la calibration débat !`);
        loadCalibrationDebat();
    } else {
        alert(data.error || "Erreur d'import");
    }
    input.value = "";
});

// Calibration détails
function setupCalibrationDetails() {
    const section = document.getElementById("section-calibration");
    if (!section) return;
    section.addEventListener("click", async (e) => {
        const genBtn = e.target.closest("#calib-details-prompt-generate");
        const copyBtn = e.target.closest("#calib-details-prompt-copy");
        const addBtn = e.target.closest("#calib-details-add-btn");
        if (genBtn) {
            const count = document.getElementById("calib-details-prompt-count")?.value || 30;
            try {
                const { prompt } = await API.get(`/api/admin/calibration-details/prompt?count=${count}`);
                const zone = document.getElementById("calib-details-prompt-output");
                const ta = document.getElementById("calib-details-prompt-text");
                if (ta) ta.value = prompt;
                if (zone) zone.classList.remove("hidden");
            } catch (err) {
                alert("Erreur : " + (err.message || "impossible de charger le prompt"));
            }
        }
        if (copyBtn) {
            const ta = document.getElementById("calib-details-prompt-text");
            if (!ta?.value) return;
            await navigator.clipboard.writeText(ta.value);
            const s = document.getElementById("calib-details-prompt-copy-status");
            if (s) { s.textContent = "Copié !"; setTimeout(() => (s.textContent = ""), 2000); }
        }
        if (addBtn) {
            const suggestion = document.getElementById("calib-details-suggestion")?.value?.trim();
            if (!suggestion) return;
            const hintRaw = document.getElementById("calib-details-hint")?.value?.trim();
            const hint = !hintRaw || hintRaw.toLowerCase() === "non" ? null : hintRaw;
            try {
                await API.post("/api/admin/calibration-details", { suggestion_text: suggestion, hint });
                document.getElementById("calib-details-suggestion").value = "";
                document.getElementById("calib-details-hint").value = "";
                loadCalibrationDetails();
            } catch (err) {
                alert("Erreur : " + (err.message || "échec"));
            }
        }
    });
    document.getElementById("calib-details-file-input")?.addEventListener("change", async (e) => {
        const input = e.target;
        if (!input.files?.length) return;
        const file = input.files[0];
        document.getElementById("calib-details-file-name").textContent = file.name;
        const fd = new FormData();
        fd.append("file", file);
        try {
            const { data, status } = await API.postFile("/api/admin/calibration-details/import", fd);
            if (status === 200) {
                alert(`${data.imported} exemples importés dans la calibration détails !`);
                loadCalibrationDetails();
            } else {
                alert(data.error || "Erreur d'import");
            }
        } catch (err) {
            alert("Erreur : " + (err.message || "échec"));
        }
        input.value = "";
    });
}

async function loadCalibrationDetails() {
    try {
        const items = await API.get("/api/admin/calibration-details");
        const c = document.getElementById("calib-details-list");
        if (!c) return;
        if (!items.length) {
            c.innerHTML = '<p class="empty-msg">Aucun exemple. Ajoutez des suggestions ou importez un JSON.</p>';
            return;
        }
        const groups = {};
        items.forEach((e) => {
            const key = e.suggestion_base || "__sans_groupe__";
            if (!groups[key]) groups[key] = [];
            groups[key].push(e);
        });
        const sortedKeys = Object.keys(groups).sort((a, b) => {
            if (a === "__sans_groupe__") return 1;
            if (b === "__sans_groupe__") return -1;
            return 0;
        });
        c.innerHTML = sortedKeys.map((key) => {
            const exemples = groups[key];
            const baseLabel = key === "__sans_groupe__" ? "Exemples isolés" : esc(key);
            const itemsHtml = exemples.map((e) => `
                <div class="calib-details-item" data-id="${e.id}">
                    <span class="calib-details-suggestion">"${esc(e.suggestion_text)}"</span>
                    <span class="calib-details-hint ${e.hint ? '' : 'none'}">${e.hint ? esc(e.hint) : "non"}</span>
                    <button class="btn btn-sm btn-ghost calib-details-del" data-id="${e.id}">Suppr.</button>
                </div>
            `).join("");
            return `
                <div class="calib-details-group">
                    <div class="calib-details-group-header">${baseLabel}</div>
                    <div class="calib-details-group-scroll">
                        ${itemsHtml}
                    </div>
                </div>
            `;
        }).join("");
        c.querySelectorAll(".calib-details-del").forEach((btn) => {
            btn.addEventListener("click", async () => {
                if (confirm("Supprimer cet exemple ?")) {
                    await API.delete(`/api/admin/calibration-details/${btn.dataset.id}`);
                    loadCalibrationDetails();
                }
            });
        });
    } catch (e) { console.error(e); }
}

function setupCalibrationRapport() {
    const section = document.getElementById("section-calibration");
    if (!section) return;
    section.addEventListener("click", async (e) => {
        const genBtn = e.target.closest("#calib-rapport-prompt-generate");
        const copyBtn = e.target.closest("#calib-rapport-prompt-copy");
        const addBtn = e.target.closest("#calib-rapport-add-btn");
        if (genBtn) {
            const count = document.getElementById("calib-rapport-prompt-count")?.value || 25;
            try {
                const { prompt } = await API.get(`/api/admin/calibration-rapport/prompt?count=${count}`);
                const zone = document.getElementById("calib-rapport-prompt-output");
                const ta = document.getElementById("calib-rapport-prompt-text");
                if (ta) ta.value = prompt;
                if (zone) zone.classList.remove("hidden");
            } catch (err) {
                alert("Erreur : " + (err.message || "impossible de charger le prompt"));
            }
        }
        if (copyBtn) {
            const ta = document.getElementById("calib-rapport-prompt-text");
            if (!ta?.value) return;
            await navigator.clipboard.writeText(ta.value);
            const s = document.getElementById("calib-rapport-prompt-copy-status");
            if (s) { s.textContent = "Copié !"; setTimeout(() => (s.textContent = ""), 2000); }
        }
        if (addBtn) {
            const existing = document.getElementById("calib-rapport-existing")?.value?.trim();
            const newText = document.getElementById("calib-rapport-new")?.value?.trim();
            if (!existing || !newText) return;
            const hasRapport = document.getElementById("calib-rapport-has")?.checked ?? false;
            const isPrecision = document.getElementById("calib-rapport-precision")?.checked ?? false;
            try {
                await API.post("/api/admin/calibration-rapport", {
                    existing_text: existing, new_text: newText,
                    has_rapport: hasRapport, is_precision: isPrecision,
                });
                document.getElementById("calib-rapport-existing").value = "";
                document.getElementById("calib-rapport-new").value = "";
                loadCalibrationRapport();
            } catch (err) {
                alert("Erreur : " + (err.message || "échec"));
            }
        }
    });
    document.getElementById("calib-rapport-file-input")?.addEventListener("change", async (e) => {
        const input = e.target;
        if (!input.files?.length) return;
        const file = input.files[0];
        document.getElementById("calib-rapport-file-name").textContent = file.name;
        const fd = new FormData();
        fd.append("file", file);
        try {
            const { data, status } = await API.postFile("/api/admin/calibration-rapport/import", fd);
            if (status === 200) {
                alert(`${data.imported} exemples importés !`);
                loadCalibrationRapport();
            } else {
                alert(data.error || "Erreur d'import");
            }
        } catch (err) {
            alert("Erreur : " + (err.message || "échec"));
        }
        input.value = "";
    });
}

async function loadCalibrationRapport() {
    try {
        const items = await API.get("/api/admin/calibration-rapport");
        const c = document.getElementById("calib-rapport-list");
        if (!c) return;
        if (!items.length) {
            c.innerHTML = '<p class="empty-msg">Aucun exemple. Ajoutez des paires (existante, nouvelle) pour guider l\'IA.</p>';
            return;
        }
        c.innerHTML = items.map((e) => `
            <div class="calib-rapport-item" data-id="${e.id}">
                <span class="calib-rapport-existing">"${esc(e.existing_text)}"</span>
                <span class="calib-rapport-arrow">+</span>
                <span class="calib-rapport-new">"${esc(e.new_text)}"</span>
                <span class="calib-rapport-badge rapport-${e.has_rapport ? 'yes' : 'no'}">Rapport ${e.has_rapport ? "oui" : "non"}</span>
                <span class="calib-rapport-badge precision-${e.is_precision ? 'yes' : 'no'}">Précision ${e.is_precision ? "oui" : "non"}</span>
                <button class="btn btn-sm btn-ghost calib-rapport-del" data-id="${e.id}">Suppr.</button>
            </div>
        `).join("");
        c.querySelectorAll(".calib-rapport-del").forEach((btn) => {
            btn.addEventListener("click", async () => {
                if (confirm("Supprimer ?")) {
                    await API.delete(`/api/admin/calibration-rapport/${btn.dataset.id}`);
                    loadCalibrationRapport();
                }
            });
        });
    } catch (err) { console.error(err); }
}

async function handleCalibImport() {
    const input = document.getElementById("calib-file-input");
    if (!input.files.length) return;
    const file = input.files[0];
    document.getElementById("calib-file-name").textContent = file.name;
    const fd = new FormData();
    fd.append("file", file);
    const { data, status } = await API.postFile("/api/admin/calibration/import", fd);
    if (status === 200) {
        let msg = `${data.imported} messages importés !`;
        if (data.pre_trained > 0) msg += `\n${data.pre_trained} déjà pré-entraînés (importés comme validés).`;
        alert(msg);
        // Auto-filter to new batch
        calibBatchFilter = data.batch_id || "";
        document.getElementById("calib-batch-filter").value = calibBatchFilter;
        loadCalibration();
    } else { alert(data.error || "Erreur d'import"); }
    input.value = "";
}

async function processAllCalib() {
    const btn = document.getElementById("calib-process-all");
    btn.disabled = true; btn.textContent = "Analyse en cours...";
    const { data } = await API.post("/api/admin/calibration/process-all", { batch_id: calibBatchFilter });
    alert(`${data.processed} exemples traites !`);
    btn.disabled = false; btn.textContent = "Analyser avec IA";
    loadCalibration();
}

async function validateAllCalib() {
    const { data } = await API.post("/api/admin/calibration/validate-all-processed", { batch_id: calibBatchFilter });
    alert(`${data.validated} exemples valides !`);
    loadCalibration();
}

async function exportCalib() {
    const data = await API.get("/api/admin/calibration/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "calibration_data.json";
    a.click();
}

async function generateJsonCalib() {
    const btn = document.getElementById("calib-generate-json");
    const count = parseInt(prompt("Nombre d'exemples à générer (5-30) :", "10")) || 10;
    if (count < 5 || count > 30) return;
    btn.disabled = true; btn.textContent = "Génération JSON...";
    try {
        const { data, status } = await API.post("/api/admin/calibration/generate-json", { count });
        if (status === 200) {
            alert(`${data.count} exemples générés et importés !`);
            calibBatchFilter = data.batch_id || "";
            loadCalibration();
        } else alert(data.error || "Erreur");
    } catch (e) { alert("Erreur : " + (e.message || e)); }
    btn.disabled = false; btn.textContent = "Générer JSON (IA)";
}

async function aiSuggest() {
    const btn = document.getElementById("calib-ai-suggest");
    btn.disabled = true; btn.textContent = "Generation...";
    const { data, status } = await API.post("/api/admin/calibration/ai-suggest", {});
    if (status === 200) {
        alert(`${data.created.length} exemples generes par l'IA !`);
        calibBatchFilter = data.batch_id || "";
        loadCalibration();
    } else {
        alert(data.error || "Erreur");
    }
    btn.disabled = false; btn.textContent = "IA propose";
}

// Context
async function saveContext() {
    const value = document.getElementById("context-textarea").value;
    await API.put("/api/admin/context", { context: value });
    const s = document.getElementById("context-save-status");
    s.textContent = "Sauvegarde";
    setTimeout(() => s.textContent = "", 2000);
}

// Prompt
async function generatePrompt() {
    const count = document.getElementById("prompt-count").value || 50;
    const data = await API.get(`/api/admin/calibration/prompt?count=${count}`);
    const zone = document.getElementById("prompt-output-zone");
    zone.classList.remove("hidden");
    document.getElementById("prompt-output").value = data.prompt;
}

function copyPrompt() {
    const ta = document.getElementById("prompt-output");
    ta.select();
    navigator.clipboard.writeText(ta.value).then(() => {
        const s = document.getElementById("prompt-copy-status");
        s.textContent = "Copie !";
        setTimeout(() => s.textContent = "", 2000);
    });
}

// ==================== Modal Historique ====================

function setupHistoryModal() {
    const modal = document.getElementById("history-modal");
    const closeBtn = document.getElementById("history-modal-close");
    const backdrop = modal?.querySelector(".history-modal-backdrop");
    if (closeBtn) closeBtn.addEventListener("click", () => modal?.classList.add("hidden"));
    if (backdrop) backdrop.addEventListener("click", () => modal?.classList.add("hidden"));
}

async function openHistoryModal(type, id) {
    const modal = document.getElementById("history-modal");
    const titleEl = document.getElementById("history-modal-title");
    const bodyEl = document.getElementById("history-modal-body");
    const pdfLink = document.getElementById("history-download-pdf");
    if (!modal || !bodyEl) return;
    titleEl.textContent = type === "suggestion" ? `Historique — Suggestion #${id}` : `Historique — Proposition CVL #${id}`;
    pdfLink.href = type === "suggestion" ? `/api/admin/suggestions/${id}/pdf` : `/api/admin/official-proposal/${id}/pdf`;
    bodyEl.innerHTML = '<p class="empty-msg">Chargement...</p>';
    modal.classList.remove("hidden");
    try {
        const url = type === "suggestion" ? `/api/admin/suggestions/${id}/history` : `/api/admin/official-proposal/${id}/history`;
        const data = await API.get(url);
        bodyEl.innerHTML = renderHistoryContent(type, data);
    } catch (e) {
        bodyEl.innerHTML = `<p class="empty-msg">Erreur: ${esc(String(e.message || e))}</p>`;
    }
}

function renderHistoryContent(type, d) {
    const fmtDate = (s) => s ? new Date(s).toLocaleString("fr-FR") : "—";
    let html = "";
    if (type === "suggestion") {
        html = `
            <div class="history-section">
                <h4>Titre</h4>
                <p>${esc(d.title || "")}</p>
            </div>
            ${d.subtitle ? `<div class="history-section"><h4>Sous-titre</h4><p>${esc(d.subtitle)}</p></div>` : ""}
            <div class="history-section">
                <h4>Texte original</h4>
                <p>${esc(d.original_text || "")}</p>
            </div>
            <div class="history-section">
                <h4>Informations</h4>
                <ul class="history-info-list">
                    <li><strong>Catégorie:</strong> ${esc(d.category || "—")}</li>
                    <li><strong>Statut:</strong> ${esc(d.status || "—")}</li>
                    <li><strong>Lieu:</strong> ${esc(d.location_name || "—")}</li>
                    <li><strong>Créée le:</strong> ${fmtDate(d.created_at)}</li>
                    <li><strong>Mots-clés:</strong> ${esc((d.keywords || []).join(", ") || "—")}</li>
                    ${d.needs_debate ? `<li><strong>Pour:</strong> ${d.vote_for ?? 0}</li><li><strong>Contre:</strong> ${d.vote_against ?? 0}</li>` : `<li><strong>Soutiens:</strong> ${d.vote_count ?? 0}</li>`}
                </ul>
            </div>
            <div class="history-section">
                <h4>Résultat des votes</h4>
                <div class="history-chart-placeholder" id="history-chart"></div>
            </div>`;
        if (d.needs_debate && (d.arguments_for?.length || d.arguments_against?.length)) {
            html += `
            <div class="history-section">
                <h4>Arguments pour</h4>
                <ul>${(d.arguments_for || []).map((a) => `<li>${esc(a.summary || a.original_text)}</li>`).join("") || "<li>—</li>"}</ul>
            </div>
            <div class="history-section">
                <h4>Arguments contre</h4>
                <ul>${(d.arguments_against || []).map((a) => `<li>${esc(a.summary || a.original_text)}</li>`).join("") || "<li>—</li>"}</ul>
            </div>`;
        }
    } else {
        const contentText = d.content ? (() => {
            const div = document.createElement("div");
            div.innerHTML = d.content;
            return div.textContent || div.innerText || d.content;
        })() : "—";
        const aiEval = (d.proportion != null || d.feasibility != null) ? `
            <div class="history-section">
                <h4>Évaluation IA</h4>
                <ul class="history-info-list">
                    <li><strong>Impact:</strong> ${Math.round((d.proportion ?? 0) * 100)}%</li>
                    <li><strong>Faisabilité:</strong> ${Math.round((d.feasibility ?? 0.5) * 100)}%</li>
                    <li><strong>Coût:</strong> ${Math.round((d.cost ?? 0.5) * 100)}%</li>
                </ul>
            </div>` : "";
        html = `
            <div class="history-section">
                <h4>Contenu</h4>
                <div class="history-proposal-content">${d.content || "—"}</div>
            </div>
            <div class="history-section">
                <h4>Informations</h4>
                <ul class="history-info-list">
                    <li><strong>Statut:</strong> ${esc(d.status || "—")}</li>
                    <li><strong>Active:</strong> ${d.active ? "Oui" : "Non"}</li>
                    <li><strong>Créée le:</strong> ${fmtDate(d.created_at)}</li>
                    ${d.needs_debate ? `<li><strong>Pour:</strong> ${d.vote_for ?? 0}</li><li><strong>Contre:</strong> ${d.vote_against ?? 0}</li>` : `<li><strong>Soutiens:</strong> ${d.vote_for ?? 0}</li>`}
                </ul>
            </div>
            ${aiEval}
            <div class="history-section">
                <h4>Résultat des votes</h4>
                <div class="history-chart-placeholder" id="history-chart"></div>
            </div>`;
        if (d.needs_debate && (d.arguments_for?.length || d.arguments_against?.length)) {
            html += `
            <div class="history-section">
                <h4>Arguments pour</h4>
                <ul>${(d.arguments_for || []).map((a) => `<li>${esc(a.summary || a.original_text)}</li>`).join("") || "<li>—</li>"}</ul>
            </div>
            <div class="history-section">
                <h4>Arguments contre</h4>
                <ul>${(d.arguments_against || []).map((a) => `<li>${esc(a.summary || a.original_text)}</li>`).join("") || "<li>—</li>"}</ul>
            </div>`;
        }
    }
    setTimeout(() => {
        const chartEl = document.getElementById("history-chart");
        if (chartEl && typeof Chart !== "undefined") {
            const canvas = document.createElement("canvas");
            chartEl.innerHTML = "";
            chartEl.appendChild(canvas);
            const cfg = d.needs_debate
                ? { type: "bar", data: { labels: ["Pour", "Contre"], datasets: [{ label: "Votes", data: [d.vote_for ?? 0, d.vote_against ?? 0], backgroundColor: ["#22c55e", "#ef4444"] }] }, options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } } }
                : { type: "bar", data: { labels: ["Soutiens"], datasets: [{ label: "Soutiens", data: [d.vote_count ?? d.vote_for ?? 0], backgroundColor: "#3b82f6" }] }, options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } } };
            new Chart(canvas, cfg);
        }
    }, 50);
    return html;
}

// ==================== Proposition Officielle CVL ====================

function setupCvlProposal() {
    const editor = document.getElementById("cvl-editor");
    if (!editor) return;

    document.querySelectorAll(".cvl-tool-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            editor.focus();
            document.execCommand(btn.dataset.cmd, false, null);
        });
    });
    document.getElementById("cvl-tool-color").addEventListener("input", (e) => {
        editor.focus();
        document.execCommand("foreColor", false, e.target.value);
    });
    document.getElementById("cvl-tool-opacity").addEventListener("input", (e) => {
        editor.focus();
        const v = parseFloat(e.target.value);
        const sel = window.getSelection();
        if (sel.rangeCount) {
            const range = sel.getRangeAt(0);
            const span = document.createElement("span");
            span.style.opacity = v;
            try { range.surroundContents(span); } catch (_) { /* ignore */ }
        }
    });
    document.getElementById("cvl-tool-size").addEventListener("change", (e) => {
        editor.focus();
        const sizes = { 1: "1", 2: "2", 3: "3", 4: "4" };
        document.execCommand("fontSize", false, sizes[e.target.value] || "2");
    });

    document.getElementById("cvl-save-btn").addEventListener("click", saveCvlProposal);
    document.getElementById("cvl-publish-btn").addEventListener("click", publishCvlProposal);
    document.getElementById("cvl-status-select").addEventListener("change", updateCvlStatus);
    document.getElementById("cvl-needs-debate").addEventListener("change", updateCvlNeedsDebate);
    document.getElementById("cvl-close-btn").addEventListener("click", closeCvlProposal);
    document.getElementById("cvl-proposal-select")?.addEventListener("change", loadCvlProposal);
    document.getElementById("cvl-new-proposal-btn")?.addEventListener("click", async () => {
        const list = await API.get("/api/admin/official-proposals");
        const hasActive = list.some((pr) => pr.active);
        if (hasActive) {
            alert("Une seule proposition active est autorisée. Clôturez la proposition actuelle d'abord.");
            return;
        }
        const { data: p } = await API.post("/api/admin/official-proposal/new", {});
        currentCvlProposalId = p?.id;
        await loadCvlProposalsList(currentCvlProposalId);
        document.getElementById("cvl-proposal-select").value = currentCvlProposalId;
        loadCvlProposal();
    });
    document.getElementById("cvl-add-arg-for-btn")?.addEventListener("click", () => addCvlArgument("for"));
    document.getElementById("cvl-add-arg-against-btn")?.addEventListener("click", () => addCvlArgument("against"));
    document.getElementById("cvl-history-btn")?.addEventListener("click", () => {
        const pid = currentCvlProposalId || document.getElementById("cvl-proposal-select")?.value;
        if (pid) openHistoryModal("proposal", parseInt(pid));
        else alert("Aucune proposition sélectionnée.");
    });
}

let currentCvlProposalId = null;

async function loadCvlProposalsList(desiredPid) {
    const sel = document.getElementById("cvl-proposal-select");
    if (!sel) return;
    try {
        const list = await API.get("/api/admin/official-proposals");
        const ids = list.map((pr) => pr.id);
        let selectedId = desiredPid ?? currentCvlProposalId ?? list[0]?.id;
        if (selectedId && !ids.includes(selectedId)) selectedId = list[0]?.id;
        sel.innerHTML = list.length ? list.map((pr) =>
            `<option value="${pr.id}" ${pr.id === selectedId ? "selected" : ""}>#${pr.id} ${pr.active ? "● " : ""}${esc(pr.content_preview || "Vide")}</option>`
        ).join("") : '<option value="">Aucune proposition</option>';
        if (list.length) {
            currentCvlProposalId = selectedId ?? list[0].id;
            sel.value = String(currentCvlProposalId);
        }
        const newBtn = document.getElementById("cvl-new-proposal-btn");
        if (newBtn) newBtn.disabled = list.some((pr) => pr.active);
    } catch (e) { sel.innerHTML = '<option value="">Erreur</option>'; }
}

async function loadCvlProposal() {
    const sel = document.getElementById("cvl-proposal-select");
    const userSelectedPid = sel?.value ? parseInt(sel.value) : null;
    try {
        await loadCvlProposalsList(userSelectedPid);
        const pid = sel?.value ? parseInt(sel.value) : null;
        if (!pid) {
            document.getElementById("cvl-editor").innerHTML = "";
            document.getElementById("cvl-preview").innerHTML = "<p class=\"empty-msg\">Aucune proposition. Cliquez sur + Nouvelle.</p>";
            document.getElementById("cvl-arguments-section")?.classList.add("hidden");
            const pdfLink = document.getElementById("cvl-pdf-link");
            if (pdfLink) pdfLink.href = "#";
            return;
        }
        const p = await API.get(`/api/admin/official-proposal?id=${pid}`);
        currentCvlProposalId = p?.id;
        const editor = document.getElementById("cvl-editor");
        const statusSel = document.getElementById("cvl-status-select");
        const preview = document.getElementById("cvl-preview");
        if (p) {
            editor.innerHTML = p.content || "";
            statusSel.value = p.status || "En cours";
            const needsDebateCb = document.getElementById("cvl-needs-debate");
            if (needsDebateCb) needsDebateCb.checked = !!p.needs_debate;
            preview.innerHTML = p.content ? `<div class="cvl-preview-content">${p.content}</div>` : "<p class=\"empty-msg\">Aucune proposition publiee</p>";
            const evalEl = document.getElementById("cvl-ai-eval");
            if (evalEl && p.content) {
                const imp = Math.round((p.proportion ?? 0) * 100);
                const fais = Math.round((p.feasibility ?? 0.5) * 100);
                const cout = Math.round((p.cost ?? 0.5) * 100);
                evalEl.innerHTML = `<span class="cvl-eval-item"><strong>Impact:</strong> ${imp}%</span><span class="cvl-eval-item"><strong>Faisabilité:</strong> ${fais}%</span><span class="cvl-eval-item"><strong>Coût:</strong> ${cout}%</span><span class="cvl-eval-item"><strong>Débat:</strong> ${p.needs_debate ? "Oui" : "Non"}</span>`;
                evalEl.classList.remove("hidden");
            } else if (evalEl) evalEl.classList.add("hidden");
        } else {
            editor.innerHTML = "";
            statusSel.value = "En cours";
            const needsDebateCb = document.getElementById("cvl-needs-debate");
            if (needsDebateCb) needsDebateCb.checked = false;
            preview.innerHTML = "<p class=\"empty-msg\">Aucune proposition. Redigez et publiez.</p>";
            const evalEl = document.getElementById("cvl-ai-eval");
            if (evalEl) evalEl.classList.add("hidden");
            const pdfLink = document.getElementById("cvl-pdf-link");
            if (pdfLink) pdfLink.href = "#";
        }
        renderCvlArguments(p);
        const argsSection = document.getElementById("cvl-arguments-section");
        if (argsSection) argsSection.classList.toggle("hidden", !p?.needs_debate);
        const pdfLink = document.getElementById("cvl-pdf-link");
        if (pdfLink) pdfLink.href = p?.id ? `/api/admin/official-proposal/${p.id}/pdf` : "#";
        loadCvlProposalsList(currentCvlProposalId);
    } catch (e) { console.error(e); }
}

function renderCvlArguments(p) {
    const forList = document.getElementById("cvl-args-for-list");
    const againstList = document.getElementById("cvl-args-against-list");
    if (!forList || !againstList) return;
    const argsFor = p?.arguments_for || [];
    const argsAgainst = p?.arguments_against || [];
    forList.innerHTML = argsFor.map((a) =>
        `<li><span>${esc(a.summary || a.original_text)}</span><button type="button" class="btn btn-ghost btn-sm cvl-arg-remove" data-id="${a.id}">×</button></li>`
    ).join("");
    againstList.innerHTML = argsAgainst.map((a) =>
        `<li><span>${esc(a.summary || a.original_text)}</span><button type="button" class="btn btn-ghost btn-sm cvl-arg-remove" data-id="${a.id}">×</button></li>`
    ).join("");
    forList.querySelectorAll(".cvl-arg-remove").forEach((btn) => {
        btn.addEventListener("click", () => removeCvlArgument(parseInt(btn.dataset.id)));
    });
    againstList.querySelectorAll(".cvl-arg-remove").forEach((btn) => {
        btn.addEventListener("click", () => removeCvlArgument(parseInt(btn.dataset.id)));
    });
}

async function removeCvlArgument(argId) {
    await API.delete(`/api/admin/official-proposal/argument/${argId}`);
    loadCvlProposal();
}

async function addCvlArgument(side) {
    const input = document.getElementById(`cvl-add-arg-${side}`);
    const text = input?.value?.trim();
    if (!text || text.length < 5) return;
    const pid = currentCvlProposalId || document.getElementById("cvl-proposal-select")?.value;
    if (!pid) return;
    await API.post(`/api/admin/official-proposal/${pid}/argument`, { side, text });
    input.value = "";
    loadCvlProposal();
}

async function saveCvlProposal() {
    const content = document.getElementById("cvl-editor").innerHTML;
    const needsDebate = document.getElementById("cvl-needs-debate").checked;
    const payload = { content, needs_debate: needsDebate };
    if (currentCvlProposalId) payload.id = currentCvlProposalId;
    await API.put("/api/admin/official-proposal", payload);
    document.getElementById("cvl-save-status").textContent = "Sauvegarde";
    setTimeout(() => { document.getElementById("cvl-save-status").textContent = ""; }, 2000);
    document.getElementById("cvl-preview").innerHTML = content ? `<div class="cvl-preview-content">${content}</div>` : "<p class=\"empty-msg\">Vide</p>";
}

async function publishCvlProposal() {
    await saveCvlProposal();
    const payload = currentCvlProposalId ? { id: currentCvlProposalId } : {};
    await API.post("/api/admin/official-proposal/publish", payload);
    document.getElementById("cvl-save-status").textContent = "Publie !";
    setTimeout(() => { document.getElementById("cvl-save-status").textContent = ""; }, 2000);
}

async function updateCvlStatus() {
    const status = document.getElementById("cvl-status-select").value;
    const payload = { status };
    if (currentCvlProposalId) payload.id = currentCvlProposalId;
    await API.put("/api/admin/official-proposal", payload);
    document.getElementById("cvl-save-status").textContent = "Statut mis a jour";
    setTimeout(() => { document.getElementById("cvl-save-status").textContent = ""; }, 2000);
}

async function updateCvlNeedsDebate() {
    const needsDebate = document.getElementById("cvl-needs-debate").checked;
    const payload = { needs_debate: needsDebate };
    if (currentCvlProposalId) payload.id = currentCvlProposalId;
    await API.put("/api/admin/official-proposal", payload);
    document.getElementById("cvl-save-status").textContent = "Mode debat mis a jour";
    setTimeout(() => { document.getElementById("cvl-save-status").textContent = ""; }, 2000);
}

async function closeCvlProposal() {
    if (!confirm("Cloturer la proposition ? Elle sera retirée de l'affichage.")) return;
    await API.post("/api/admin/official-proposal/close", {});
    loadCvlProposal();
}

// ==================== Information Officielle CVL ====================

let cvlInfoSetupDone = false;

function setupCvlOfficialInfo() {
    if (cvlInfoSetupDone) return;
    cvlInfoSetupDone = true;
    document.getElementById("cvl-info-create-btn")?.addEventListener("click", createCvlOfficialInfo);
}

async function loadCvlOfficialInfo() {
    setupCvlOfficialInfo();
    try {
        const list = await API.get("/api/admin/cvl-official-info");
        const c = document.getElementById("cvl-info-list");
        if (!list.length) {
            c.innerHTML = "<p class=\"empty-msg\">Aucune information. Créez-en une ci-dessus.</p>";
            return;
        }
        c.innerHTML = list.map((i) => `
            <div class="cvl-info-item" data-id="${i.id}">
                <div class="cvl-info-header">
                    <span class="cvl-info-title">${esc(i.title)}</span>
                    <span class="cvl-info-badges">
                        <span class="cvl-info-style cvl-info-${i.style}">${i.style}</span>
                        <span class="cvl-info-mode">${i.display_mode}</span>
                        ${i.active ? '<span class="cvl-info-active">Actif</span>' : '<span class="cvl-info-inactive">Inactif</span>'}
                    </span>
                </div>
                ${i.content ? `<div class="cvl-info-content">${esc(i.content)}</div>` : ""}
                <div class="cvl-info-actions">
                    <label class="cvl-info-toggle"><input type="checkbox" ${i.active ? "checked" : ""} data-id="${i.id}"> Actif</label>
                    <button class="btn btn-sm btn-ghost cvl-info-retirer" data-id="${i.id}">Retirer</button>
                </div>
            </div>
        `).join("");
        c.querySelectorAll(".cvl-info-toggle input").forEach((cb) => {
            cb.addEventListener("change", () => updateCvlInfoActive(parseInt(cb.dataset.id), cb.checked));
        });
        c.querySelectorAll(".cvl-info-retirer").forEach((btn) => {
            btn.addEventListener("click", () => deleteCvlInfo(parseInt(btn.dataset.id)));
        });
    } catch (e) {
        document.getElementById("cvl-info-list").innerHTML = `<p class="empty-msg">Erreur: ${esc(String(e.message || e))}</p>`;
    }
}

async function createCvlOfficialInfo() {
    const title = document.getElementById("cvl-info-title")?.value?.trim();
    if (!title) { alert("Titre requis"); return; }
    await API.post("/api/admin/cvl-official-info", {
        title,
        content: document.getElementById("cvl-info-content")?.value || "",
        style: document.getElementById("cvl-info-style")?.value || "info",
        display_mode: document.getElementById("cvl-info-mode")?.value || "banner",
    });
    document.getElementById("cvl-info-title").value = "";
    document.getElementById("cvl-info-content").value = "";
    loadCvlOfficialInfo();
}

async function updateCvlInfoActive(id, active) {
    await API.put(`/api/admin/cvl-official-info/${id}`, { active });
    loadCvlOfficialInfo();
}

async function deleteCvlInfo(id) {
    if (!confirm("Retirer cette information de l'affichage ?")) return;
    await API.delete(`/api/admin/cvl-official-info/${id}`);
    loadCvlOfficialInfo();
}

// ==================== Announcements ====================

function setupAnnouncements() {
    const chatInput = document.getElementById("chat-ia-input");
    const chatSend = document.getElementById("chat-ia-send");
    if (chatInput && chatSend) {
        chatSend.addEventListener("click", sendChatIA);
        chatInput.addEventListener("keydown", (e) => { if (e.key === "Enter") sendChatIA(); });
    }
    document.getElementById("ann-create-btn").addEventListener("click", createAnnouncement);
    ["ann-title", "ann-content", "ann-style"].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.addEventListener("input", previewAnnouncement);
    });
    document.getElementById("admin-priority-clear")?.addEventListener("click", async () => {
        await API.post("/api/admin/announcements/clear-priority");
        loadPriorityBanner();
        loadAnnouncements();
    });
}

async function loadAnnouncements() {
    try { renderAnnouncements(await API.get("/api/admin/announcements")); } catch (e) { console.error(e); }
}

async function loadPriorityBanner() {
    try {
        const p = await API.get("/api/admin/priority-announcement");
        const banner = document.getElementById("admin-priority-banner");
        if (p && p.id) {
            banner.classList.remove("hidden");
            banner.querySelector(".admin-priority-text").textContent = `Annonce prioritaire active : « ${esc(p.title)} » — Tous les displays affichent cette annonce`;
        } else {
            banner.classList.add("hidden");
        }
    } catch (e) { /* ignore */ }
}

function renderAnnouncements(anns) {
    const c = document.getElementById("ann-list");
    if (!anns.length) { c.innerHTML = '<p class="empty-msg">Aucune annonce</p>'; loadPriorityBanner(); return; }
    c.innerHTML = anns.map((a) => `
        <div class="ann-card ann-style-${a.style}${a.active ? "" : " ann-expired"}${a.is_priority ? " ann-priority" : ""}">
            <div class="ann-card-content">
                <strong>${esc(a.title)}</strong>
                ${a.content ? `<p>${esc(a.content)}</p>` : ""}
                ${a.extra_info ? `<p class="ann-extra-info"><em>Infos compl.: ${esc(a.extra_info)}</em></p>` : ""}
                <span class="ann-card-meta">${a.active ? "Active" : "Expiree"}${a.is_priority ? " · PRIORITAIRE" : ""}${a.expires_at ? ` — Expire: ${new Date(a.expires_at).toLocaleString("fr-FR")}` : ""}</span>
            </div>
            <div class="ann-card-actions">
                ${a.active && !a.is_priority ? `<button class="btn btn-sm btn-primary ann-priority-btn" data-id="${a.id}" data-title="${esc(a.title)}">Annonce prioritaire</button>` : ""}
                ${a.active ? `<button class="btn btn-sm btn-ghost ann-deact" data-id="${a.id}">Desactiver</button>` : `<button class="btn btn-sm btn-primary ann-react" data-id="${a.id}">Activer</button>`}
                <button class="btn btn-sm btn-ghost ann-extra" data-id="${a.id}" data-extra="${esc(a.extra_info || "")}" data-title="${esc(a.title)}">Extra info</button>
                <button class="btn btn-sm btn-ghost ann-del" data-id="${a.id}" style="color:var(--error)">Supprimer</button>
            </div>
        </div>`).join("");
    c.querySelectorAll(".ann-deact").forEach((b) => { b.addEventListener("click", async () => { await API.put(`/api/admin/announcements/${b.dataset.id}`, { active: false }); loadAnnouncements(); loadPriorityBanner(); }); });
    c.querySelectorAll(".ann-react").forEach((b) => { b.addEventListener("click", async () => { await API.put(`/api/admin/announcements/${b.dataset.id}`, { active: true }); loadAnnouncements(); loadPriorityBanner(); }); });
    c.querySelectorAll(".ann-del").forEach((b) => { b.addEventListener("click", async () => { if (confirm("Supprimer ?")) { await API.delete(`/api/admin/announcements/${b.dataset.id}`); loadAnnouncements(); loadPriorityBanner(); } }); });
    c.querySelectorAll(".ann-priority-btn").forEach((b) => {
        b.addEventListener("click", async () => {
            const title = b.dataset.title || "";
            if (!confirm(`Activer l'annonce prioritaire « ${title} » ?\n\nTous les displays (TV, écrans) afficheront UNIQUEMENT cette annonce jusqu'à désactivation.`)) return;
            await API.post(`/api/admin/announcements/${b.dataset.id}/set-priority`);
            loadAnnouncements();
            loadPriorityBanner();
        });
    });
    c.querySelectorAll(".ann-extra").forEach((b) => {
        b.addEventListener("click", () => {
            const extra = prompt("Informations complémentaires (affichées en dessous sur les displays) :", b.dataset.extra || "");
            if (extra !== null) {
                API.put(`/api/admin/announcements/${b.dataset.id}`, { extra_info: extra }).then(() => loadAnnouncements());
            }
        });
    });
    loadPriorityBanner();
}

async function sendChatIA() {
    const input = document.getElementById("chat-ia-input");
    const messages = document.getElementById("chat-ia-messages");
    const msg = input?.value?.trim();
    if (!msg) return;
    input.value = "";
    messages.innerHTML = (messages.innerHTML || "") + `<div class="chat-ia-msg chat-ia-user"><span>${esc(msg)}</span></div>`;
    messages.scrollTop = messages.scrollHeight;
    const btn = document.getElementById("chat-ia-send");
    btn.disabled = true;
    try {
        const { data } = await API.post("/api/admin/chat-ia", { message: msg });
        messages.innerHTML += `<div class="chat-ia-msg chat-ia-bot"><span>${esc(data.reply || "")}</span></div>`;
        messages.scrollTop = messages.scrollHeight;
        if (data.announcement) loadAnnouncements();
    } catch (e) {
        messages.innerHTML += `<div class="chat-ia-msg chat-ia-bot chat-ia-error"><span>Erreur : ${esc(String(e))}</span></div>`;
    }
    btn.disabled = false;
}

async function createAnnouncement() {
    const title = document.getElementById("ann-title").value.trim();
    if (!title) return;
    const { status } = await API.post("/api/admin/announcements", {
        title,
        content: document.getElementById("ann-content").value.trim(),
        style: document.getElementById("ann-style").value,
        duration_minutes: parseInt(document.getElementById("ann-duration").value) || 60,
    });
    if (status === 201) {
        document.getElementById("ann-title").value = "";
        document.getElementById("ann-content").value = "";
        document.getElementById("ann-preview").innerHTML = "";
        loadAnnouncements();
    }
}

// ==================== Bus Settings ====================

function setupBus() {
    document.getElementById("bus-save")?.addEventListener("click", saveBusSettings);
    document.getElementById("bus-add-slot")?.addEventListener("click", () => {
        const editor = document.getElementById("bus-schedule-editor");
        const div = document.createElement("div");
        div.className = "bus-slot-row";
        div.innerHTML = `<input type="time" class="bus-slot-start" value="07:40"><span>→</span><input type="time" class="bus-slot-end" value="08:10"><button type="button" class="btn btn-sm btn-ghost bus-slot-remove">×</button>`;
        div.querySelector(".bus-slot-remove").addEventListener("click", () => div.remove());
        editor.appendChild(div);
    });
}

async function loadBusSettings() {
    try {
        const data = await API.get("/api/admin/bus-settings");
        const gtfsUrl = document.getElementById("bus-gtfs-url");
        if (gtfsUrl) gtfsUrl.value = data.bus_gtfs_url || "";
        const stopTa = document.getElementById("bus-stop-ids");
        if (stopTa) stopTa.value = JSON.stringify(data.bus_stop_ids || [], null, 0);
        const routeOrderTa = document.getElementById("bus-route-order");
        if (routeOrderTa) routeOrderTa.value = JSON.stringify(data.bus_route_order || [], null, 2);
        const etaI = document.getElementById("bus-eta-imminent-max");
        if (etaI) etaI.value = data.bus_eta_imminent_max ?? 1;
        const etaS = document.getElementById("bus-eta-soon-max");
        if (etaS) etaS.value = data.bus_eta_soon_max ?? 3;
        const etaN = document.getElementById("bus-eta-near-max");
        if (etaN) etaN.value = data.bus_eta_near_max ?? 7;
        const h = document.getElementById("bus-compute-horizon-minutes");
        if (h) h.value = data.bus_compute_horizon_minutes ?? data.bus_horizon_minutes ?? 600;
        const md = document.getElementById("bus-max-departures");
        if (md) md.value = data.bus_max_departures ?? 32;
        const rm = document.getElementById("bus-relevance-minutes");
        if (rm) rm.value = data.bus_relevance_minutes ?? 30;
        const cs = document.getElementById("bus-cache-seconds");
        if (cs) cs.value = data.bus_cache_seconds ?? 60;
        const gr = document.getElementById("bus-gtfs-refresh-days");
        if (gr) gr.value = data.bus_gtfs_refresh_days ?? 7;
        const apiKey = document.getElementById("bus-api-key");
        if (apiKey) apiKey.value = data.bus_api_key || "";
        const useStatic = document.getElementById("bus-use-static");
        if (useStatic) useStatic.checked = data.bus_use_static || false;
        const restrictSched = document.getElementById("bus-restrict-to-schedule");
        if (restrictSched) restrictSched.checked = data.bus_restrict_to_schedule || false;
        const tvSchedOnly = document.getElementById("bus-tv-show-only-during-schedule");
        if (tvSchedOnly) tvSchedOnly.checked = data.bus_tv_show_only_during_schedule || false;
        document.getElementById("bus-force-display").checked = data.bus_force_display || false;
        document.getElementById("bus-force-display-until").value = data.bus_force_display_until ? data.bus_force_display_until.slice(0, 16) : "";
        document.getElementById("bus-alternance-enabled").checked = data.bus_alternance_enabled || false;
        document.getElementById("bus-alternance-interval-sec").value = data.bus_alternance_interval_sec || 60;
        document.getElementById("bus-test-mode").checked = data.bus_test_mode || false;
        document.getElementById("bus-test-perturbations").checked = data.bus_test_perturbations || false;
        const excludeEl = document.getElementById("bus-exclude-pages");
        if (excludeEl) {
            const pages = data.bus_display_pages || [];
            excludeEl.innerHTML = pages.length ? pages.map(p => `
                <label class="bus-exclude-item">
                    <input type="checkbox" data-page-id="${p.id}" ${p.bus_excluded ? "checked" : ""}>
                    <span>/tv/${esc(p.slug)}</span>
                </label>
            `).join("") : "<p class=\"empty-hint\">Aucune page d'affichage. Créez-en dans Pages d'affichage.</p>";
        }
        const editor = document.getElementById("bus-schedule-editor");
        editor.innerHTML = "";
        (data.bus_schedule || []).forEach((slot) => {
            const div = document.createElement("div");
            div.className = "bus-slot-row";
            div.innerHTML = `<input type="time" class="bus-slot-start" value="${slot.start || "07:40"}"><span>→</span><input type="time" class="bus-slot-end" value="${slot.end || "08:10"}"><button type="button" class="btn btn-sm btn-ghost bus-slot-remove">×</button>`;
            div.querySelector(".bus-slot-remove").addEventListener("click", () => div.remove());
            editor.appendChild(div);
        });
        if (!editor.children.length) {
            const defaultSlots = [
                { start: "07:15", end: "08:30" },
                { start: "11:45", end: "13:15" },
                { start: "16:15", end: "18:30" },
            ];
            defaultSlots.forEach((slot) => {
                const div = document.createElement("div");
                div.className = "bus-slot-row";
                div.innerHTML = `<input type="time" class="bus-slot-start" value="${slot.start}"><span>→</span><input type="time" class="bus-slot-end" value="${slot.end}"><button type="button" class="btn btn-sm btn-ghost bus-slot-remove">×</button>`;
                div.querySelector(".bus-slot-remove").addEventListener("click", () => div.remove());
                editor.appendChild(div);
            });
        }
    } catch (e) { console.error(e); }
}

async function saveBusSettings() {
    const schedule = [];
    document.querySelectorAll("#bus-schedule-editor .bus-slot-row").forEach((row) => {
        const start = row.querySelector(".bus-slot-start")?.value;
        const end = row.querySelector(".bus-slot-end")?.value;
        if (start && end) schedule.push({ start, end });
    });
    const until = document.getElementById("bus-force-display-until")?.value;
    let stopIds = ["CSC01", "CSC02", "TRA01", "TRA02", "10869", "10954"];
    const ta = document.getElementById("bus-stop-ids")?.value?.trim();
    if (ta) {
        try {
            const parsed = JSON.parse(ta);
            if (Array.isArray(parsed)) stopIds = parsed.map(String);
        } catch (e) {
            alert("JSON invalide pour les stop_id.");
            return;
        }
    }
    let routeOrder = [];
    const rota = document.getElementById("bus-route-order")?.value?.trim();
    if (rota) {
        try {
            const parsed = JSON.parse(rota);
            if (Array.isArray(parsed)) routeOrder = parsed.map(String);
        } catch (e) {
            alert("JSON invalide pour l’ordre des lignes.");
            return;
        }
    }
    await API.put("/api/admin/bus-settings", {
        bus_gtfs_url: document.getElementById("bus-gtfs-url")?.value?.trim() || "",
        bus_stop_ids: stopIds,
        bus_route_order: routeOrder,
        bus_eta_imminent_max: parseInt(document.getElementById("bus-eta-imminent-max")?.value, 10) || 1,
        bus_eta_soon_max: parseInt(document.getElementById("bus-eta-soon-max")?.value, 10) || 3,
        bus_eta_near_max: parseInt(document.getElementById("bus-eta-near-max")?.value, 10) || 7,
        bus_compute_horizon_minutes: parseInt(document.getElementById("bus-compute-horizon-minutes")?.value, 10) || 600,
        bus_horizon_minutes: parseInt(document.getElementById("bus-compute-horizon-minutes")?.value, 10) || 600,
        bus_max_departures: parseInt(document.getElementById("bus-max-departures")?.value, 10) || 32,
        bus_relevance_minutes: parseInt(document.getElementById("bus-relevance-minutes")?.value, 10) || 30,
        bus_cache_seconds: parseInt(document.getElementById("bus-cache-seconds")?.value, 10) || 60,
        bus_gtfs_refresh_days: parseInt(document.getElementById("bus-gtfs-refresh-days")?.value, 10) || 7,
        bus_api_key: document.getElementById("bus-api-key")?.value?.trim() || "",
        bus_use_static: document.getElementById("bus-use-static")?.checked || false,
        bus_restrict_to_schedule: document.getElementById("bus-restrict-to-schedule")?.checked || false,
        bus_tv_show_only_during_schedule: document.getElementById("bus-tv-show-only-during-schedule")?.checked || false,
        bus_force_display: document.getElementById("bus-force-display")?.checked || false,
        bus_force_display_until: until ? new Date(until).toISOString().slice(0, 19) : "",
        bus_schedule: schedule,
        bus_alternance_enabled: document.getElementById("bus-alternance-enabled")?.checked || false,
        bus_alternance_interval_sec: parseInt(document.getElementById("bus-alternance-interval-sec")?.value) || 60,
        bus_test_mode: document.getElementById("bus-test-mode")?.checked || false,
        bus_test_perturbations: document.getElementById("bus-test-perturbations")?.checked || false,
        bus_excluded_page_ids: Array.from(document.querySelectorAll("#bus-exclude-pages input:checked")).map(el => parseInt(el.dataset.pageId)).filter(n => !isNaN(n)),
    });
    const s = document.getElementById("bus-save-status");
    if (s) { s.textContent = "Sauvegardé"; setTimeout(() => (s.textContent = ""), 2000); }
}

// ==================== Backup & Historique ====================

let currentBackupPreviewId = null;

function setupBackup() {
    document.getElementById("backup-save-settings")?.addEventListener("click", saveBackupSettings);
    document.getElementById("backup-create-btn")?.addEventListener("click", createBackup);
    document.getElementById("backup-preview-close")?.addEventListener("click", () => document.getElementById("backup-preview-modal")?.classList.add("hidden"));
    document.querySelector(".backup-preview-backdrop")?.addEventListener("click", () => document.getElementById("backup-preview-modal")?.classList.add("hidden"));
    document.getElementById("backup-restore-btn")?.addEventListener("click", restoreBackup);
}

async function loadBackup() {
    try {
        const [backups, settings] = await Promise.all([
            API.get("/api/admin/backups"),
            API.get("/api/admin/backup-settings"),
        ]);
        document.getElementById("backup-interval-hours").value = settings.backup_interval_hours ?? 0;
        const listEl = document.getElementById("backup-list");
        if (!listEl) return;
        if (!backups.length) {
            listEl.innerHTML = '<p class="empty-msg">Aucune backup. Créez-en une ou configurez l\'auto-backup.</p>';
            return;
        }
        listEl.innerHTML = backups.map((b) => {
            const stats = b.stats || {};
            const date = b.created_at ? new Date(b.created_at).toLocaleString("fr-FR") : "—";
            const size = b.size_bytes ? `${(b.size_bytes / 1024).toFixed(1)} Ko` : "—";
            return `
            <div class="backup-card" data-id="${b.id}">
                <div class="backup-card-main">
                    <strong>${esc(b.filename)}</strong>
                    <span class="backup-meta">${date} · ${size}</span>
                </div>
                <div class="backup-card-stats">
                    <span>${stats.suggestions_count ?? 0} suggestions</span>
                    <span>${stats.announcements_count ?? 0} annonces</span>
                    <span>${stats.votes_total ?? 0} votes</span>
                </div>
                <div class="backup-card-actions">
                    <button type="button" class="btn btn-sm btn-secondary backup-preview-btn" data-id="${b.id}">Prévisualiser</button>
                    <a href="/api/admin/backups/${b.id}/download" class="btn btn-sm btn-ghost" download>Télécharger</a>
                </div>
            </div>`;
        }).join("");
        listEl.querySelectorAll(".backup-preview-btn").forEach((btn) => {
            btn.addEventListener("click", () => openBackupPreview(parseInt(btn.dataset.id)));
        });
    } catch (e) { console.error(e); }
}

async function saveBackupSettings() {
    const v = parseInt(document.getElementById("backup-interval-hours")?.value || "0");
    await API.put("/api/admin/backup-settings", { backup_interval_hours: Math.max(0, Math.min(168, v)) });
    const s = document.getElementById("backup-settings-status");
    if (s) { s.textContent = "Sauvegardé"; setTimeout(() => (s.textContent = ""), 2000); }
}

async function createBackup() {
    const btn = document.getElementById("backup-create-btn");
    const status = document.getElementById("backup-create-status");
    if (btn) btn.disabled = true;
    if (status) status.textContent = "Création...";
    try {
        await API.post("/api/admin/backups", {});
        if (status) status.textContent = "Backup créée";
        loadBackup();
    } catch (e) {
        if (status) status.textContent = "Erreur: " + (e.message || "Échec");
    }
    if (btn) btn.disabled = false;
    setTimeout(() => { if (status) status.textContent = ""; }, 3000);
}

async function openBackupPreview(id) {
    currentBackupPreviewId = id;
    const modal = document.getElementById("backup-preview-modal");
    const title = document.getElementById("backup-preview-title");
    const body = document.getElementById("backup-preview-body");
    const downloadLink = document.getElementById("backup-download-link");
    if (!modal || !body) return;
    try {
        const data = await API.get(`/api/admin/backups/${id}/preview`);
        title.textContent = `Prévisualisation — ${data.created_at || "backup"}`;
        downloadLink.href = `/api/admin/backups/${id}/download`;
        downloadLink.download = data.created_at ? `backup_${data.created_at.replace(/[:.]/g, "-")}.json` : "backup.json";
        const stats = data.stats || {};
        let html = `<div class="backup-preview-stats">
            <strong>Statistiques</strong>
            <ul><li>Suggestions: ${stats.suggestions_count ?? 0}</li>
            <li>Annonces: ${stats.announcements_count ?? 0}</li>
            <li>Votes totaux: ${stats.votes_total ?? 0}</li>
            <li>Propositions: ${stats.proposals_count ?? 0}</li>
            <li>Lieux: ${stats.locations_count ?? 0}</li></ul>
        </div>`;
        if (data.by_status && Object.keys(data.by_status).length) {
            html += `<div class="backup-preview-section"><strong>Par statut</strong><pre>${esc(JSON.stringify(data.by_status, null, 2))}</pre></div>`;
        }
        if (data.by_category && Object.keys(data.by_category).length) {
            html += `<div class="backup-preview-section"><strong>Par catégorie</strong><pre>${esc(JSON.stringify(data.by_category, null, 2))}</pre></div>`;
        }
        if (data.suggestions?.length) {
            html += `<div class="backup-preview-section"><strong>Suggestions (${data.suggestions.length})</strong><div class="backup-preview-list">`;
            data.suggestions.slice(0, 20).forEach((s) => {
                html += `<div class="backup-preview-item"><span class="badge badge-category">${esc(s.category)}</span> ${esc(s.title)}</div>`;
            });
            if (data.suggestions.length > 20) html += `<p>... et ${data.suggestions.length - 20} autres</p>`;
            html += "</div></div>";
        }
        if (data.announcements?.length) {
            html += `<div class="backup-preview-section"><strong>Annonces (${data.announcements.length})</strong><div class="backup-preview-list">`;
            data.announcements.forEach((a) => { html += `<div class="backup-preview-item">${esc(a.title)}</div>`; });
            html += "</div></div>";
        }
        body.innerHTML = html;
        modal.classList.remove("hidden");
    } catch (e) {
        body.innerHTML = `<p class="error-msg">Erreur: ${esc(e.message || "Impossible de charger")}</p>`;
        modal.classList.remove("hidden");
    }
}

async function restoreBackup() {
    if (!currentBackupPreviewId) return;
    if (!confirm("Restaurer cette backup remplacera toutes les données actuelles. Continuer ?")) return;
    try {
        await API.post(`/api/admin/backups/${currentBackupPreviewId}/restore`);
        document.getElementById("backup-preview-modal")?.classList.add("hidden");
        alert("Restauration terminée. Rechargez la page.");
        location.reload();
    } catch (e) {
        alert("Erreur: " + (e.message || "Échec de la restauration"));
    }
}

function previewAnnouncement() {
    const t = document.getElementById("ann-title").value.trim();
    const co = document.getElementById("ann-content").value.trim();
    const s = document.getElementById("ann-style").value;
    const p = document.getElementById("ann-preview");
    if (!t) { p.innerHTML = ""; return; }
    p.innerHTML = `<div class="display-announcement display-ann-${s}"><strong>${esc(t)}</strong>${co ? `<p>${esc(co)}</p>` : ""}</div>`;
}

// ==================== Settings ====================

function setupSettings() {
    document.getElementById("toggle-submissions").addEventListener("change", async (e) => {
        await API.put("/api/admin/settings", { submissions_open: e.target.checked ? "true" : "false" });
        updateSettingsHints();
    });

    document.getElementById("toggle-feature-bus")?.addEventListener("change", async (e) => {
        await API.put("/api/admin/settings", { feature_bus_enabled: e.target.checked ? "true" : "false" });
        updateSettingsHints();
    });

    document.getElementById("toggle-feature-display-dynamic")?.addEventListener("change", async (e) => {
        await API.put("/api/admin/settings", { feature_display_dynamic_enabled: e.target.checked ? "true" : "false" });
        updateSettingsHints();
    });

    document.getElementById("toggle-feature-ringtone-banner")?.addEventListener("change", async (e) => {
        await setRingtoneBannerEnabledFromUi(e.target.checked);
    });

    document.getElementById("display-mode-select").addEventListener("change", async (e) => {
        await API.put("/api/admin/settings", { display_mode: e.target.value });
        updateSettingsHints();
        const cfg = document.getElementById("waiting-text-config");
        cfg.classList.toggle("hidden", e.target.value !== "waiting");
    });

    document.getElementById("save-waiting-text").addEventListener("click", async () => {
        const title = document.getElementById("waiting-title-input").value;
        const text = document.getElementById("waiting-text-input").value;
        await API.put("/api/admin/settings", { display_waiting_title: title, display_waiting_text: text });
        const s = document.getElementById("waiting-save-status");
        s.textContent = "\u2713 Sauvegard\u00e9";
        setTimeout(() => (s.textContent = ""), 2000);
    });

    document.getElementById("save-subtitle-threshold")?.addEventListener("click", async () => {
        const raw = parseInt(document.getElementById("subtitle-like-threshold")?.value, 10);
        const v = Math.max(2, Math.min(200, Number.isFinite(raw) ? raw : 5));
        await API.put("/api/admin/settings", { subtitle_like_threshold: String(v) });
        const st = document.getElementById("subtitle-threshold-save-status");
        if (st) {
            st.textContent = "\u2713 Sauvegard\u00e9";
            setTimeout(() => (st.textContent = ""), 2000);
        }
    });
}

async function loadSettings() {
    try {
        const settings = await API.get("/api/admin/settings");
        document.getElementById("toggle-submissions").checked = settings.submissions_open !== "false";
        document.getElementById("toggle-feature-bus").checked = settings.feature_bus_enabled !== "false";
        document.getElementById("toggle-feature-display-dynamic").checked = settings.feature_display_dynamic_enabled !== "false";
        const tr = document.getElementById("toggle-feature-ringtone-banner");
        if (tr) tr.checked = settings.feature_ringtone_banner_enabled === "true";
        document.getElementById("display-mode-select").value = settings.display_mode || "normal";
        document.getElementById("waiting-title-input").value = settings.display_waiting_title || "";
        document.getElementById("waiting-text-input").value = settings.display_waiting_text || "";
        document.getElementById("waiting-text-config").classList.toggle("hidden", settings.display_mode !== "waiting");
        const stThr = document.getElementById("subtitle-like-threshold");
        if (stThr) stThr.value = settings.subtitle_like_threshold ?? "5";
        updateSettingsHints();
    } catch (e) {
        console.error(e);
    }
}

function updateSettingsHints() {
    const subOpen = document.getElementById("toggle-submissions").checked;
    const hint = document.getElementById("submissions-status-hint");
    hint.textContent = subOpen ? "Ouvertes" : "Ferm\u00e9es";
    hint.style.color = subOpen ? "var(--success)" : "var(--error)";

    const busEnabled = document.getElementById("toggle-feature-bus")?.checked ?? true;
    const busHint = document.getElementById("feature-bus-hint");
    if (busHint) {
        busHint.textContent = busEnabled ? "Activ\u00e9" : "D\u00e9sactiv\u00e9";
        busHint.style.color = busEnabled ? "var(--success)" : "var(--error)";
    }

    const displayDynamicEnabled = document.getElementById("toggle-feature-display-dynamic")?.checked ?? true;
    const displayDynamicHint = document.getElementById("feature-display-dynamic-hint");
    if (displayDynamicHint) {
        displayDynamicHint.textContent = displayDynamicEnabled ? "Activ\u00e9" : "D\u00e9sactiv\u00e9";
        displayDynamicHint.style.color = displayDynamicEnabled ? "var(--success)" : "var(--error)";
    }

    const ringtoneOn = document.getElementById("toggle-feature-ringtone-banner")?.checked ?? false;
    const ringtoneHint = document.getElementById("feature-ringtone-hint");
    if (ringtoneHint) {
        ringtoneHint.textContent = ringtoneOn ? "Activ\u00e9" : "D\u00e9sactiv\u00e9";
        ringtoneHint.style.color = ringtoneOn ? "var(--success)" : "var(--error)";
    }

    const mode = document.getElementById("display-mode-select").value;
    const modeHint = document.getElementById("display-mode-hint");
    modeHint.textContent = mode === "normal" ? "Normal" : "Attente";
    modeHint.style.color = mode === "normal" ? "var(--success)" : "var(--warning)";
}

// ==================== Traçabilité ====================

let traceSetupDone = false;
let traceCurrentData = null;

function setupTrace() {
    if (traceSetupDone) return;
    traceSetupDone = true;
    const simBtn = document.getElementById("trace-simulate-btn");
    const input = document.getElementById("trace-input");
    const ouiBtn = document.getElementById("trace-btn-oui");
    const corrigerBtn = document.getElementById("trace-btn-corriger");
    const correctionForm = document.getElementById("trace-correction-form");
    const validateBtn = document.getElementById("trace-validate-correction-btn");

    if (simBtn) simBtn.addEventListener("click", runTraceSimulation);
    if (input) input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); runTraceSimulation(); } });
    if (ouiBtn) ouiBtn.addEventListener("click", () => sendTraceFeedback(true));
    if (corrigerBtn) corrigerBtn.addEventListener("click", () => {
        correctionForm.classList.remove("hidden");
        const d = traceCurrentData?.final || traceCurrentData?.steps?.find((s) => s.step === "verify")?.result || {};
        document.getElementById("trace-correction-title").value = d.title || "";
        document.getElementById("trace-correction-category").value = d.category || "Autre";
        document.getElementById("trace-correction-keywords").value = (d.keywords || []).join(", ");
        document.getElementById("trace-correction-location").value = d.location_name || "";
    });
    if (validateBtn) validateBtn.addEventListener("click", () => sendTraceFeedback(false));
}

async function runTraceSimulation() {
    const input = document.getElementById("trace-input");
    const container = document.getElementById("trace-flow-container");
    const simBtn = document.getElementById("trace-simulate-btn");
    const text = (input?.value || "").trim();
    if (!text || text.length < 5) {
        alert("Entrez une suggestion d'au moins 5 caractères.");
        return;
    }

    simBtn.disabled = true;
    simBtn.textContent = "Simulation...";
    container.classList.remove("hidden");

    // Reset schema
    document.querySelectorAll(".trace-step").forEach((el) => el.classList.remove("trace-step-visible"));
    document.getElementById("trace-original-content").textContent = text;
    document.getElementById("trace-step-original").classList.add("trace-step-visible");
    document.getElementById("trace-main-content").textContent = "";
    document.getElementById("trace-verify-content").textContent = "";
    document.getElementById("trace-final-content").textContent = "";
    document.getElementById("trace-feedback-zone").classList.add("hidden");
    document.getElementById("trace-correction-form").classList.add("hidden");
    document.getElementById("trace-arrow-1").textContent = "⏳ Réflexion...";
    document.getElementById("trace-arrow-1").classList.add("trace-arrow-active");
    document.getElementById("trace-arrow-2").textContent = "⏳ Réflexion...";
    document.getElementById("trace-arrow-2").classList.remove("trace-arrow-active");

    try {
        const { data, status } = await API.post("/api/admin/trace/simulate", { text });
        if (status !== 200) {
            alert(data.error || "Erreur lors de la simulation");
            return;
        }

        traceCurrentData = data;
        const mainResult = data.steps?.find((s) => s.step === "main")?.result || {};
        const verifyResult = data.steps?.find((s) => s.step === "verify")?.result || {};
        const final = data.final || {};

        // Animate: show main after 1.5s
        await sleep(1500);
        document.getElementById("trace-arrow-1").classList.remove("trace-arrow-active");
        document.getElementById("trace-arrow-1").textContent = "→";
        document.getElementById("trace-main-content").textContent = formatTraceResult(mainResult);
        document.getElementById("trace-step-main").classList.add("trace-step-visible");

        // Animate: show verify after 1.5s
        document.getElementById("trace-arrow-2").classList.add("trace-arrow-active");
        await sleep(1500);
        document.getElementById("trace-arrow-2").classList.remove("trace-arrow-active");
        document.getElementById("trace-arrow-2").textContent = "→";
        document.getElementById("trace-verify-content").textContent = formatTraceResult(verifyResult);
        document.getElementById("trace-step-verify").classList.add("trace-step-visible");

        // Show final
        document.getElementById("trace-final-content").textContent = formatTraceResult(final);
        document.getElementById("trace-step-final").classList.add("trace-step-visible");

        document.getElementById("trace-feedback-zone").classList.remove("hidden");
    } catch (e) {
        alert("Erreur : " + (e.message || e));
    }
    simBtn.disabled = false;
    simBtn.textContent = "Lancer la simulation";
}

function formatTraceResult(r) {
    if (!r) return "—";
    const parts = [];
    if (r.title) parts.push("Titre : " + r.title);
    if (r.category) parts.push("Catégorie : " + r.category);
    if (r.keywords?.length) parts.push("Mots-clés : " + r.keywords.join(", "));
    if (r.location_name) parts.push("Lieu : " + r.location_name);
    return parts.length ? parts.join("\n") : "—";
}

function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

async function sendTraceFeedback(validated) {
    const d = traceCurrentData;
    if (!d) return;

    let correction = {};
    if (!validated) {
        const title = document.getElementById("trace-correction-title").value?.trim();
        if (!title || title.length < 3) {
            alert("Le titre corrigé doit faire au moins 3 caractères.");
            return;
        }
        const kw = document.getElementById("trace-correction-keywords").value;
        correction = {
            title,
            category: document.getElementById("trace-correction-category").value || "Autre",
            keywords: kw ? kw.split(",").map((k) => k.trim()).filter(Boolean) : [],
            location: document.getElementById("trace-correction-location").value?.trim() || "",
        };
    }

    try {
        const mainResult = d.steps?.find((s) => s.step === "main")?.result || {};
        const verifyResult = d.steps?.find((s) => s.step === "verify")?.result || {};
        await API.post("/api/admin/trace/feedback", {
            original_text: d.original,
            main_result: mainResult,
            verify_result: verifyResult,
            validated: validated ? true : null,
            correction: validated ? {} : correction,
        });
        const zone = document.getElementById("trace-feedback-zone");
        zone.innerHTML = "<p class=\"trace-feedback-success\">Merci ! Votre retour a été enregistré.</p>";
        setTimeout(() => {
            zone.classList.add("hidden");
            document.getElementById("trace-input").value = "";
        }, 2000);
    } catch (e) {
        alert("Erreur : " + (e.message || e));
    }
}

// ==================== Calibration IA vérification ====================

let calibVerifySetupDone = false;

async function _loadCalibVerifyData() {
    try {
        const list = await API.get("/api/admin/calibration-verification");
        document.getElementById("calib-verify-count").textContent = list.length || 0;
        const container = document.getElementById("calib-verify-list");
        if (!container) return;
        if (!list.length) {
            container.innerHTML = "<p class=\"empty-msg\">Aucun exemple. Utilisez la traçabilité pour valider des corrections, ou importez un JSON.</p>";
            return;
        }
        container.innerHTML = list.map((ex) => {
            const cor = ex.correction || {};
            const title = cor.title || "—";
            const cat = cor.category || "—";
            const kw = (cor.keywords || []).join(", ") || "—";
            return `
                <div class="calib-verify-item" data-id="${ex.id}">
                    <div class="calib-verify-original">${esc(ex.original_text?.slice(0, 80) || "")}${(ex.original_text?.length || 0) > 80 ? "…" : ""}</div>
                    <div class="calib-verify-correction">Titre: ${esc(title)} | Catégorie: ${esc(cat)} | Mots-clés: ${esc(kw)}</div>
                    <div class="calib-verify-actions">
                        <button class="btn btn-sm btn-ghost calib-verify-edit" data-id="${ex.id}">Modifier</button>
                        <button class="btn btn-sm btn-ghost calib-verify-delete" data-id="${ex.id}">Supprimer</button>
                    </div>
                </div>`;
        }).join("");

        container.querySelectorAll(".calib-verify-delete").forEach((btn) => {
            btn.addEventListener("click", () => deleteCalibVerify(parseInt(btn.dataset.id)));
        });
        container.querySelectorAll(".calib-verify-edit").forEach((btn) => {
            btn.addEventListener("click", () => editCalibVerify(parseInt(btn.dataset.id), list.find((e) => e.id === parseInt(btn.dataset.id))));
        });
    } catch (e) {
        document.getElementById("calib-verify-list").innerHTML = `<p class="empty-msg">Erreur: ${esc(String(e.message || e))}</p>`;
    }
}

async function loadCalibrationVerify() {
    if (!calibVerifySetupDone) {
        calibVerifySetupDone = true;
        setupCalibrationVerify();
    }
    await _loadCalibVerifyData();
}

async function deleteCalibVerify(id) {
    if (!confirm("Supprimer cet exemple ?")) return;
    await API.delete(`/api/admin/calibration-verification/${id}`);
    loadCalibrationVerify();
}

function editCalibVerify(id, ex) {
    const cor = ex?.correction || {};
    const newTitle = prompt("Titre corrigé:", cor.title || "");
    if (newTitle === null) return;
    const newCat = prompt("Catégorie:", cor.category || "Autre");
    if (newCat === null) return;
    const newKw = prompt("Mots-clés (virgules):", (cor.keywords || []).join(", "));
    if (newKw === null) return;
    const newLoc = prompt("Lieu:", cor.location || "");
    if (newLoc === null) return;
    API.put(`/api/admin/calibration-verification/${id}`, {
        correction: {
            title: newTitle.trim(),
            category: newCat || "Autre",
            keywords: newKw ? newKw.split(",").map((k) => k.trim()).filter(Boolean) : [],
            location: newLoc?.trim() || "",
        },
    }).then(() => loadCalibrationVerify());
}

function setupCalibrationVerify() {
    const genBtn = document.getElementById("calib-verify-generate-prompt");
    const copyBtn = document.getElementById("calib-verify-prompt-copy");
    const fileInput = document.getElementById("calib-verify-file-input");
    if (genBtn) genBtn.addEventListener("click", generateCalibVerifyPrompt);
    if (copyBtn) copyBtn.addEventListener("click", () => {
        const ta = document.getElementById("calib-verify-prompt-text");
        ta.select();
        navigator.clipboard.writeText(ta.value).then(() => {
            document.getElementById("calib-verify-prompt-copy-status").textContent = "Copié !";
            setTimeout(() => { document.getElementById("calib-verify-prompt-copy-status").textContent = ""; }, 2000);
        });
    });
    if (fileInput) {
        fileInput.addEventListener("change", (e) => {
            const fn = document.getElementById("calib-verify-file-name");
            if (fn) fn.textContent = e.target.files?.[0]?.name || "";
            importCalibVerifyJson(e);
        });
    }
}

async function generateCalibVerifyPrompt() {
    const data = await API.get("/api/admin/calibration-verification/prompt");
    const zone = document.getElementById("calib-verify-prompt-output");
    zone.classList.remove("hidden");
    document.getElementById("calib-verify-prompt-text").value = data.prompt || "";
}

async function importCalibVerifyJson(e) {
    const file = e?.target?.files?.[0];
    if (!file) return;
    try {
        const text = await file.text();
        const json = JSON.parse(text);
        const arr = Array.isArray(json) ? json : (json.examples || json.data || [json]);
        const { data, status } = await API.post("/api/admin/calibration-verification/import", { examples: arr });
        if (status === 200) {
            alert(`${data.imported || 0} exemples importés.`);
            loadCalibrationVerify();
        } else alert(data.error || "Erreur");
    } catch (err) {
        alert("Fichier JSON invalide : " + (err.message || err));
    }
    e.target.value = "";
}

// ==================== LLM Resources ====================

function setupLLMResources() {
    document.getElementById("llm-save-config").addEventListener("click", saveLLMConfig);
    document.getElementById("llm-reset-credits").addEventListener("click", resetLLMCredits);
}

async function loadLLMResources() {
    try {
        const info = await API.get("/api/admin/llm-credits");
        renderLLMResources(info);
    } catch (e) { console.error(e); }
}

function renderLLMResources(info) {
    const dot = document.getElementById("llm-status-dot");
    const label = document.getElementById("llm-status-label");
    const model = document.getElementById("llm-model-name");

    if (info.available) {
        dot.className = "llm-status-dot llm-online";
        label.textContent = "Ollama connecté";
    } else {
        dot.className = "llm-status-dot llm-offline";
        label.textContent = "Ollama hors ligne";
    }
    model.textContent = info.model || "";

    document.getElementById("llm-credits-used").textContent = `${info.used} / ${info.max}`;
    document.getElementById("llm-credits-remaining").textContent = info.remaining;

    const avgMs = info.avg_call_duration_ms;
    document.getElementById("llm-avg-time").textContent = avgMs > 0 ? formatDuration(avgMs) : "—";
    document.getElementById("llm-est-suggestions").textContent = info.est_suggestions_remaining;

    const pct = info.max > 0 ? Math.min(100, Math.round((info.used / info.max) * 100)) : 0;
    document.getElementById("llm-usage-percent").textContent = `${pct}%`;
    const fill = document.getElementById("llm-usage-fill");
    fill.style.width = `${pct}%`;
    fill.className = "llm-usage-fill" + (pct >= 90 ? " llm-usage-critical" : pct >= 70 ? " llm-usage-warning" : "");

    if (info.reset_at) {
        const reset = new Date(info.reset_at);
        document.getElementById("llm-reset-info").textContent = `Réinitialisation : ${reset.toLocaleString("fr-FR")} (toutes les ${info.period_hours}h)`;
    }

    document.getElementById("llm-max-credits").value = info.max;
    document.getElementById("llm-period-hours").value = info.period_hours;

    document.getElementById("llm-est-call-time").textContent = avgMs > 0 ? formatDuration(avgMs) : "Pas encore de données";
    document.getElementById("llm-est-sugg-time").textContent = info.est_time_per_suggestion_ms > 0 ? formatDuration(info.est_time_per_suggestion_ms) : "Pas encore de données";
    document.getElementById("llm-est-sugg-count").textContent = info.est_suggestions_remaining;
    document.getElementById("llm-total-calls").textContent = info.total_calls_tracked;
}

function formatDuration(ms) {
    if (ms < 1000) return `${ms}ms`;
    const sec = ms / 1000;
    if (sec < 60) return `${sec.toFixed(1)}s`;
    return `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`;
}

async function saveLLMConfig() {
    const max = parseInt(document.getElementById("llm-max-credits").value) || 100;
    const hours = parseInt(document.getElementById("llm-period-hours").value) || 24;
    await API.put("/api/admin/llm-credits", { max_credits: max, period_hours: hours });
    const s = document.getElementById("llm-save-status");
    s.textContent = "Sauvegarde";
    setTimeout(() => s.textContent = "", 2000);
    loadLLMResources();
}

async function resetLLMCredits() {
    if (!confirm("Reinitialiser le compteur de credits a 0 ?")) return;
    await API.post("/api/admin/llm-credits/reset", {});
    loadLLMResources();
}

// ==================== Display Manager ====================

let dmPresentations = [];
let dmPages = [];
let dmMedia = [];
let dmEditingPreso = null;
let dmSelectedSlide = null;
let dmMediaCache = null;

const VIDEO_EXTS = ["mp4", "webm", "ogg"];

function isVideo(urlOrName) {
    if (!urlOrName) return false;
    const ext = urlOrName.split(".").pop().toLowerCase();
    return VIDEO_EXTS.includes(ext);
}

function setupDisplayManager() {
    document.querySelectorAll(".dm-tab").forEach(tab => {
        tab.addEventListener("click", () => {
            document.querySelectorAll(".dm-tab").forEach(t => t.classList.remove("active"));
            tab.classList.add("active");
            document.querySelectorAll(".dm-tab-content").forEach(c => c.classList.remove("active"));
            document.getElementById(`tab-${tab.dataset.dmtab}`).classList.add("active");
            if (tab.dataset.dmtab === "dm-media") loadMedia();
            if (tab.dataset.dmtab === "dm-pages") loadDisplayPages();
            if (tab.dataset.dmtab === "dm-presentations") { loadPresentations(); dmCloseEditor(); }
            if (tab.dataset.dmtab === "dm-scrap") loadScrapArticles();
            if (tab.dataset.dmtab === "dm-overview") loadOverview();
        });
    });

    document.getElementById("dm-create-preso").addEventListener("click", dmCreatePresentation);
    document.getElementById("dm-create-page").addEventListener("click", dmCreatePage);
    document.getElementById("dm-create-autonews").addEventListener("click", dmCreateAutoNewsPage);
    document.getElementById("dm-media-upload").addEventListener("change", dmUploadMedia);
    document.getElementById("dm-editor-back").addEventListener("click", dmCloseEditor);
    document.getElementById("dm-add-slide").addEventListener("click", dmAddSlide);
}

async function loadDisplayManager() {
    await loadPresentations();
}

async function dmGetMedia() {
    if (!dmMediaCache) dmMediaCache = await API.get("/api/admin/media");
    return dmMediaCache;
}

function dmInvalidateMediaCache() { dmMediaCache = null; }

// ---- Presentations ----

async function loadPresentations() {
    dmPresentations = await API.get("/api/admin/presentations");
    renderPresentations();
}

function renderPresentations() {
    const el = document.getElementById("dm-preso-list");
    if (!dmPresentations.length) {
        el.innerHTML = `<p class="empty-msg">Aucune présentation. Créez-en une pour commencer.</p>`;
        return;
    }
    el.innerHTML = dmPresentations.map(p => `
        <div class="dm-preso-card" data-id="${p.id}">
            <div class="dm-preso-info">
                <h3>${esc(p.name)}</h3>
                <div class="dm-preso-meta">
                    <span class="dm-tag">${p.slide_count} slide${p.slide_count !== 1 ? "s" : ""}</span>
                    <span class="dm-tag">${p.active ? "Active" : "Inactive"}</span>
                    ${p.pages.map(pg => `<span class="dm-tag dm-tag-link">/tv/${esc(pg.slug)}</span>`).join("")}
                </div>
            </div>
            <div class="dm-preso-actions">
                <button class="btn btn-sm btn-primary" onclick="dmOpenEditor(${p.id})">Editer</button>
                <button class="btn btn-sm btn-ghost" onclick="dmTogglePreso(${p.id}, ${!p.active})">${p.active ? "Pause" : "Activer"}</button>
                <button class="btn btn-sm btn-danger" onclick="dmDeletePreso(${p.id})">Supprimer</button>
            </div>
        </div>
    `).join("");
}

async function dmCreatePresentation() {
    const name = prompt("Nom de la présentation :");
    if (!name) return;
    await API.post("/api/admin/presentations", { name });
    await loadPresentations();
}

async function dmTogglePreso(id, active) {
    await API.put(`/api/admin/presentations/${id}`, { active });
    await loadPresentations();
}

async function dmDeletePreso(id) {
    if (!confirm("Supprimer cette présentation et toutes ses slides ?")) return;
    await API.delete(`/api/admin/presentations/${id}`);
    await loadPresentations();
}

// ---- Slide Editor ----

async function dmOpenEditor(presoId) {
    const preso = await API.get(`/api/admin/presentations/${presoId}`);
    dmEditingPreso = preso;
    dmSelectedSlide = null;
    document.getElementById("dm-preso-list").classList.add("hidden");
    document.getElementById("dm-create-preso").classList.add("hidden");
    document.getElementById("dm-slide-editor").classList.remove("hidden");
    document.getElementById("dm-editor-title").textContent = preso.name;
    const linkEl = document.getElementById("dm-editor-link");
    if (preso.pages.length) {
        linkEl.innerHTML = preso.pages.map(p => `<a href="/tv/${esc(p.slug)}" target="_blank">/tv/${esc(p.slug)}</a>`).join(", ");
    } else {
        linkEl.textContent = "";
    }
    renderSlidesList();
    renderSlideDetail();
    renderPreview();
}

function dmCloseEditor() {
    dmEditingPreso = null;
    dmSelectedSlide = null;
    document.getElementById("dm-slide-editor").classList.add("hidden");
    document.getElementById("dm-preso-list").classList.remove("hidden");
    document.getElementById("dm-create-preso").classList.remove("hidden");
    const pf = document.getElementById("dm-preview-frame");
    if (pf) pf.innerHTML = `<p class="empty-msg">Éditez une slide pour voir l'aperçu</p>`;
    loadPresentations();
}

function renderSlidesList() {
    const list = document.getElementById("dm-slides-list");
    if (!dmEditingPreso || !dmEditingPreso.slides.length) {
        list.innerHTML = `<p class="empty-msg">Aucune slide</p>`;
        return;
    }
    list.innerHTML = dmEditingPreso.slides.map((s, i) => {
        const thumb = slideThumb(s);
        return `
        <div class="dm-slide-item ${dmSelectedSlide && dmSelectedSlide.id === s.id ? "active" : ""}"
             data-id="${s.id}" draggable="true"
             ondragstart="dmDragStart(event, ${i})"
             ondragover="dmDragOver(event)"
             ondrop="dmDrop(event, ${i})"
             onclick="dmSelectSlide(${s.id})">
            <span class="dm-slide-num">${i + 1}</span>
            <span class="dm-slide-thumb">${thumb}</span>
            <div class="dm-slide-meta">
                <span class="dm-slide-label">${slideTypeLabel(s.slide_type)}</span>
                <span class="dm-slide-dur">${s.duration}s · ${transitionLabel(s.transition)}</span>
            </div>
            <button class="dm-slide-delete-mini" onclick="event.stopPropagation();dmDeleteSlide(${s.id})" title="Supprimer">✕</button>
        </div>`;
    }).join("");
}

function slideThumb(s) {
    const c = s.content || {};
    if (s.slide_type === "image" && c.url) {
        return `<img src="${esc(c.url)}" class="dm-slide-thumb-img">`;
    }
    if (s.slide_type === "video" && c.url) {
        return `<span class="dm-slide-thumb-icon">▶</span>`;
    }
    if (s.slide_type === "multi_image" && c.urls && c.urls[0]) {
        return `<img src="${esc(c.urls[0])}" class="dm-slide-thumb-img">`;
    }
    return `<span class="dm-slide-thumb-icon">${slideTypeIcon(s.slide_type)}</span>`;
}

let dmDragIdx = null;
function dmDragStart(e, idx) { dmDragIdx = idx; e.dataTransfer.effectAllowed = "move"; }
function dmDragOver(e) { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }
async function dmDrop(e, dropIdx) {
    e.preventDefault();
    if (dmDragIdx === null || dmDragIdx === dropIdx) return;
    const slides = [...dmEditingPreso.slides];
    const [moved] = slides.splice(dmDragIdx, 1);
    slides.splice(dropIdx, 0, moved);
    const order = slides.map(s => s.id);
    await API.put(`/api/admin/presentations/${dmEditingPreso.id}/reorder`, { order });
    const preso = await API.get(`/api/admin/presentations/${dmEditingPreso.id}`);
    dmEditingPreso = preso;
    renderSlidesList();
    dmDragIdx = null;
}

function dmSelectSlide(sid) {
    dmSelectedSlide = dmEditingPreso.slides.find(s => s.id === sid) || null;
    renderSlidesList();
    renderSlideDetail();
    renderPreview();
}

async function dmAddSlide() {
    if (!dmEditingPreso) return;
    const type = document.getElementById("dm-new-slide-type").value;
    const defaults = { image: { fit: "cover" }, video: { muted: true, loop: true }, text: { background_color: "#1e293b", text_color: "#ffffff", font_size: "4rem", text_align: "center" }, suggestions: { mode: "top_voted", limit: 10, background_color: "#0f172a", text_color: "#ffffff" }, multi_image: { layout: "grid", urls: [] }, autonews: {}, bus: {} };
    await API.post(`/api/admin/presentations/${dmEditingPreso.id}/slides`, { slide_type: type, content: defaults[type] || {} });
    const preso = await API.get(`/api/admin/presentations/${dmEditingPreso.id}`);
    dmEditingPreso = preso;
    dmSelectedSlide = preso.slides[preso.slides.length - 1] || null;
    renderSlidesList();
    renderSlideDetail();
    renderPreview();
}

async function dmDeleteSlide(sid) {
    if (!confirm("Supprimer cette slide ?")) return;
    await API.delete(`/api/admin/slides/${sid}`);
    const preso = await API.get(`/api/admin/presentations/${dmEditingPreso.id}`);
    dmEditingPreso = preso;
    if (dmSelectedSlide && dmSelectedSlide.id === sid) dmSelectedSlide = null;
    renderSlidesList();
    renderSlideDetail();
    renderPreview();
}

async function dmSaveSlide() {
    if (!dmSelectedSlide) return;
    const dur = parseInt(document.getElementById("dm-s-duration").value) || 10;
    const trans = document.getElementById("dm-s-transition").value;
    const content = collectSlideContent();
    await API.put(`/api/admin/slides/${dmSelectedSlide.id}`, {
        duration: dur, transition: trans, content
    });
    const preso = await API.get(`/api/admin/presentations/${dmEditingPreso.id}`);
    dmEditingPreso = preso;
    dmSelectedSlide = preso.slides.find(s => s.id === dmSelectedSlide.id) || null;
    renderSlidesList();
    const saveBtn = document.querySelector("#dm-slide-detail .btn-primary");
    if (saveBtn) { saveBtn.textContent = "Sauvegarde !"; setTimeout(() => { saveBtn.textContent = "Sauvegarder"; }, 1500); }
}

function collectSlideContent() {
    if (!dmSelectedSlide) return {};
    const type = dmSelectedSlide.slide_type;
    const val = id => { const el = document.getElementById(id); return el ? el.value : ""; };
    const chk = id => { const el = document.getElementById(id); return el ? el.checked : false; };

    if (type === "image") {
        return { url: val("dm-s-img-url"), fit: val("dm-s-img-fit"), overlay_text: val("dm-s-overlay-text"), overlay_position: val("dm-s-overlay-pos") };
    }
    if (type === "video") {
        return { url: val("dm-s-vid-url"), muted: chk("dm-s-vid-muted"), loop: chk("dm-s-vid-loop"), fit: val("dm-s-vid-fit") };
    }
    if (type === "multi_image") {
        const inputs = document.querySelectorAll(".dm-s-multi-url");
        const urls = Array.from(inputs).map(i => i.value).filter(Boolean);
        return { urls, layout: val("dm-s-layout"), gap: parseInt(val("dm-s-gap")) || 4 };
    }
    if (type === "text") {
        return {
            title: val("dm-s-title"), subtitle: val("dm-s-subtitle"), body: val("dm-s-body"),
            background_color: val("dm-s-bg"), text_color: val("dm-s-fg"),
            font_size: val("dm-s-fontsize"), text_align: val("dm-s-align"),
            background_url: val("dm-s-bg-img"),
        };
    }
    if (type === "suggestions") {
        return {
            mode: val("dm-s-sugg-mode"), limit: parseInt(val("dm-s-sugg-limit")) || 10,
            background_color: val("dm-s-sugg-bg"), text_color: val("dm-s-sugg-fg"),
        };
    }
    return {};
}

function onSlideFieldChange() { renderPreview(); }

function renderSlideDetail() {
    const el = document.getElementById("dm-slide-detail");
    if (!dmSelectedSlide) {
        el.innerHTML = `<p class="empty-msg">Sélectionnez une slide pour la modifier</p>`;
        return;
    }
    const s = dmSelectedSlide;
    const c = s.content || {};
    let typeFields = "";

    if (s.slide_type === "image") {
        typeFields = `
            <div class="form-group"><label>Image</label>
                <div id="dm-picker-image" class="dm-inline-picker" data-target="dm-s-img-url"></div>
                <input type="hidden" id="dm-s-img-url" value="${esc(c.url || "")}">
            </div>
            <div class="form-group"><label>Remplissage</label>
                <select id="dm-s-img-fit" onchange="onSlideFieldChange()">
                    <option value="cover" ${c.fit === "cover" ? "selected" : ""}>Remplir (cover)</option>
                    <option value="contain" ${c.fit === "contain" ? "selected" : ""}>Ajuster (contain)</option>
                </select>
            </div>
            <div class="form-group"><label>Texte superposé (optionnel)</label>
                <input type="text" id="dm-s-overlay-text" value="${esc(c.overlay_text || "")}" placeholder="Texte affiché sur l'image..." oninput="onSlideFieldChange()">
            </div>
            <div class="form-group"><label>Position du texte</label>
                <select id="dm-s-overlay-pos" onchange="onSlideFieldChange()">
                    <option value="bottom" ${(c.overlay_position || "bottom") === "bottom" ? "selected" : ""}>Bas</option>
                    <option value="center" ${c.overlay_position === "center" ? "selected" : ""}>Centre</option>
                    <option value="top" ${c.overlay_position === "top" ? "selected" : ""}>Haut</option>
                </select>
            </div>`;
    } else if (s.slide_type === "video") {
        typeFields = `
            <div class="form-group"><label>Vidéo</label>
                <div id="dm-picker-video" class="dm-inline-picker" data-target="dm-s-vid-url" data-filter="video"></div>
                <input type="hidden" id="dm-s-vid-url" value="${esc(c.url || "")}">
            </div>
            <div class="form-row">
                <div class="form-group"><label><input type="checkbox" id="dm-s-vid-muted" ${c.muted !== false ? "checked" : ""}> Muet</label></div>
                <div class="form-group"><label><input type="checkbox" id="dm-s-vid-loop" ${c.loop !== false ? "checked" : ""}> Boucle</label></div>
            </div>
            <div class="form-group"><label>Remplissage</label>
                <select id="dm-s-vid-fit" onchange="onSlideFieldChange()">
                    <option value="cover" ${(c.fit || "cover") === "cover" ? "selected" : ""}>Remplir (cover)</option>
                    <option value="contain" ${c.fit === "contain" ? "selected" : ""}>Ajuster (contain)</option>
                </select>
            </div>`;
    } else if (s.slide_type === "multi_image") {
        typeFields = `
            <div class="form-group"><label>Images</label>
                <div id="dm-picker-multi" class="dm-inline-picker-multi" data-target="dm-s-multi-container"></div>
                <div id="dm-s-multi-container"></div>
            </div>
            <div class="form-row">
                <div class="form-group"><label>Disposition</label>
                    <select id="dm-s-layout" onchange="onSlideFieldChange()">
                        <option value="grid" ${c.layout === "grid" ? "selected" : ""}>Grille</option>
                        <option value="row" ${c.layout === "row" ? "selected" : ""}>Ligne</option>
                        <option value="col" ${c.layout === "col" ? "selected" : ""}>Colonne</option>
                    </select>
                </div>
                <div class="form-group"><label>Espacement (px)</label>
                    <input type="number" id="dm-s-gap" value="${c.gap || 4}" min="0" max="32" onchange="onSlideFieldChange()">
                </div>
            </div>`;
    } else if (s.slide_type === "text") {
        typeFields = `
            <div class="form-group"><label>Titre</label><input type="text" id="dm-s-title" value="${esc(c.title || "")}" oninput="onSlideFieldChange()"></div>
            <div class="form-group"><label>Sous-titre</label><input type="text" id="dm-s-subtitle" value="${esc(c.subtitle || "")}" oninput="onSlideFieldChange()"></div>
            <div class="form-group"><label>Contenu</label><textarea id="dm-s-body" rows="3" oninput="onSlideFieldChange()">${esc(c.body || "")}</textarea></div>
            <div class="form-row">
                <div class="form-group"><label>Fond</label><input type="color" id="dm-s-bg" value="${c.background_color || "#1e293b"}" oninput="onSlideFieldChange()"></div>
                <div class="form-group"><label>Texte</label><input type="color" id="dm-s-fg" value="${c.text_color || "#ffffff"}" oninput="onSlideFieldChange()"></div>
            </div>
            <div class="form-row">
                <div class="form-group"><label>Taille titre</label>
                    <select id="dm-s-fontsize" onchange="onSlideFieldChange()">
                        <option value="2rem" ${c.font_size === "2rem" ? "selected" : ""}>Petit</option>
                        <option value="3rem" ${c.font_size === "3rem" ? "selected" : ""}>Moyen</option>
                        <option value="4rem" ${(!c.font_size || c.font_size === "4rem") ? "selected" : ""}>Grand</option>
                        <option value="5rem" ${c.font_size === "5rem" ? "selected" : ""}>Très grand</option>
                    </select>
                </div>
                <div class="form-group"><label>Alignement</label>
                    <select id="dm-s-align" onchange="onSlideFieldChange()">
                        <option value="center" ${(c.text_align || "center") === "center" ? "selected" : ""}>Centre</option>
                        <option value="left" ${c.text_align === "left" ? "selected" : ""}>Gauche</option>
                        <option value="right" ${c.text_align === "right" ? "selected" : ""}>Droite</option>
                    </select>
                </div>
            </div>
            <div class="form-group"><label>Image de fond (optionnel)</label>
                <div id="dm-picker-bgimg" class="dm-inline-picker" data-target="dm-s-bg-img"></div>
                <input type="hidden" id="dm-s-bg-img" value="${esc(c.background_url || "")}">
            </div>`;
    } else if (s.slide_type === "suggestions") {
        typeFields = `
            <div class="form-row">
                <div class="form-group"><label>Mode</label>
                    <select id="dm-s-sugg-mode" onchange="onSlideFieldChange()">
                        <option value="top_voted" ${c.mode === "top_voted" ? "selected" : ""}>Plus votées</option>
                        <option value="recent" ${c.mode === "recent" ? "selected" : ""}>Récentes</option>
                    </select>
                </div>
                <div class="form-group"><label>Limite</label><input type="number" id="dm-s-sugg-limit" value="${c.limit || 10}" min="1" max="50" onchange="onSlideFieldChange()"></div>
            </div>
            <div class="form-row">
                <div class="form-group"><label>Fond</label><input type="color" id="dm-s-sugg-bg" value="${c.background_color || "#0f172a"}" oninput="onSlideFieldChange()"></div>
                <div class="form-group"><label>Texte</label><input type="color" id="dm-s-sugg-fg" value="${c.text_color || "#ffffff"}" oninput="onSlideFieldChange()"></div>
            </div>`;
    } else if (s.slide_type === "autonews") {
        typeFields = `<p class="dm-autonews-hint">Affiche un article scrapé depuis e-lyco. Aucune configuration nécessaire.</p>`;
    }

    el.innerHTML = `
        <div class="dm-detail-header">
            <span>${slideTypeLabel(s.slide_type)}</span>
            <button class="btn btn-sm btn-danger" onclick="dmDeleteSlide(${s.id})">Supprimer</button>
        </div>
        <div class="form-row">
            <div class="form-group"><label>Durée d'affichage (sec)</label><input type="number" id="dm-s-duration" value="${s.duration}" min="1" max="300" title="Temps d'attente sur la slide avant passage à la suivante"></div>
            <div class="form-group"><label>Transition</label>
                <select id="dm-s-transition">
                    <option value="fade" ${s.transition === "fade" ? "selected" : ""}>Fondu</option>
                    <option value="slide" ${s.transition === "slide" ? "selected" : ""}>Glissement</option>
                    <option value="zoom" ${s.transition === "zoom" ? "selected" : ""}>Zoom</option>
                    <option value="cut" ${s.transition === "cut" ? "selected" : ""}>Coupe directe</option>
                    <option value="appear" ${s.transition === "appear" ? "selected" : ""}>Apparition</option>
                </select>
            </div>
        </div>
        ${typeFields}
        <button class="btn btn-primary" onclick="dmSaveSlide()">Sauvegarder</button>
    `;

    initInlinePickers();
}

// ---- Inline Visual Pickers ----

async function initInlinePickers() {
    const media = await dmGetMedia();
    document.querySelectorAll(".dm-inline-picker").forEach(picker => {
        const targetId = picker.dataset.target;
        const filter = picker.dataset.filter;
        const currentVal = document.getElementById(targetId)?.value || "";
        let items = media;
        if (filter === "video") {
            items = media.filter(m => isVideo(m.filename));
        } else {
            items = media.filter(m => !isVideo(m.filename));
        }
        renderInlinePicker(picker, items, targetId, currentVal);
    });

    const multiPicker = document.getElementById("dm-picker-multi");
    if (multiPicker) {
        const imgs = media.filter(m => !isVideo(m.filename));
        const container = document.getElementById("dm-s-multi-container");
        const currentUrls = (dmSelectedSlide?.content?.urls) || [];
        renderMultiPicker(multiPicker, imgs, container, currentUrls);
    }
}

function renderInlinePicker(picker, items, targetId, currentVal) {
    if (!items.length) {
        picker.innerHTML = `<p class="dm-picker-empty">Aucun fichier disponible. Uploadez-en dans la médiathèque.</p>`;
        return;
    }
    picker.innerHTML = `<div class="dm-picker-grid">${items.map(m => {
        const sel = m.url === currentVal ? "dm-picker-selected" : "";
        if (isVideo(m.filename)) {
            return `<div class="dm-picker-thumb ${sel}" data-url="${esc(m.url)}" title="${esc(m.original_name)}"><div class="dm-picker-vid-icon">▶</div><span class="dm-picker-name">${esc(m.original_name)}</span></div>`;
        }
        return `<div class="dm-picker-thumb ${sel}" data-url="${esc(m.url)}" title="${esc(m.original_name)}"><img src="${esc(m.url)}"><span class="dm-picker-name">${esc(m.original_name)}</span></div>`;
    }).join("")}</div>`;

    picker.querySelectorAll(".dm-picker-thumb").forEach(thumb => {
        thumb.addEventListener("click", () => {
            picker.querySelectorAll(".dm-picker-thumb").forEach(t => t.classList.remove("dm-picker-selected"));
            thumb.classList.add("dm-picker-selected");
            const input = document.getElementById(targetId);
            if (input) { input.value = thumb.dataset.url; onSlideFieldChange(); }
        });
    });
}

function renderMultiPicker(picker, items, container, currentUrls) {
    if (!items.length) {
        picker.innerHTML = `<p class="dm-picker-empty">Aucune image disponible.</p>`;
        return;
    }
    picker.innerHTML = `<p class="dm-picker-hint">Cliquez pour ajouter/retirer des images :</p><div class="dm-picker-grid">${items.map(m => {
        const sel = currentUrls.includes(m.url) ? "dm-picker-selected" : "";
        return `<div class="dm-picker-thumb ${sel}" data-url="${esc(m.url)}"><img src="${esc(m.url)}"><span class="dm-picker-name">${esc(m.original_name)}</span></div>`;
    }).join("")}</div>`;

    container.innerHTML = currentUrls.map(u => `<input type="hidden" class="dm-s-multi-url" value="${esc(u)}">`).join("");

    picker.querySelectorAll(".dm-picker-thumb").forEach(thumb => {
        thumb.addEventListener("click", () => {
            thumb.classList.toggle("dm-picker-selected");
            const selected = Array.from(picker.querySelectorAll(".dm-picker-selected")).map(t => t.dataset.url);
            container.innerHTML = selected.map(u => `<input type="hidden" class="dm-s-multi-url" value="${esc(u)}">`).join("");
            onSlideFieldChange();
        });
    });
}

// ---- Live Preview ----

function renderPreview() {
    const frame = document.getElementById("dm-preview-frame");
    if (!frame) return;
    if (!dmSelectedSlide) {
        frame.innerHTML = `<p class="empty-msg">Éditez une slide pour voir l'aperçu</p>`;
        return;
    }
    const content = collectSlideContent();
    const type = dmSelectedSlide.slide_type;

    if (type === "image") {
        const url = content.url || "";
        const fit = content.fit || "cover";
        const overlay = content.overlay_text || "";
        const opos = content.overlay_position || "bottom";
        if (!url) { frame.innerHTML = `<div class="dm-pv-empty">Sélectionnez une image</div>`; return; }
        frame.innerHTML = `<div class="dm-pv-img" style="background-image:url('${esc(url)}');background-size:${fit};background-position:center;background-repeat:no-repeat;">
            ${overlay ? `<div class="dm-pv-overlay dm-pv-overlay-${opos}">${esc(overlay)}</div>` : ""}
        </div>`;
    } else if (type === "video") {
        const url = content.url || "";
        if (!url) { frame.innerHTML = `<div class="dm-pv-empty">Sélectionnez une vidéo</div>`; return; }
        const fit = content.fit || "cover";
        frame.innerHTML = `<video class="dm-pv-video" style="object-fit:${fit}" src="${esc(url)}" autoplay muted loop playsinline></video>`;
    } else if (type === "multi_image") {
        const urls = content.urls || [];
        const gap = content.gap || 4;
        if (!urls.length) { frame.innerHTML = `<div class="dm-pv-empty">Sélectionnez des images</div>`; return; }
        const layout = content.layout || "grid";
        let cls = "dm-pv-multi-grid";
        if (layout === "row") cls = "dm-pv-multi-row";
        else if (layout === "col") cls = "dm-pv-multi-col";
        frame.innerHTML = `<div class="dm-pv-multi ${cls}" style="gap:${gap}px">${urls.map(u =>
            `<div class="dm-pv-multi-img" style="background-image:url('${esc(u)}')"></div>`
        ).join("")}</div>`;
    } else if (type === "text") {
        const bg = content.background_color || "#1e293b";
        const fg = content.text_color || "#ffffff";
        const sz = content.font_size || "4rem";
        const al = content.text_align || "center";
        const bgImg = content.background_url ? `background-image:url('${esc(content.background_url)}');background-size:cover;` : "";
        const scale = 0.25;
        frame.innerHTML = `<div class="dm-pv-text" style="background:${bg};color:${fg};text-align:${al};${bgImg}">
            <div class="dm-pv-text-inner">
                ${content.title ? `<div style="font-size:calc(${sz} * ${scale});font-weight:800;line-height:1.1;margin-bottom:4px">${esc(content.title)}</div>` : ""}
                ${content.subtitle ? `<div style="font-size:calc(${sz} * ${scale} * 0.55);opacity:0.75;margin-bottom:4px">${esc(content.subtitle)}</div>` : ""}
                ${content.body ? `<div style="font-size:calc(${sz} * ${scale} * 0.4);opacity:0.6;line-height:1.4">${esc(content.body)}</div>` : ""}
            </div>
        </div>`;
    } else if (type === "suggestions") {
        const bg = content.background_color || "#0f172a";
        const fg = content.text_color || "#ffffff";
        frame.innerHTML = `<div class="dm-pv-sugg" style="background:${bg};color:${fg}">
            <div class="dm-pv-sugg-header">Suggestions (${content.mode === "recent" ? "recentes" : "top votees"})</div>
            <div class="dm-pv-sugg-cards">
                ${Array.from({length: Math.min(content.limit || 4, 4)}, (_, i) => `<div class="dm-pv-sugg-card" style="border-color:${fg}20"><span>Suggestion ${i + 1}</span><span style="color:#f472b6">♥ ${10 - i * 2}</span></div>`).join("")}
            </div>
        </div>`;
    } else if (type === "autonews") {
        frame.innerHTML = `<div class="dm-pv-autonews">
            <div class="dm-pv-autonews-icon">📰</div>
            <div class="dm-pv-autonews-label">AutoNews</div>
            <div class="dm-pv-autonews-hint">Article e-lyco</div>
        </div>`;
    } else if (type === "bus") {
        frame.innerHTML = `<div class="dm-pv-autonews">
            <div class="dm-pv-autonews-icon">🚌</div>
            <div class="dm-pv-autonews-label">Horaires bus</div>
            <div class="dm-pv-autonews-hint">Départs Mecatran</div>
        </div>`;
    } else {
        frame.innerHTML = `<div class="dm-pv-empty">Aperçu non disponible</div>`;
    }
}

function slideTypeIcon(t) {
    return { image: "IMG", multi_image: "MULTI", video: "VID", text: "TXT", suggestions: "SUG", autonews: "NEWS", bus: "BUS", custom: "CUSTOM" }[t] || "?";
}
function slideTypeLabel(t) {
    return { image: "Image", multi_image: "Multi-images", video: "Vidéo", text: "Texte", suggestions: "Suggestions", autonews: "AutoNews", bus: "Horaires bus", custom: "Personnalisé" }[t] || t;
}
function transitionLabel(t) {
    return { fade: "Fondu", slide: "Glissement", zoom: "Zoom", cut: "Coupe", appear: "Apparition" }[t] || t;
}

// ---- Display Pages ----

async function loadDisplayPages() {
    dmPages = await API.get("/api/admin/display-pages");
    dmPresentations = await API.get("/api/admin/presentations");
    renderDisplayPages();
}

function renderDisplayPages() {
    const el = document.getElementById("dm-page-list");
    if (!dmPages.length) {
        el.innerHTML = `<p class="empty-msg">Aucune page d'affichage.</p>`;
        return;
    }
    el.innerHTML = dmPages.map(p => `
        <div class="dm-page-card">
            <div class="dm-page-info">
                <h3>${esc(p.name)}</h3>
                <span class="dm-tag dm-tag-link"><a href="/tv/${esc(p.slug)}" target="_blank">/tv/${esc(p.slug)}</a></span>
                <span class="dm-tag">${(p.page_type || "presentation") === "autonews" ? "AutoNews" : (p.presentation_name ? "→ " + esc(p.presentation_name) : "Non liée")}</span>
            </div>
            <div class="dm-page-actions">
                ${(p.page_type || "presentation") !== "autonews" ? `<select class="control-select" onchange="dmLinkPage(${p.id}, this.value)">
                    <option value="">— Aucune présentation —</option>
                    ${dmPresentations.map(pr => `<option value="${pr.id}" ${p.presentation_id === pr.id ? "selected" : ""}>${esc(pr.name)}</option>`).join("")}
                </select>` : ""}
                <button class="btn btn-sm btn-danger" onclick="dmDeletePage(${p.id})">Supprimer</button>
            </div>
        </div>
    `).join("");
}

async function dmCreatePage() {
    const name = prompt("Nom de la page (ex: Hall, CDI, Cantine) :");
    if (!name) return;
    const slug = prompt("Slug URL (ex: hall, cdi) :", name.toLowerCase().replace(/[^a-z0-9]+/g, "-"));
    if (!slug) return;
    await API.post("/api/admin/display-pages", { name, slug });
    await loadDisplayPages();
}

async function dmCreateAutoNewsPage() {
    const existing = dmPages.find(p => (p.page_type || p.slug) === "autonews");
    if (existing) {
        alert("Une page AutoNews existe déjà : /tv/" + existing.slug);
        return;
    }
    await API.post("/api/admin/display-pages", { name: "AutoNews", slug: "autonews", page_type: "autonews" });
    await loadDisplayPages();
}

async function dmLinkPage(pageId, presoId) {
    await API.put(`/api/admin/display-pages/${pageId}`, { presentation_id: presoId ? parseInt(presoId) : null });
    await loadDisplayPages();
}

async function dmDeletePage(id) {
    if (!confirm("Supprimer cette page d'affichage ?")) return;
    await API.delete(`/api/admin/display-pages/${id}`);
    await loadDisplayPages();
}

// ---- Scrap AutoNews ----

async function loadScrapArticles() {
    try {
        const [articles, batchesData] = await Promise.all([
            API.get("/api/admin/scraped-news"),
            API.get("/api/admin/scraped-news/batches"),
        ]);
        renderScrapBatches(batchesData);
        renderScrapList(articles);
        setupScrapControls();
    } catch (e) {
        document.getElementById("dm-scrap-list").innerHTML = `<p class="empty-msg">Erreur de chargement.</p>`;
    }
}

function renderScrapBatches(data) {
    const sel = document.getElementById("dm-scrap-current-batch");
    if (!sel) return;
    const batches = data.batches || [];
    const current = data.current_batch || "";
    sel.innerHTML = `<option value="">— Tous les scraps —</option>` + batches.map(b => {
        const bid = b.batch_id || "legacy";
        const label = bid === "legacy" ? "Ancien (legacy)" : bid;
        return `<option value="${esc(bid)}" ${bid === current ? "selected" : ""}>${esc(label)} (${b.count} article${b.count !== 1 ? "s" : ""})</option>`;
    }).join("");
}

function setupScrapControls() {
    const setBtn = document.getElementById("dm-scrap-set-current");
    const runBtn = document.getElementById("dm-scrap-run");
    const sel = document.getElementById("dm-scrap-current-batch");
    if (setBtn && sel) {
        setBtn.onclick = async () => {
            const batchId = sel.value || "";
            await API.put("/api/admin/scraped-news/current-batch", { batch_id: batchId || null });
            loadScrapArticles();
        };
    }
    if (runBtn) {
        runBtn.onclick = async () => {
            if (!confirm("Lancer un nouveau scrap e-lyco maintenant ? Les nouveaux articles remplaceront le scrap actuel.")) return;
            runBtn.disabled = true;
            runBtn.textContent = "Scrap en cours...";
            await API.post("/api/admin/scraped-news/run-scrape");
            setTimeout(() => { runBtn.disabled = false; runBtn.textContent = "Lancer un nouveau scrap"; loadScrapArticles(); }, 3000);
        };
    }
}

function renderScrapList(articles) {
    const el = document.getElementById("dm-scrap-list");
    if (!articles.length) {
        el.innerHTML = `<p class="empty-msg">Aucun article scrapé. Cliquez sur « Lancer un nouveau scrap » ou attendez le scrap automatique.</p>`;
        return;
    }
    el.innerHTML = articles.map(a => {
        const img = a.image_url ? `<img src="${esc(a.image_url)}" alt="" class="dm-scrap-thumb">` : `<div class="dm-scrap-thumb dm-scrap-no-img">Sans image</div>`;
        const batchTag = a.batch_id && a.batch_id !== "legacy" ? `<span class="dm-scrap-batch-tag">${esc(a.batch_id)}</span>` : "";
        return `
        <div class="dm-scrap-card" data-id="${a.id}" data-batch="${esc(a.batch_id || "legacy")}">
            <div class="dm-scrap-preview">${img}${batchTag}</div>
            <div class="dm-scrap-body">
                <div class="dm-scrap-fields">
                    <div class="form-group"><label>Titre</label><input type="text" class="dm-scrap-title" value="${esc(a.title)}" data-id="${a.id}"></div>
                    <div class="form-group"><label>Sous-titre (excerpt)</label><input type="text" class="dm-scrap-excerpt" value="${esc(a.excerpt || "")}" data-id="${a.id}" placeholder="Court résumé..."></div>
                    <div class="form-group"><label>Texte (résumé affiché)</label><textarea class="dm-scrap-summary" rows="3" data-id="${a.id}" placeholder="Résumé pour l'affichage...">${esc(a.summary || a.excerpt || "")}</textarea></div>
                    <div class="form-group"><label>URL image</label><input type="text" class="dm-scrap-image" value="${esc(a.image_url || "")}" data-id="${a.id}" placeholder="https://..."></div>
                </div>
                <div class="dm-scrap-card-actions">
                    <button class="btn btn-sm btn-primary dm-scrap-save" data-id="${a.id}">Sauvegarder</button>
                    <button class="btn btn-sm btn-danger dm-scrap-delete-batch" data-batch="${esc(a.batch_id || "legacy")}" title="Supprimer tout ce scrap">Supprimer ce scrap</button>
                </div>
            </div>
        </div>`;
    }).join("");

    el.querySelectorAll(".dm-scrap-save").forEach(btn => {
        btn.addEventListener("click", async () => {
            const id = parseInt(btn.dataset.id);
            const card = btn.closest(".dm-scrap-card");
            const title = card.querySelector(".dm-scrap-title").value.trim();
            const excerpt = card.querySelector(".dm-scrap-excerpt").value.trim();
            const summary = card.querySelector(".dm-scrap-summary").value.trim();
            const image_url = card.querySelector(".dm-scrap-image").value.trim();
            btn.disabled = true;
            btn.textContent = "...";
            try {
                await API.put(`/api/admin/scraped-news/${id}`, { title, excerpt, summary, image_url });
                btn.textContent = "Sauvegardé !";
            } catch (e) {
                btn.textContent = "Erreur";
            }
            setTimeout(() => { btn.textContent = "Sauvegarder"; btn.disabled = false; }, 1500);
        });
    });
    el.querySelectorAll(".dm-scrap-delete-batch").forEach(btn => {
        btn.addEventListener("click", async () => {
            const batch = btn.dataset.batch || "legacy";
            if (!confirm(`Supprimer tous les articles du scrap « ${batch} » ?`)) return;
            await API.delete(`/api/admin/scraped-news/batch/${encodeURIComponent(batch)}`);
            loadScrapArticles();
        });
    });
}

// ---- Media ----

async function loadMedia() {
    dmInvalidateMediaCache();
    dmMedia = await dmGetMedia();
    renderMedia();
}

function renderMedia() {
    const grid = document.getElementById("dm-media-grid");
    if (!dmMedia.length) {
        grid.innerHTML = `<p class="empty-msg">Aucun média uploadé.</p>`;
        return;
    }
    grid.innerHTML = dmMedia.map(m => {
        const vid = isVideo(m.filename);
        const preview = vid
            ? `<div class="dm-media-vid-thumb"><span>▶</span><video src="${m.url}" muted preload="metadata"></video></div>`
            : `<img src="${m.url}" alt="${esc(m.original_name)}">`;
        return `
        <div class="dm-media-item">
            ${preview}
            <div class="dm-media-info">
                <span class="dm-media-name">${esc(m.original_name)}</span>
                <span class="dm-media-size">${m.size > 1024 * 1024 ? (m.size / 1024 / 1024).toFixed(1) + " Mo" : (m.size / 1024).toFixed(0) + " Ko"}</span>
            </div>
            <div class="dm-media-actions">
                <button class="btn btn-sm btn-ghost" onclick="navigator.clipboard.writeText('${m.url}')" title="Copier URL">URL</button>
                <button class="btn btn-sm btn-danger" onclick="dmDeleteMedia(${m.id})" title="Supprimer">Suppr.</button>
            </div>
        </div>`;
    }).join("");
}

async function dmUploadMedia(e) {
    const files = e.target.files;
    if (!files.length) return;
    for (const file of files) {
        const fd = new FormData();
        fd.append("file", file);
        await fetch("/api/admin/media", { method: "POST", body: fd });
    }
    e.target.value = "";
    dmInvalidateMediaCache();
    await loadMedia();
}

async function dmDeleteMedia(id) {
    if (!confirm("Supprimer ce fichier ?")) return;
    await API.delete(`/api/admin/media/${id}`);
    dmInvalidateMediaCache();
    await loadMedia();
}

// ---- Overview (visual relationships) ----

async function loadOverview() {
    dmPresentations = await API.get("/api/admin/presentations");
    dmPages = await API.get("/api/admin/display-pages");
    renderOverview();
}

function renderOverview() {
    const el = document.getElementById("dm-overview-graph");
    if (!dmPresentations.length && !dmPages.length) {
        el.innerHTML = `<p class="empty-msg">Créez des présentations et des pages pour voir les relations.</p>`;
        return;
    }

    let html = `<div class="dm-ov-section"><h3>Pages d'affichage</h3><div class="dm-ov-cards">`;
    if (dmPages.length) {
        html += dmPages.map(p => {
            const linked = dmPresentations.find(pr => pr.id === p.presentation_id);
            return `<div class="dm-ov-card dm-ov-page">
                <strong>${esc(p.name)}</strong>
                <span class="dm-tag dm-tag-link">/tv/${esc(p.slug)}</span>
                ${linked ? `<span class="dm-ov-arrow">→</span><span class="dm-ov-linked">${esc(linked.name)}</span>` : `<span class="dm-ov-unlinked">Non liée</span>`}
            </div>`;
        }).join("");
    } else {
        html += `<p class="empty-msg">Aucune page</p>`;
    }
    html += `</div></div>`;

    html += `<div class="dm-ov-section"><h3>Presentations</h3><div class="dm-ov-cards">`;
    if (dmPresentations.length) {
        html += dmPresentations.map(p => {
            const linkedPages = dmPages.filter(pg => pg.presentation_id === p.id);
            return `<div class="dm-ov-card dm-ov-preso">
                <strong>${esc(p.name)}</strong>
                <span class="dm-tag">${p.slide_count} slides</span>
                <span class="dm-tag">${p.active ? "Active" : "Inactive"}</span>
                ${linkedPages.length ? linkedPages.map(pg => `<span class="dm-tag dm-tag-link">/tv/${esc(pg.slug)}</span>`).join("") : `<span class="dm-ov-unlinked">Aucune page liée</span>`}
            </div>`;
        }).join("");
    } else {
        html += `<p class="empty-msg">Aucune présentation</p>`;
    }
    html += `</div></div>`;

    el.innerHTML = html;
}

// ==================== Sondage musique ====================

let musicPollList = [];
let musicPollCurrentId = null;

function isoToDatetimeLocal(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function datetimeLocalToIso(val) {
    if (!val || !String(val).trim()) return null;
    const d = new Date(val);
    if (Number.isNaN(d.getTime())) return null;
    return d.toISOString();
}

/** Préécoute : proxy admin (Spotify p.scdn.co + extraits Deezer *.dzcdn.net). */
function spotifyPreviewSrcForAdmin(url) {
    if (!url || typeof url !== "string") return "";
    const u = url.trim();
    if (!u.startsWith("https://")) return u;
    if (u.startsWith("https://p.scdn.co/") || u.includes(".dzcdn.net/")) {
        return `/api/admin/spotify/preview-audio?url=${encodeURIComponent(u)}`;
    }
    return u;
}

let musicPollTabsSetupDone = false;

async function setRingtoneBannerEnabledFromUi(checked) {
    await API.put("/api/admin/settings", { feature_ringtone_banner_enabled: checked ? "true" : "false" });
    const t = document.getElementById("toggle-feature-ringtone-banner");
    const h = document.getElementById("hub-toggle-ringtone-banner");
    const r = document.getElementById("ringtone-banner-enabled");
    if (t) t.checked = checked;
    if (h) h.checked = checked;
    if (r) r.checked = checked;
    updateSettingsHints();
}

function setupMusicPollTabs() {
    if (musicPollTabsSetupDone) return;
    musicPollTabsSetupDone = true;
    document.querySelectorAll("[data-music-poll-tab]").forEach((btn) => {
        btn.addEventListener("click", () => switchMusicPollTab(btn.getAttribute("data-music-poll-tab")));
    });
}

function switchMusicPollTab(tab) {
    const editor = document.getElementById("music-poll-tab-panel-editor");
    const results = document.getElementById("music-poll-tab-panel-results");
    const be = document.getElementById("music-poll-tab-btn-editor");
    const br = document.getElementById("music-poll-tab-btn-results");
    if (!editor || !results) return;
    const isEditor = tab === "editor";
    editor.classList.toggle("hidden", !isEditor);
    results.classList.toggle("hidden", isEditor);
    if (be) {
        be.classList.toggle("music-poll-tab-btn--active", isEditor);
        be.setAttribute("aria-selected", isEditor ? "true" : "false");
    }
    if (br) {
        br.classList.toggle("music-poll-tab-btn--active", !isEditor);
        br.setAttribute("aria-selected", !isEditor ? "true" : "false");
    }
    if (!isEditor) refreshRingtoneAdminPanel();
}

async function refreshRingtoneAdminPanel() {
    const disp = document.getElementById("ringtone-current-display");
    const empty = document.getElementById("ringtone-active-poll-empty");
    const resultsEl = document.getElementById("ringtone-poll-results-clickable");
    try {
        const data = await API.get("/api/admin/ringtone");
        const enabled = !!data.enabled;
        const rb = document.getElementById("ringtone-banner-enabled");
        const tf = document.getElementById("toggle-feature-ringtone-banner");
        const hub = document.getElementById("hub-toggle-ringtone-banner");
        if (rb) rb.checked = enabled;
        if (tf) tf.checked = enabled;
        if (hub) hub.checked = enabled;
        updateSettingsHints();
        const sel = data.selection;
        if (disp) {
            if (!sel || !sel.title) {
                disp.innerHTML = `<p class="empty-msg">Aucun morceau sélectionné.</p>`;
            } else {
                const th = sel.thumbnail_url
                    ? `<img class="ringtone-admin-thumb" src="${esc(sel.thumbnail_url)}" alt="" width="72" height="72" loading="lazy">`
                    : `<div class="ringtone-admin-thumb ringtone-admin-thumb--ph" aria-hidden="true">♪</div>`;
                const src = sel.source === "poll" ? "Sondage" : "Spotify";
                disp.innerHTML = `<div class="ringtone-current-row">${th}<div><strong>${esc(sel.title)}</strong><br><span>${esc(
                    sel.artist || "",
                )}</span><br><span class="context-hint">Source : ${src}</span></div></div>`;
            }
        }
        const ap = data.active_poll;
        if (!ap) {
            if (empty) empty.classList.remove("hidden");
            if (resultsEl) resultsEl.innerHTML = "";
            return;
        }
        if (empty) empty.classList.add("hidden");
        if (!resultsEl) return;
        if (!ap.tracks || !ap.tracks.length) {
            resultsEl.innerHTML = `<p class="empty-msg">Aucun morceau dans le sondage actif.</p>`;
            return;
        }
        const total = ap.total_votes || 0;
        let html = `<p class="music-poll-results-total">Sondage « ${esc(ap.title)} » — total des votes : <strong>${total}</strong></p>`;
        html += `<div class="music-poll-results-bars">`;
        ap.tracks.forEach((row, i) => {
            const pct = total ? Math.round((100 * row.vote_count) / total) : 0;
            const isSel = sel && sel.source === "poll" && sel.poll_id === ap.id && sel.track_id === row.id;
            html += `<div class="music-poll-result-row music-poll-result-row--selectable${
                isSel ? " music-poll-result-row--selected" : ""
            }" data-poll-id="${ap.id}" data-track-id="${row.id}" role="button" tabindex="0">
  <div class="music-poll-result-head">
    <span class="music-poll-result-label">${i + 1}. ${esc(row.title)} — ${esc(row.artist)}</span>
    <span class="music-poll-result-num">${row.vote_count} vote(s) · ${pct}%</span>
  </div>
  <div class="music-poll-result-bar-wrap">
    <div class="music-poll-result-bar" style="width:${pct}%"></div>
  </div>
</div>`;
        });
        html += `</div>`;
        resultsEl.innerHTML = html;
    } catch (e) {
        console.error(e);
        if (resultsEl) resultsEl.innerHTML = `<p class="empty-msg">Impossible de charger les résultats.</p>`;
    }
}

function setupRingtoneAdminPanel() {
    document.getElementById("ringtone-apply-spotify-btn")?.addEventListener("click", async () => {
        const url = document.getElementById("ringtone-spotify-url")?.value?.trim() || "";
        const st = document.getElementById("ringtone-save-status");
        if (!url) {
            if (st) st.textContent = "Collez un lien Spotify.";
            return;
        }
        if (st) st.textContent = "Enregistrement…";
        const { data, status } = await API.put("/api/admin/ringtone", { selection: { source: "manual", spotify_url: url } });
        if (status >= 400) {
            if (st) st.textContent = data.error || "Erreur";
            return;
        }
        if (st) st.textContent = "\u2713 Enregistré";
        setTimeout(() => {
            if (st) st.textContent = "";
        }, 2500);
        await refreshRingtoneAdminPanel();
    });
    document.getElementById("ringtone-clear-btn")?.addEventListener("click", async () => {
        const { data, status } = await API.put("/api/admin/ringtone", { clear_selection: true });
        if (status >= 400) {
            alert(data.error || "Erreur");
            return;
        }
        const inp = document.getElementById("ringtone-spotify-url");
        if (inp) inp.value = "";
        await refreshRingtoneAdminPanel();
    });
    const ringtoneResultsEl = document.getElementById("ringtone-poll-results-clickable");
    const applyRingtoneRowSelection = async (row) => {
        if (!row) return;
        const pollId = parseInt(row.dataset.pollId, 10);
        const trackId = parseInt(row.dataset.trackId, 10);
        const { data, status } = await API.put("/api/admin/ringtone", {
            selection: { source: "poll", poll_id: pollId, track_id: trackId },
        });
        if (status >= 400) {
            alert(data.error || "Erreur");
            return;
        }
        await refreshRingtoneAdminPanel();
    };
    ringtoneResultsEl?.addEventListener("click", async (e) => {
        const row = e.target.closest(".music-poll-result-row--selectable");
        if (!row) return;
        await applyRingtoneRowSelection(row);
    });
    ringtoneResultsEl?.addEventListener("keydown", async (e) => {
        if (e.key !== "Enter" && e.key !== " ") return;
        const row = e.target.closest(".music-poll-result-row--selectable");
        if (!row) return;
        e.preventDefault();
        await applyRingtoneRowSelection(row);
    });
    document.getElementById("ringtone-banner-enabled")?.addEventListener("change", async (e) => {
        await setRingtoneBannerEnabledFromUi(e.target.checked);
    });
}

function setupMusicPoll() {
    setupMusicPollTabs();
    setupRingtoneAdminPanel();
    document.getElementById("music-poll-selector").addEventListener("change", onMusicPollSelectorChange);
    document.getElementById("music-poll-new-btn").addEventListener("click", newMusicPollForm);
    document.getElementById("music-poll-save-btn").addEventListener("click", saveMusicPoll);
    document.getElementById("music-poll-publish-btn")?.addEventListener("click", publishMusicPoll);
    document.getElementById("music-poll-delete-btn").addEventListener("click", deleteMusicPoll);
    document.getElementById("music-poll-add-track-btn").addEventListener("click", addMusicPollTrack);
    document.getElementById("music-poll-refresh-preview-btn")?.addEventListener("click", refreshMusicPollPreview);
    document.getElementById("music-poll-test-playlist-btn")?.addEventListener("click", testMusicPollPlaylist);
}

async function refreshMusicPollPreview() {
    const wrap = document.getElementById("music-poll-student-preview");
    if (!wrap) return;
    const editId = document.getElementById("music-poll-edit-id").value;
    window.MusicPollWidget?.stopAll?.();
    if (!editId) {
        wrap.innerHTML = `<p class="empty-msg">Enregistrez d’abord le sondage (titre + Enregistrer), puis ajoutez des morceaux.</p>`;
        return;
    }
    wrap.innerHTML = `<p class="empty-msg">Chargement…</p>`;
    try {
        const data = await API.get(`/api/admin/music-poll/${editId}/preview`);
        if (!data.active) {
            wrap.innerHTML = `<p class="empty-msg">${esc(data.message || "Aperçu indisponible.")}</p>`;
            return;
        }
        const MW = window.MusicPollWidget;
        if (!MW || !MW.buildDOM || !MW.bindPreview) {
            wrap.innerHTML = `<p class="empty-msg">Script sondage musique manquant.</p>`;
            return;
        }
        wrap.innerHTML = "";
        const el = MW.buildDOM(data.poll);
        el.classList.add("music-poll--admin-preview");
        el.id = "music-poll-admin-preview-module";
        wrap.appendChild(el);
        MW.bindPreview(el);
        el.querySelectorAll(".music-track-card").forEach((card) => {
            const u = card.dataset.previewUrl;
            if (u) card.dataset.previewUrl = spotifyPreviewSrcForAdmin(u);
        });
    } catch (e) {
        console.error(e);
        wrap.innerHTML = `<p class="empty-msg">Impossible de charger l’aperçu.</p>`;
    }
}

async function testMusicPollPlaylist() {
    const url = document.getElementById("music-poll-playlist").value.trim();
    const statusEl = document.getElementById("music-poll-playlist-test-status");
    const previewEl = document.getElementById("music-poll-playlist-preview");
    if (!statusEl || !previewEl) return;
    if (!url) {
        statusEl.textContent = "Collez d’abord un lien Spotify.";
        statusEl.style.color = "";
        previewEl.classList.add("hidden");
        previewEl.innerHTML = "";
        return;
    }
    statusEl.textContent = "Vérification…";
    statusEl.style.color = "";
    previewEl.classList.add("hidden");
    previewEl.innerHTML = "";
    try {
        const { data, status } = await API.post("/api/admin/spotify/verify-playlist", { url });
        if (status === 503) {
            statusEl.textContent = data.error || "Spotify non configuré.";
            return;
        }
        if (status >= 400 && !data.ok) {
            statusEl.textContent = data.error || "Erreur.";
            return;
        }
        if (!data.ok) {
            const detail = data.detail ? ` (${data.detail})` : "";
            statusEl.textContent = (data.error || "Échec.") + detail;
            return;
        }
        statusEl.textContent = data.message || "OK.";
        statusEl.style.color = "#15803d";
        setTimeout(() => {
            statusEl.textContent = "";
            statusEl.style.color = "";
        }, 8000);
        previewEl.classList.remove("hidden");
        const thumb = data.thumbnail_url
            ? `<img class="music-poll-playlist-preview-thumb" src="${esc(data.thumbnail_url)}" alt="" width="64" height="64" loading="lazy">`
            : `<div class="music-poll-admin-thumb music-poll-admin-thumb--ph" aria-hidden="true">♪</div>`;
        previewEl.innerHTML = `<div class="music-poll-playlist-preview-card">${thumb}<div class="music-poll-playlist-preview-meta"><strong>${esc(
            data.name || "Playlist"
        )}</strong><a href="${esc(data.external_url)}" target="_blank" rel="noopener">Ouvrir sur Spotify ↗</a></div></div>`;
    } catch (e) {
        console.error(e);
        statusEl.textContent = "Erreur réseau.";
    }
}

function onMusicPollSelectorChange() {
    const id = document.getElementById("music-poll-selector").value;
    if (!id) {
        newMusicPollForm();
        return;
    }
    loadMusicPollDetail(parseInt(id, 10));
}

async function loadSpotifyAdmin() {
    try {
        const d = await API.get("/api/admin/spotify-settings");
        document.getElementById("spotify-client-id").value = d.client_id || "";
        const badge = document.getElementById("spotify-config-badge");
        const warn = document.getElementById("music-poll-spotify-warning");
        const envHint = document.getElementById("spotify-env-hint");
        if (d.configured) {
            badge.textContent = "Configuré";
            badge.className = "spotify-config-badge spotify-config-badge--on";
            warn.classList.add("hidden");
        } else {
            badge.textContent = "Non configuré";
            badge.className = "spotify-config-badge spotify-config-badge--off";
            warn.classList.remove("hidden");
        }
        const efb = d.env_fallback_active || {};
        if (efb.client_id || efb.client_secret) {
            envHint.textContent =
                "Une partie des identifiants provient des variables d’environnement du serveur (complément ou secours).";
            envHint.classList.remove("hidden");
        } else {
            envHint.classList.add("hidden");
        }
        const shell = document.getElementById("spotify-secret-shell");
        const veil = document.getElementById("spotify-secret-veil");
        const input = document.getElementById("spotify-client-secret");
        const dots = document.getElementById("spotify-secret-dots");
        document.getElementById("spotify-clear-secret").checked = false;
        if (dots) dots.textContent = d.client_secret_hint || "••••••••";
        if (d.client_secret_configured) {
            shell.classList.add("is-locked");
            shell.classList.remove("is-revealed");
            veil.classList.remove("hidden");
            input.value = "";
            input.placeholder = d.client_secret_hint
                ? `Secret enregistré (${d.client_secret_hint})`
                : "Secret enregistré";
        } else {
            shell.classList.remove("is-locked");
            shell.classList.add("is-revealed");
            veil.classList.add("hidden");
            input.value = "";
            input.placeholder = "Collez le Client Secret (dashboard Spotify)";
        }
    } catch (e) {
        console.error(e);
    }
}

async function saveSpotifySettings() {
    const status = document.getElementById("spotify-save-status");
    status.textContent = "Enregistrement…";
    const body = { client_id: document.getElementById("spotify-client-id").value };
    if (document.getElementById("spotify-clear-secret").checked) {
        body.clear_client_secret = true;
    } else {
        const sec = document.getElementById("spotify-client-secret").value.trim();
        if (sec) body.client_secret = sec;
    }
    try {
        const { data, status: st } = await API.put("/api/admin/spotify-settings", body);
        if (st >= 400) throw new Error(data.error || "Erreur");
        status.textContent = data.test_message || "Enregistré.";
        status.title = data.test_message || "";
        status.style.color = data.test_ok === false ? "#b45309" : "";
        setTimeout(() => {
            status.textContent = "";
            status.title = "";
            status.style.color = "";
        }, 10000);
        await loadSpotifyAdmin();
    } catch (e) {
        status.textContent = e.message || "Erreur";
    }
}

function setupSpotifyApiCard() {
    document.getElementById("spotify-save-btn").addEventListener("click", saveSpotifySettings);
    document.getElementById("spotify-secret-veil").addEventListener("click", (e) => {
        e.preventDefault();
        const shell = document.getElementById("spotify-secret-shell");
        const veil = document.getElementById("spotify-secret-veil");
        const input = document.getElementById("spotify-client-secret");
        shell.classList.remove("is-locked");
        shell.classList.add("is-revealed");
        veil.classList.add("hidden");
        input.value = "";
        input.placeholder = "Saisir le nouveau Client Secret";
        input.focus();
    });
}

async function loadMusicPollAdmin() {
    await loadSpotifyAdmin();
    try {
        const data = await API.get("/api/admin/music-polls");
        musicPollList = data.polls || [];
        const sel = document.getElementById("music-poll-selector");
        sel.innerHTML = "";
        const o0 = document.createElement("option");
        o0.value = "";
        o0.textContent = "— Choisir —";
        sel.appendChild(o0);
        musicPollList.forEach((p) => {
            const o = document.createElement("option");
            o.value = String(p.id);
            o.textContent = `#${p.id} ${p.title} (${p.track_count} morceaux)${p.is_active ? " · actif" : ""}`;
            sel.appendChild(o);
        });
        if (musicPollCurrentId && musicPollList.some((x) => x.id === musicPollCurrentId)) {
            sel.value = String(musicPollCurrentId);
            await loadMusicPollDetail(musicPollCurrentId);
        } else if (musicPollList.length) {
            sel.value = String(musicPollList[0].id);
            await loadMusicPollDetail(musicPollList[0].id);
        } else {
            newMusicPollForm();
        }
        await refreshRingtoneAdminPanel();
    } catch (e) {
        console.error(e);
    }
}

function newMusicPollForm() {
    musicPollCurrentId = null;
    document.getElementById("music-poll-edit-id").value = "";
    document.getElementById("music-poll-selector").value = "";
    document.getElementById("music-poll-title").value = "";
    document.getElementById("music-poll-max-votes").value = "1";
    document.getElementById("music-poll-end").value = "";
    document.getElementById("music-poll-playlist").value = "";
    document.getElementById("music-poll-active").checked = false;
    document.getElementById("music-poll-tracks-admin").innerHTML = "";
    const rr = document.getElementById("ringtone-poll-results-clickable");
    if (rr) rr.innerHTML = "";
    document.getElementById("music-poll-save-status").textContent = "";
    document.getElementById("music-poll-playlist-test-status").textContent = "";
    const plp = document.getElementById("music-poll-playlist-preview");
    if (plp) {
        plp.classList.add("hidden");
        plp.innerHTML = "";
    }
    window.MusicPollWidget?.stopAll?.();
    const prev = document.getElementById("music-poll-student-preview");
    if (prev) prev.innerHTML = `<p class="empty-msg">Enregistrez d’abord le sondage (titre + Enregistrer), puis ajoutez des morceaux.</p>`;
}

async function loadMusicPollDetail(pollId) {
    musicPollCurrentId = pollId;
    try {
        const data = await API.get(`/api/admin/music-poll/${pollId}`);
        const p = data.poll;
        document.getElementById("music-poll-edit-id").value = String(p.id);
        document.getElementById("music-poll-title").value = p.title || "";
        document.getElementById("music-poll-max-votes").value = String(p.max_votes || 1);
        document.getElementById("music-poll-end").value = isoToDatetimeLocal(p.end_date);
        document.getElementById("music-poll-playlist").value = p.spotify_playlist_url || "";
        document.getElementById("music-poll-active").checked = !!p.is_active;
        renderMusicPollTracksAdmin(p.tracks || []);
        await refreshMusicPollPreview();
        await refreshRingtoneAdminPanel();
    } catch (e) {
        console.error(e);
    }
}

function renderMusicPollTracksAdmin(tracks) {
    const el = document.getElementById("music-poll-tracks-admin");
    if (!tracks.length) {
        el.innerHTML = `<p class="empty-msg">Aucun morceau. Colle un lien Spotify (track) ci-dessus.</p>`;
        return;
    }
    el.innerHTML = tracks
        .map(
            (t) => `
<div class="music-poll-admin-track-card" data-track-id="${t.id}">
  ${t.thumbnail_url ? `<img class="music-poll-admin-thumb" src="${esc(t.thumbnail_url)}" alt="" width="56" height="56" loading="lazy">` : `<div class="music-poll-admin-thumb music-poll-admin-thumb--ph" aria-hidden="true">♪</div>`}
  <div class="music-poll-admin-track-meta">
    <strong>${esc(t.title)}</strong>
    <span>${esc(t.artist)}</span>
    <span class="music-poll-admin-preview">Aperçu : ${t.preview_available ? "✓" : "✗"} · votes : ${t.vote_count}${t.preview_available ? "" : " <span class=\"music-poll-no-prev-badge\">⚠ Aperçu indisponible</span>"}</span>
  </div>
  <div class="music-poll-admin-track-actions">
    ${t.preview_url ? `<button type="button" class="btn btn-sm btn-secondary music-poll-preview-test">Tester l'aperçu</button>` : ""}
    <button type="button" class="btn btn-sm btn-secondary music-poll-track-del" data-track-id="${t.id}">Supprimer</button>
  </div>
</div>`
        )
        .join("");
    el.querySelectorAll(".music-poll-track-del").forEach((btn) => {
        btn.addEventListener("click", () => deleteMusicPollTrack(parseInt(btn.dataset.trackId, 10)));
    });
    el.querySelectorAll(".music-poll-admin-track-card").forEach((card) => {
        const tid = parseInt(card.dataset.trackId, 10);
        const tr = tracks.find((x) => x.id === tid);
        const testBtn = card.querySelector(".music-poll-preview-test");
        if (testBtn && tr && tr.preview_url) {
            testBtn.addEventListener("click", () => {
                const a = new Audio(spotifyPreviewSrcForAdmin(tr.preview_url));
                a.volume = 0.8;
                a.play().catch(() => {});
            });
        }
    });
}

async function publishMusicPoll() {
    const status = document.getElementById("music-poll-save-status");
    const title = document.getElementById("music-poll-title").value.trim();
    if (!title) {
        status.textContent = "Titre requis.";
        return;
    }
    const editId = document.getElementById("music-poll-edit-id").value;
    if (!editId) {
        status.textContent = "Enregistrez d’abord le sondage (titre + Enregistrer).";
        return;
    }
    document.getElementById("music-poll-active").checked = true;
    status.textContent = "Publication…";
    const body = {
        title,
        max_votes: parseInt(document.getElementById("music-poll-max-votes").value, 10) || 1,
        end_date: datetimeLocalToIso(document.getElementById("music-poll-end").value),
        spotify_playlist_url: document.getElementById("music-poll-playlist").value.trim() || null,
        is_active: true,
    };
    try {
        const { data, status: st } = await API.put(`/api/admin/music-poll/${editId}`, body);
        if (st >= 400) throw new Error(data.error || "Erreur");
        musicPollCurrentId = data.poll.id;
        status.textContent = "Publié (actif).";
        setTimeout(() => {
            status.textContent = "";
        }, 3000);
        await loadMusicPollAdmin();
    } catch (e) {
        status.textContent = e.message || "Erreur";
    }
}

async function saveMusicPoll() {
    const status = document.getElementById("music-poll-save-status");
    const title = document.getElementById("music-poll-title").value.trim();
    if (!title) {
        status.textContent = "Titre requis.";
        return;
    }
    const body = {
        title,
        max_votes: parseInt(document.getElementById("music-poll-max-votes").value, 10) || 1,
        end_date: datetimeLocalToIso(document.getElementById("music-poll-end").value),
        spotify_playlist_url: document.getElementById("music-poll-playlist").value.trim() || null,
        is_active: document.getElementById("music-poll-active").checked,
    };
    const editId = document.getElementById("music-poll-edit-id").value;
    status.textContent = "Enregistrement…";
    try {
        if (editId) {
            const { data, status: st } = await API.put(`/api/admin/music-poll/${editId}`, body);
            if (st >= 400) throw new Error(data.error || "Erreur");
            musicPollCurrentId = data.poll.id;
            status.textContent = "Enregistré.";
        } else {
            const { data, status: st } = await API.post("/api/admin/music-poll", body);
            if (st >= 400) throw new Error(data.error || "Erreur");
            musicPollCurrentId = data.poll.id;
            document.getElementById("music-poll-edit-id").value = String(data.poll.id);
            status.textContent = "Créé.";
        }
        setTimeout(() => { status.textContent = ""; }, 2500);
        await loadMusicPollAdmin();
    } catch (e) {
        status.textContent = e.message || "Erreur";
    }
}

async function deleteMusicPoll() {
    const editId = document.getElementById("music-poll-edit-id").value;
    if (!editId) return;
    if (!confirm("Supprimer ce sondage et tous les votes ?")) return;
    try {
        await API.delete(`/api/admin/music-poll/${editId}`);
        musicPollCurrentId = null;
        await loadMusicPollAdmin();
    } catch (e) {
        console.error(e);
    }
}

async function addMusicPollTrack() {
    const editId = document.getElementById("music-poll-edit-id").value;
    if (!editId) {
        alert("Enregistre d’abord le sondage (titre + Enregistrer).");
        return;
    }
    const url = document.getElementById("music-poll-track-url").value.trim();
    if (!url) return;
    const btn = document.getElementById("music-poll-add-track-btn");
    btn.disabled = true;
    try {
        const { data, status } = await API.post(`/api/admin/music-poll/${editId}/tracks`, { spotify_url: url });
        if (status === 503) {
            document.getElementById("music-poll-spotify-warning")?.classList.remove("hidden");
            alert("Spotify non configuré : renseignez Client ID et Secret dans la carte API ci-dessus (ou sur le serveur).");
            return;
        }
        if (status >= 400) {
            alert(data.error || "Erreur lors de l’ajout");
            return;
        }
        document.getElementById("music-poll-track-url").value = "";
        await loadMusicPollDetail(parseInt(editId, 10));
    } catch (e) {
        alert("Erreur réseau.");
    } finally {
        btn.disabled = false;
    }
}

async function deleteMusicPollTrack(trackId) {
    const editId = document.getElementById("music-poll-edit-id").value;
    if (!editId) return;
    try {
        await API.delete(`/api/admin/music-poll/${editId}/tracks/${trackId}`);
        await loadMusicPollDetail(parseInt(editId, 10));
    } catch (e) {
        console.error(e);
    }
}

// ==================== Helpers ====================

function esc(str) { if (!str) return ""; const d = document.createElement("div"); d.textContent = str; return d.innerHTML; }

document.addEventListener("DOMContentLoaded", init);
