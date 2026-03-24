const API = {
    async get(url) {
        const res = await fetch(url, { credentials: "same-origin" });
        const ct = (res.headers.get("content-type") || "").toLowerCase();
        if (ct.includes("application/json")) {
            const data = await res.json();
            if (!res.ok) {
                const err = data && (data.error || data.message);
                throw new Error(err || `HTTP ${res.status}`);
            }
            return data;
        }
        const text = await res.text();
        throw new Error(
            res.ok
                ? `Réponse inattendue (pas du JSON). ${text.slice(0, 120)}`
                : `HTTP ${res.status} — ${text.replace(/\s+/g, " ").slice(0, 160)}`,
        );
    },
    async post(url, data) {
        const res = await fetch(url, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
        });
        let payload = {};
        try {
            payload = await res.json();
        } catch {
            payload = {};
        }
        return { data: payload, status: res.status };
    },
};

const VISITOR_STORAGE_KEY = "lycee_visitor_id";
const VOTE_STORAGE_KEY = "lycee_vote_state_v1";

function readVoteCache() {
    try {
        return JSON.parse(localStorage.getItem(VOTE_STORAGE_KEY) || "{}") || {};
    } catch {
        return {};
    }
}

function writeVoteCache(obj) {
    try {
        localStorage.setItem(VOTE_STORAGE_KEY, JSON.stringify(obj));
    } catch (e) {
        console.warn("writeVoteCache", e);
    }
}

/** Synchronise le cache local avec la vérité serveur (likes / votes CVL). */
function syncVoteCacheFromServer(suggestions, proposal) {
    const prev = readVoteCache();
    const st = { v: 1, suggestions: { ...(prev.suggestions || {}) }, proposal: prev.proposal || null };
    (suggestions || []).forEach((s) => {
        if (s.has_voted) st.suggestions[String(s.id)] = s.my_vote || "for";
        else delete st.suggestions[String(s.id)];
    });
    if (proposal) {
        if (proposal.my_vote) st.proposal = { id: proposal.id, vote: proposal.my_vote };
        else st.proposal = null;
    }
    writeVoteCache(st);
}

/**
 * Source de vérité : la réponse API (has_voted / my_vote par session).
 * On ne fusionne plus un cache local par-dessus le serveur : cela masquait les désynchros
 * et entrait en conflit avec la persistance après refresh.
 */
function mergeLocalVoteHints(suggestions, proposal) {
    return { suggestions: suggestions || [], proposal: proposal || null };
}

async function sessionBootstrap() {
    try {
        const stored = localStorage.getItem(VISITOR_STORAGE_KEY);
        const uuidOk = stored && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(stored);
        let restoreOk = false;
        if (uuidOk) {
            const body = JSON.stringify({ visitor_id: stored });
            const postRestore = async () =>
                fetch("/api/session/restore", {
                    method: "POST",
                    credentials: "same-origin",
                    headers: { "Content-Type": "application/json" },
                    body,
                });
            let res = await postRestore();
            restoreOk = res.ok;
            if (!restoreOk && (res.status === 429 || res.status >= 500)) {
                await new Promise((r) => setTimeout(r, 450));
                res = await postRestore();
                restoreOk = res.ok;
            }
        }
        const me = await API.get("/api/session/me");
        if (me && me.visitor_id) {
            if (!stored) {
                localStorage.setItem(VISITOR_STORAGE_KEY, me.visitor_id);
            } else if (restoreOk) {
                localStorage.setItem(VISITOR_STORAGE_KEY, me.visitor_id);
            }
        }
    } catch (e) {
        console.warn("sessionBootstrap", e);
    }
}

const CATEGORY_ICONS = {
    "Cantine": "🍽️", "Infrastructure": "🏗️", "Vie scolaire": "📚",
    "Pédagogie": "🎓", "Numérique": "💻", "Bien-être": "🌿", "Autre": "📌",
};

let currentCategory = "Toutes";
let currentSort = "votes";
let currentDebateFilter = false;
let allSuggestions = [];
let _pendingSuggestionIds = new Set();
let officialProposal = null;
let expanded = false;
const INITIAL_SHOW = 4;

/**
 * Verrou synchrone par suggestion (Symbol) — posé avant tout await / fetch.
 * voteLocksBySuggestionId + lastVoteServerTs : réconciliation poll vs vote.
 */
const voteLocksBySuggestionId = new Map();
/** @type {Record<number, number>} dernier server_ts appliqué après une réponse vote */
const lastVoteServerTs = {};
/** Cooldown par suggestion après une action vote simple réussie (toggle add/remove). */
const suggestionVoteCooldownUntil = {};
/** Entre deux double-tap vote sur la même suggestion (mode découvrir) — anti-spam gestuel. */
const swipeDoubleTapVoteCooldownUntil = {};
let pendingOfficialProposalVote = false;

// --------------- DOM ---------------

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const input = $("#suggestion-input");
const submitBtn = $("#submit-btn");
const charCount = $("#char-count");
const suggestionsContainer = $("#suggestions-list");
const emptyState = $("#empty-state");
const feedback = $("#feedback");
const sortSelect = $("#sort-select");
const categoryFilters = $("#category-filters");
const fadeOverlay = $("#suggestions-fade");
const expandBtn = $("#expand-btn");
const expandText = $("#expand-text");
const suggestionsDesktopWrap = $("#suggestions-desktop-wrap");
const filtersSection = $("#filters-section");
const filtersMobileToggle = $("#filters-mobile-toggle");
const filtersPanelWrap = $("#filters-panel-wrap");
const phoneSwipeContent = $("#phone-swipe-content");
const phoneNavDock = $("#phone-nav-dock");
const phoneListSearchWrap = $("#phone-list-search-wrap");
const listSearchInput = $("#list-search");
const btnPhoneModeSwipe = $("#btn-phone-mode-swipe");
const btnPhoneModeList = $("#btn-phone-mode-list");
const btnPhoneModeLiked = $("#btn-phone-mode-liked");
const swipeCvlSlot = $("#swipe-cvl-slot");
const swipeDeckInner = $("#swipe-deck-inner");
const swipeCounter = $("#swipe-counter");
const swipeCounterTotal = $("#swipe-counter-total");

/** Mode téléphone : 'swipe' = une fiche à la fois, 'list' = comme sur PC */
let phoneUiMode = "swipe";
let phoneListLikedOnly = false;
let swipeIndex = 0;
/** Recherche (modes Liste / Soutenus uniquement, téléphone) */
let phoneListSearchQuery = "";
let swipeTouchStartX = 0;
let swipeTouchStartY = 0;
let swipeDragging = false;
let swipeLastTap = 0;
/** FIX-FINAL-2: reset minimal assigné dans attachSwipeDeckGestures (évite état zombie hors closure) */
let swipeGestureReset = null;
/** Évite reshuffle / re-render du deck pendant un geste tactile */
let swipeUserInteracting = false;
let pendingSwipeDeckRebuild = false;
let lastSwipeDeckSig = "";
let swipeInteractClearTimer = null;
let debateArgSheetEl = null;
/** Deck swipe (suggestions mélangées + cartes engagement), uniquement tactile */
let engagementBootstrap = null;
let swipeDeckItems = [];
/** Suggestions déjà passées en « suivant » (session onglet) — chaque id une seule fois */
const SWIPE_CONSUMED_STORAGE_KEY = "swipe_consumed_suggestion_ids_v1";
/** Carte courante du deck (restauration après poll / refresh) */
const SWIPE_DECK_ANCHOR_KEY = "swipe_deck_anchor_v1";
let swipeConsumedIds = new Set();
try {
    const raw = sessionStorage.getItem(SWIPE_CONSUMED_STORAGE_KEY);
    if (raw) {
        const arr = JSON.parse(raw);
        if (Array.isArray(arr)) swipeConsumedIds = new Set(arr.map(Number).filter((n) => !Number.isNaN(n)));
    }
} catch (e) {
    /* ignore */
}
function saveSwipeConsumedIds() {
    try {
        sessionStorage.setItem(SWIPE_CONSUMED_STORAGE_KEY, JSON.stringify([...swipeConsumedIds].sort((a, b) => a - b)));
    } catch (e) {
        /* ignore */
    }
}

// FIX-4: configuration gestuelle centralisée (seuils swipe mobile)
const GESTURE_CONFIG = {
    AXIS_LOCK_DISTANCE: 8,
    SWIPE_THRESHOLD_X_RATIO: 0.22,
    SWIPE_THRESHOLD_Y_RATIO: 0.2,
    FLICK_VELOCITY_THRESHOLD: 0.45,
    DOUBLE_TAP_MS: 300,
    LABEL_VISIBLE_AT_RATIO: 0.15,
    CLASSIFY_MIN_DIST: 12,
    COMMIT_H_ADX_MIN: 72,
    COMMIT_H_ADY_MAX: 118,
    COMMIT_RATIO_H: 0.85,
    TH_UP: 100,
    TH_DN: 88,
    VEL_VY_UP: -0.42,
    MY_DIST_UP: -118,
    ROT_X_FACTOR: 0.035,
    EXIT_ROT_FACTOR: 0.06,
};

// FIX-2: persistance cache UX (seen / likés) — la source de vérité reste l’API (has_voted, etc.)
// FIX-FINAL-1: pas de geste « favori » distinct côté produit — « Mes favoris » = liste des soutenus (dock).
// L’ancien couple swipe_favorited_ids / markFavorited n’était jamais appelé ; retiré pour éviter une API morte.
const SwipeHistory = {
    _get(key) {
        try {
            return new Set(JSON.parse(localStorage.getItem(key) || "[]"));
        } catch {
            return new Set();
        }
    },
    _save(key, set) {
        try {
            localStorage.setItem(key, JSON.stringify([...set]));
        } catch (e) {
            /* ignore */
        }
    },
    markSeen(id) {
        const s = this._get("swipe_seen_ids");
        s.add(String(id));
        this._save("swipe_seen_ids", s);
    },
    markLiked(id) {
        const s = this._get("swipe_liked_ids");
        s.add(String(id));
        this._save("swipe_liked_ids", s);
    },
    isSeen(id) {
        return this._get("swipe_seen_ids").has(String(id));
    },
    isLiked(id) {
        return this._get("swipe_liked_ids").has(String(id));
    },
    isConsumed(id) {
        return this.isLiked(id);
    },
    resetSeen() {
        try {
            localStorage.removeItem("swipe_seen_ids");
        } catch (e) {
            /* ignore */
        }
    },
    resetAll() {
        ["swipe_seen_ids", "swipe_liked_ids"].forEach((k) => {
            try {
                localStorage.removeItem(k);
            } catch (e) {
                /* ignore */
            }
        });
        try {
            localStorage.removeItem("swipe_favorited_ids");
        } catch (e) {
            /* ignore */
        }
    },
};

/** FIX-2: une fois — session « consommés » → seen localStorage */
function migrateSessionConsumedToSwipeHistory() {
    try {
        swipeConsumedIds.forEach((id) => SwipeHistory.markSeen(String(id)));
        // FIX-FINAL-1: clé legacy jamais alimentée par un geste réel
        localStorage.removeItem("swipe_favorited_ids");
    } catch (e) {
        /* ignore */
    }
}

function swipeDeckAnchorFromItem(item) {
    if (!item) return null;
    if (item.kind === "suggestion") return { k: "s", id: item.id };
    if (item.kind === "special") {
        return { k: "x", t: item.type };
    }
    if (item.kind === "end") {
        return { k: "e", t: item.type || "end" };
    }
    return null;
}

function findSwipeIndexForAnchor(anchor) {
    if (!anchor || !swipeDeckItems.length) return -1;
    return swipeDeckItems.findIndex((it) => {
        if (anchor.k === "s" && it.kind === "suggestion") return it.id === anchor.id;
        if (anchor.k === "x" && it.kind === "special") return it.type === anchor.t;
        if (anchor.k === "e" && it.kind === "end") return (it.type || "end") === anchor.t;
        return false;
    });
}

function persistSwipeDeckAnchor() {
    try {
        const it = swipeDeckItems[swipeIndex];
        const a = swipeDeckAnchorFromItem(it);
        if (a) sessionStorage.setItem(SWIPE_DECK_ANCHOR_KEY, JSON.stringify(a));
    } catch (e) {
        /* ignore */
    }
}

function restoreSwipeIndexAfterDeckBuild() {
    try {
        const raw = sessionStorage.getItem(SWIPE_DECK_ANCHOR_KEY);
        if (raw) {
            const anchor = JSON.parse(raw);
            const idx = findSwipeIndexForAnchor(anchor);
            if (idx >= 0) {
                swipeIndex = idx;
                return;
            }
        }
    } catch (e) {
        /* ignore */
    }
    clampSwipeIndex();
}
const swipeGuessReveal = {};
/** Morpion (carte spéciale) */
let tttBoard = null;
let tttGameOver = false;

const isTouchDevice = () => {
    const hasTouch = "ontouchstart" in window || navigator.maxTouchPoints > 0;
    const hasFinePointer = window.matchMedia("(pointer: fine)").matches;
    const isWideScreen = window.innerWidth >= 768;
    return hasTouch && !hasFinePointer && !isWideScreen;
};

/** Exposé pour la console / diagnostic_frontend.js (let global ≠ window.* en mode script classique). */
if (typeof window !== "undefined") {
    window.__lyceeDiag = {
        get allSuggestions() {
            return allSuggestions;
        },
        get voteLocksBySuggestionId() {
            return voteLocksBySuggestionId;
        },
        get lastVoteServerTs() {
            return lastVoteServerTs;
        },
        get voteOptimisticUntil() {
            return voteOptimisticUntil;
        },
        get swipeDoubleTapVoteCooldownUntil() {
            return swipeDoubleTapVoteCooldownUntil;
        },
        get phoneUiMode() {
            return phoneUiMode;
        },
    };
}

// --------------- Init ---------------

let submissionsOpen = true;

async function init() {
    await sessionBootstrap();
    if (isTouchDevice()) {
        try {
            // FIX-1: premier chargement mobile — défaut swipe sans écraser un choix explicite (phone_ui_mode)
            const savedExplicit = localStorage.getItem("phone_ui_mode");
            const studentDefault = localStorage.getItem("student_default_mode");
            if (!savedExplicit && !studentDefault) {
                localStorage.setItem("student_default_mode", "swipe");
                phoneUiMode = "swipe";
            } else {
                phoneUiMode = savedExplicit === "list" ? "list" : "swipe";
            }
            phoneListLikedOnly = localStorage.getItem("phone_ui_liked") === "1";
        } catch (e) {
            /* ignore */
        }
        migrateSessionConsumedToSwipeHistory();
        syncPhoneUiChrome();
        setupPhoneSwipe();
        engagementPingPresence();
        retryPendingCommunityMessages();
    }
    loadCategories();
    loadSuggestions();
    loadRingtoneStudentBanner();
    if (typeof initMusicPollModule === "function") initMusicPollModule();
    checkSubmissionsStatus();
    scheduleSuggestionsPoll();
    setupLiveSyncActivityListeners();
    setInterval(checkSubmissionsStatus, 90000);
    document.addEventListener("visibilitychange", onDocumentVisibilityChange);
    setupEvents();
}

/**
 * Rafraîchissement sans rechargement de page : fetch() périodique + fusion dans `allSuggestions`
 * et mise à jour ciblée du DOM. Polling adaptatif via `scheduleNextPoll` / `computeNextPollDelayMs` (StudentLiveSync).
 * Aucun location.reload — uniquement remplacement / animation de nœuds existants.
 */

/** Incrémenté à chaque load : les réponses obsolètes (requête plus ancienne) n’appliquent pas le DOM. */
let loadSuggestionsGeneration = 0;

/** Au retour sur l’onglet : statut soumissions + une synchro suggestions (évite données trop vieilles). */
function onDocumentVisibilityChange() {
    if (document.visibilityState === "visible") recordUserActivity();
    if (document.visibilityState !== "visible") return;
    checkSubmissionsStatus();
    clearTimeout(window.__visibilityReloadTimer);
    window.__visibilityReloadTimer = setTimeout(() => {
        loadSuggestions({ reason: "poll" });
        loadRingtoneStudentBanner();
    }, 400);
}

async function loadRingtoneStudentBanner() {
    const host = document.getElementById("ringtone-student-banner");
    if (!host) return;
    try {
        const data = await API.get("/api/ringtone/current");
        if (!data.enabled || !data.track || !data.track.title) {
            host.classList.add("hidden");
            host.innerHTML = "";
            return;
        }
        const t = data.track;
        const thumb = t.thumbnail_url
            ? `<img class="ringtone-cd ringtone-cd-spin" src="${escapeHtml(t.thumbnail_url)}" alt="" width="56" height="56" loading="lazy">`
            : `<div class="ringtone-cd ringtone-cd--ph ringtone-cd-spin" aria-hidden="true">♪</div>`;
        host.innerHTML = `<div class="ringtone-student-inner">
        <h2 class="ringtone-student-heading">Musique de la sonnerie Actuelle</h2>
        <div class="ringtone-student-row">
          <div class="ringtone-cd-shell">${thumb}</div>
          <div class="ringtone-student-meta">
            <span class="ringtone-student-track">${escapeHtml(t.title || "")}</span>
            <span class="ringtone-student-artist">${escapeHtml(t.artist || "")}</span>
          </div>
        </div>
      </div>`;
        host.classList.remove("hidden");
    } catch (e) {
        host.classList.add("hidden");
        host.innerHTML = "";
    }
}

function computeSwipeDeckSig() {
    const ids = allSuggestions
        .map((s) => s.id)
        .slice()
        .sort((a, b) => a - b)
        .join(",");
    const consumed = [...swipeConsumedIds].sort((a, b) => a - b).join(",");
    const done = (engagementBootstrap?.cards_done_today || []).slice().sort().join(",");
    const guessEl = (engagementBootstrap?.guess_eligible_ids || []).slice().sort((a, b) => a - b).join(",");
    const dlm = engagementBootstrap?.dilemma?.id ?? "";
    const peerId = engagementBootstrap?.peer_message?.id ?? "";
    return `${ids}|c:${consumed}|${done}|${guessEl}|${dlm}|pm:${peerId}`;
}

/** Met à jour le compteur / texte sur la carte swipe courante sans recréer le DOM (évite reset du geste). */
function patchSwipeVoteUiForCurrentCard() {
    if (!swipeDeckInner) return;
    const item = swipeDeckItems[swipeIndex];
    if (!item || item.kind !== "suggestion") return;
    const s = allSuggestions.find((x) => x.id === item.id);
    if (!s) return;
    if (swipeDragging) return;
    const card = swipeDeckInner.querySelector(`#swipe-active-layer .swipe-card[data-id="${s.id}"]`);
    if (!card) return;
    const hasVoted = s.has_voted === true;

    if (!s.needs_debate) {
        card.dataset.hasVoted = hasVoted ? "true" : "false";
        card.classList.toggle("swipe-card--liked", hasVoted);
        const big = card.querySelector(".swipe-card-votes-big");
        if (big) {
            big.classList.toggle("swipe-card-votes-big--liked", hasVoted);
            const t = `♥ ${s.vote_count}`;
            if (big.textContent !== t) tickLiveField(big);
            big.textContent = t;
        }
        const hint = card.querySelector(".swipe-card-hint:not(.swipe-card-hint-debate-inner)");
        if (hint) {
            hint.textContent = hasVoted ? "Double tap · retirer le soutien" : "Double tap · soutenir";
        }
    } else {
        card.dataset.hasVoted = hasVoted ? "true" : "false";
        const vf = card.querySelector(".swipe-debate-score--for");
        const va = card.querySelector(".swipe-debate-score--against");
        if (vf) {
            const t = `Pour ${s.vote_for ?? 0}`;
            if (vf.textContent !== t) tickLiveField(vf);
            vf.textContent = t;
        }
        if (va) {
            const t = `Contre ${s.vote_against ?? 0}`;
            if (va.textContent !== t) tickLiveField(va);
            va.textContent = t;
        }
    }
}

async function checkSubmissionsStatus() {
    try {
        const settings = await API.get("/api/settings/public");
        submissionsOpen = settings.submissions_open;
        const submitCard = $(".submit-card");
        let banner = $("#submissions-closed-banner");

        if (!submissionsOpen) {
            input.disabled = true;
            submitBtn.disabled = true;
            if (!banner) {
                banner = document.createElement("div");
                banner.id = "submissions-closed-banner";
                banner.className = "submissions-closed-banner";
                banner.innerHTML = `<span class="closed-icon">🔒</span> Les suggestions sont temporairement fermées.`;
                submitCard.prepend(banner);
            }
        } else {
            input.disabled = false;
            const len = input.value.trim().length;
            submitBtn.disabled = len < 5 || len > 500;
            if (banner) banner.remove();
        }
    } catch (e) {
        /* ignore */
    }
}

function setupEvents() {
    input.addEventListener("input", () => {
        const len = input.value.length;
        charCount.textContent = `${len} / 500`;
        const canSubmit = len >= 5 && len <= 500 && submissionsOpen;
        submitBtn.disabled = !canSubmit;
    });

    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && e.ctrlKey && !submitBtn.disabled) handleSubmit();
    });

    submitBtn.addEventListener("click", () => handleSubmit());
    $("#precision-yes")?.addEventListener("click", () => onPrecisionChoice(true));
    $("#precision-no")?.addEventListener("click", () => onPrecisionChoice(false));
    sortSelect.addEventListener("change", () => {
        currentSort = sortSelect.value;
        loadSuggestions();
    });
    $("#btn-refresh-suggestions")?.addEventListener("click", () => loadSuggestions({ reason: "user" }));

    expandBtn.addEventListener("click", toggleExpand);
}

// --------------- Categories ---------------

async function loadCategories() {
    const categories = await API.get("/api/categories");
    categoryFilters.innerHTML = "";

    ["Toutes", ...categories.filter((c) => c !== "Autre"), "Débat"].forEach((cat) => {
        const btn = document.createElement("button");
        btn.type = "button";
        const isDebate = cat === "Débat";
        const isActive = isDebate ? currentDebateFilter : (cat === currentCategory && !currentDebateFilter);
        btn.className = `filter-chip${isActive ? " active" : ""}`;
        btn.dataset.category = isDebate ? "__debat__" : cat;
        const icon = isDebate ? "⚖️" : (CATEGORY_ICONS[cat] || "");
        btn.textContent = isDebate ? "Débat" : (cat === "Toutes" ? "Toutes" : `${icon} ${cat}`);
        btn.addEventListener("click", () => {
            $$("#category-filters .filter-chip").forEach((c) => c.classList.remove("active"));
            btn.classList.add("active");
            if (isDebate) {
                currentDebateFilter = true;
                currentCategory = "Toutes";
            } else {
                currentDebateFilter = false;
                currentCategory = cat;
            }
            expanded = false;
            loadSuggestions();
        });
        categoryFilters.appendChild(btn);
    });
}

// --------------- Information Officielle CVL ---------------

function renderCvlOfficialInfo(list) {
    const container = $("#cvl-official-info-container");
    if (!container) return;
    if (!list.length) {
        container.innerHTML = "";
        container.classList.add("hidden");
        return;
    }
    container.classList.remove("hidden");
    container.innerHTML = list.map((i) => {
        const modeClass = i.display_mode === "full" ? "cvl-info-full" : i.display_mode === "compact" ? "cvl-info-compact" : "cvl-info-banner";
        return `
            <div class="cvl-official-info-item cvl-official-info-${i.style} ${modeClass}">
                <div class="cvl-official-info-icon">${i.style === "urgent" ? "🚨" : i.style === "warning" ? "⚠️" : i.style === "success" ? "✅" : "ℹ️"}</div>
                <div class="cvl-official-info-body">
                    <strong class="cvl-official-info-title">${escapeHtml(i.title || "")}</strong>
                    ${i.content ? `<span class="cvl-official-info-content">${escapeHtml(i.content)}</span>` : ""}
                </div>
            </div>
        `;
    }).join("");
}

// --------------- Load Suggestions ---------------
//
// StudentLiveSync : polling fetch adaptatif + patch DOM ciblé (évolutif vers SSE/WebSocket :
// remplacer scheduleNextPoll par une souscription, garder applySuggestionDomPatch).

/** @type {Record<number, number>} voteOptimisticUntil — timestamp jusqu’auquel on évite qu’un poll serveur « en retard » baisse un compteur qu’on vient d’augmenter. */
const voteOptimisticUntil = {};

function tryAcquireVoteLock(suggestionId) {
    if (voteLocksBySuggestionId.has(suggestionId)) return null;
    const token = Symbol(`vote-${suggestionId}`);
    voteLocksBySuggestionId.set(suggestionId, { token, started: Date.now() });
    return token;
}

function releaseVoteLock(suggestionId, token) {
    const cur = voteLocksBySuggestionId.get(suggestionId);
    if (cur && cur.token === token) voteLocksBySuggestionId.delete(suggestionId);
}

function voteLockOrClaimActive(suggestionId) {
    return (
        voteLocksBySuggestionId.has(suggestionId) ||
        (voteOptimisticUntil[suggestionId] && Date.now() < voteOptimisticUntil[suggestionId])
    );
}

/**
 * Si le poll arrive avec un server_ts plus ancien qu’un vote déjà appliqué, on garde les champs vote côté client.
 * Le DOM (data-has-voted, classes) doit suivre l’objet fusionné : voir syncPollVoteDomToMatchSuggestions + updateSuggestionsInPlace.
 */
function applyPollVoteMerge(prev, incoming) {
    // Requête POST /vote en cours pour cette suggestion : ne pas écraser l’état vote avec un poll concurrent
    if (voteLocksBySuggestionId.has(incoming.id)) {
        return {
            ...incoming,
            vote_count: prev.vote_count,
            has_voted: prev.has_voted,
            my_vote: prev.my_vote,
            vote_for: prev.vote_for,
            vote_against: prev.vote_against,
        };
    }
    const incTs = incoming.server_ts ?? 0;
    const gate = lastVoteServerTs[incoming.id] ?? 0;
    const claim = voteLockOrClaimActive(incoming.id);
    if (claim && gate > 0 && incTs > 0 && incTs < gate) {
        return {
            ...incoming,
            vote_count: prev.vote_count,
            has_voted: prev.has_voted,
            my_vote: prev.my_vote,
            vote_for: prev.vote_for,
            vote_against: prev.vote_against,
        };
    }
    return incoming;
}

/** Liste « cœur » : uniquement tactile + onglet Liste (pas PC, pas swipe). */
function isPhoneListHeartLayout() {
    return document.body.classList.contains("student-phone-ui") && document.body.classList.contains("st-phone-list");
}

/**
 * Liste (vote simple) : PC = pastille texte ; téléphone + liste = cœur gris / rose + compteur dessous.
 * @param {object} [opts]
 * @param {boolean} [opts.silent] — pas de tickLiveField (poll / sync discret)
 */
function updateSimpleVoteControlDom(listCard, s, opts = {}) {
    const silent = opts.silent === true;
    if (!listCard || s.needs_debate) return;
    listCard.dataset.hasVoted = s.has_voted ? "true" : "false";
    listCard.classList.toggle("suggestion-card--user-voted", s.has_voted === true);
    const wrap = listCard.querySelector(".suggestion-vote-wrap");
    const btn = listCard.querySelector(".suggestion-vote-btn");
    if (!wrap || !btn) return;
    const textEl = wrap.querySelector(".suggestion-vote-btn-text");
    const iconEl = wrap.querySelector(".suggestion-vote-btn-icon");
    const countBelow = wrap.querySelector(".suggestion-vote-count-below");
    const voted = !!s.has_voted;
    const phoneList = isPhoneListHeartLayout();
    wrap.classList.toggle("suggestion-vote-wrap--voted", voted);
    wrap.dataset.hasVoted = voted ? "true" : "false";
    if (phoneList) wrap.classList.add("suggestion-vote-wrap--phone-list");
    else wrap.classList.remove("suggestion-vote-wrap--phone-list");
    btn.classList.toggle("voted", voted);
    btn.classList.toggle("suggestion-vote-btn--phone-idle", phoneList && !voted);
    btn.dataset.hasVoted = voted ? "true" : "false";
    btn.setAttribute("aria-pressed", voted ? "true" : "false");
    btn.disabled = false;
    btn.setAttribute(
        "aria-label",
        voted
            ? "Retirer mon soutien"
            : `Soutenir — ${s.vote_count} soutien${s.vote_count !== 1 ? "s" : ""} actuellement`,
    );
    const countStr = String(s.vote_count);
    if (phoneList) {
        if (textEl) textEl.textContent = "";
        if (iconEl) {
            const ic = voted ? "♥" : "♡";
            if (iconEl.textContent !== ic) {
                if (!silent) tickLiveField(iconEl);
            }
            iconEl.textContent = ic;
        }
        if (countBelow) {
            if (countBelow.textContent !== countStr) {
                if (!silent) tickLiveField(countBelow);
            }
            countBelow.textContent = countStr;
        }
    } else {
        const lineText = voted ? `✓ Soutenu · ${s.vote_count}` : `♥ Soutenir · ${s.vote_count}`;
        if (textEl) {
            if (textEl.textContent !== lineText) {
                if (!silent) tickLiveField(textEl);
            }
            textEl.textContent = lineText;
        }
        if (iconEl) iconEl.textContent = "";
        if (countBelow) countBelow.textContent = "";
        btn.classList.remove("suggestion-vote-btn--phone-idle");
    }
}

/** Liste : aligne data-has-voted, bande utilisateur et bouton sur `s` (source = allSuggestions après merge / réponse vote). */
function syncSimpleVoteListCardDom(s, opts = {}) {
    if (!s || s.needs_debate) return;
    const listCard = suggestionsContainer.querySelector(`.suggestion-card[data-id="${s.id}"]`);
    if (!listCard) return;
    updateSimpleVoteControlDom(listCard, s, opts);
}

/** Après poll : le merge mémoire peut préserver has_voted — recopier sur les cartes liste + swipe active. */
function syncPollVoteDomToMatchSuggestions() {
    for (const s of allSuggestions) {
        if (!s.needs_debate) syncSimpleVoteListCardDom(s, { silent: true });
    }
    if (isTouchDevice() && phoneUiMode === "swipe") patchSwipeVoteUiForCurrentCard();
}

let lastIdsSignature = "";
/** Signature serveur brute (hors merge cache local) pour détecter « rien n’a changé » sans rerender. */
let lastPollContentSig = "";
let pollNoChangeStreak = 0;
let pollFailStreak = 0;
let pollBoostCycles = 0;
let lastUserActivityAt = Date.now();
let engagementNextFetchAt = 0;
const ENGAGEMENT_MIN_INTERVAL_MS = 42000;

const POLL_BASE_LIST_MS = 9000;
const POLL_BASE_SWIPE_MS = 17000;
const POLL_HIDDEN_MS = 38000;
const POLL_INACTIVE_MS = 36000;
const POLL_INACTIVE_AFTER_MS = 90000;
const POLL_NOCHANGE_STEP_MS = 4500;
const POLL_NOCHANGE_MAX_EXTRA_MS = 18000;
const POLL_FAIL_BACKOFF_CAP_MS = 90000;

let deferredPollDomPending = false;

function recordUserActivity() {
    lastUserActivityAt = Date.now();
}

function computeContentSignature(suggestions, proposal) {
    const parts = [];
    if (proposal) {
        parts.push(
            `p:${proposal.id}|vf:${proposal.vote_for ?? 0}|va:${proposal.vote_against ?? 0}|nd:${proposal.needs_debate ? 1 : 0}|u:${proposal.updated_at || ""}`,
        );
    }
    (suggestions || []).forEach((s) => {
        parts.push(
            [
                s.id,
                s.vote_count,
                s.status,
                s.title,
                s.subtitle || "",
                s.needs_debate ? 1 : 0,
                s.vote_for ?? 0,
                s.vote_against ?? 0,
                s.importance_score ?? 0,
                s.updated_at || "",
                s.server_ts ?? "",
                s.has_voted ? 1 : 0,
                s.my_vote || "",
            ].join(":"),
        );
    });
    return parts.sort().join("|");
}

function buildCardLiveSig(s) {
    const af = (s.arguments_for || []).length;
    const aa = (s.arguments_against || []).length;
    return [
        s.id,
        s.vote_count,
        s.status,
        s.title,
        s.subtitle || "",
        s.category,
        s.location_name || "",
        s.needs_debate ? 1 : 0,
        s.vote_for ?? 0,
        s.vote_against ?? 0,
        s.has_voted ? 1 : 0,
        s.my_vote || "",
        s.importance_score ?? 0,
        af,
        aa,
    ].join("§");
}

function buildProposalLiveSig(p) {
    if (!p) return "";
    const af = (p.arguments_for || []).length;
    const aa = (p.arguments_against || []).length;
    return [
        p.id,
        p.vote_for ?? 0,
        p.vote_against ?? 0,
        p.needs_debate ? 1 : 0,
        p.my_vote || "",
        p.status || "",
        p.updated_at || "",
        af,
        aa,
    ].join("§");
}

function reconcileOptimisticVotes(nextList) {
    const prevById = new Map(allSuggestions.map((x) => [x.id, x]));
    return nextList.map((s) => {
        const prev = prevById.get(s.id);
        const until = voteOptimisticUntil[s.id];
        if (!until || Date.now() >= until || !prev) return s;
        if (s.needs_debate) {
            let next = { ...s };
            if (
                typeof s.vote_for === "number" &&
                typeof prev.vote_for === "number" &&
                s.vote_for < prev.vote_for
            ) {
                next.vote_for = prev.vote_for;
            }
            if (
                typeof s.vote_against === "number" &&
                typeof prev.vote_against === "number" &&
                s.vote_against < prev.vote_against
            ) {
                next.vote_against = prev.vote_against;
            }
            return next;
        }
        if (
            typeof s.vote_count === "number" &&
            typeof prev.vote_count === "number" &&
            s.vote_count < prev.vote_count
        ) {
            return { ...s, vote_count: prev.vote_count };
        }
        return s;
    });
}

function engagementRefreshDue() {
    return Date.now() >= engagementNextFetchAt;
}

function shouldDeferPollDom() {
    if (debateArgSheetEl) return true;
    if (swipeUserInteracting) return true;
    if (swipeDragging) return true;
    const ae = document.activeElement;
    if (!ae) return false;
    const tag = ae.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
    if (ae.closest?.(".debate-arg-sheet, .precision-modal, #precision-modal")) return true;
    return false;
}

function scheduleDeferredPollDomApply() {
    clearTimeout(window.__deferredPollDomTimer);
    window.__deferredPollDomTimer = setTimeout(() => {
        if (shouldDeferPollDom()) {
            scheduleDeferredPollDomApply();
            return;
        }
        deferredPollDomPending = false;
        try {
            updateSuggestionsInPlace({ quiet: true });
            reorderSuggestionCards();
            syncPollVoteDomToMatchSuggestions();
        } catch (e) {
            console.warn("deferredPollDomApply", e);
        }
    }, 380);
}

function computeNextPollDelayMs() {
    let ms =
        isTouchDevice() && phoneUiMode === "swipe"
            ? POLL_BASE_SWIPE_MS
            : POLL_BASE_LIST_MS;
    if (document.hidden) ms = Math.max(ms, POLL_HIDDEN_MS);
    const idleFor = Date.now() - lastUserActivityAt;
    if (idleFor > POLL_INACTIVE_AFTER_MS) ms = Math.max(ms, POLL_INACTIVE_MS);
    ms += Math.min(pollNoChangeStreak * POLL_NOCHANGE_STEP_MS, POLL_NOCHANGE_MAX_EXTRA_MS);
    if (pollBoostCycles > 0) {
        const boostCap =
            isTouchDevice() && phoneUiMode === "swipe" ? POLL_BASE_SWIPE_MS : POLL_BASE_LIST_MS;
        ms = Math.min(ms, boostCap);
    }
    const failMul = Math.min(1.45 ** pollFailStreak, 4);
    ms = Math.min(ms * failMul, POLL_FAIL_BACKOFF_CAP_MS);
    return Math.round(ms);
}

/** Pointeurs / clavier / scroll : alimente le polling « actif » vs inactif. */
function setupLiveSyncActivityListeners() {
    const onAct = () => recordUserActivity();
    document.addEventListener("pointerdown", onAct, { passive: true });
    document.addEventListener("keydown", onAct);
    let scrollT = 0;
    window.addEventListener(
        "scroll",
        () => {
            clearTimeout(scrollT);
            scrollT = setTimeout(onAct, 120);
        },
        { passive: true },
    );
}

function scheduleNextPoll() {
    try {
        clearTimeout(window.__pollTimeoutId);
    } catch {
        /* ignore */
    }
    window.__pollTimeoutId = setTimeout(() => {
        loadSuggestions({ reason: "poll" });
    }, computeNextPollDelayMs());
}

function scheduleSuggestionsPoll() {
    try {
        clearInterval(window.__suggestionsPollId);
    } catch {
        /* ignore */
    }
    try {
        clearTimeout(window.__pollTimeoutId);
    } catch {
        /* ignore */
    }
    scheduleNextPoll();
}

function buildIdsSignature(proposal, suggestions) {
    const parts = [];
    if (proposal) parts.push(`p${proposal.id}`);
    for (const s of suggestions) parts.push(`s${s.id}`);
    return parts.sort().join("|");
}

function debateOrProposalStructureMismatch(proposal) {
    const cvl = suggestionsContainer.querySelector(".suggestion-card-cvl");
    if (proposal && !cvl) return true;
    if (!proposal && cvl) return true;
    if (proposal && cvl) {
        const hasAgainst = !!cvl.querySelector(".cvl-vote-against[data-vote='against']");
        if (!!proposal.needs_debate !== hasAgainst) return true;
    }
    const showCount = expanded ? allSuggestions.length : INITIAL_SHOW;
    for (const s of allSuggestions.slice(0, showCount)) {
        const card = suggestionsContainer.querySelector(`.suggestion-card[data-id="${s.id}"]`);
        if (!card) return true;
        if (!!s.needs_debate !== card.classList.contains("suggestion-card-debate")) return true;
    }
    return false;
}

/** Micro-transition quand un nombre affiché change (votes, etc.) — sans recharger la page. */
function tickLiveField(el) {
    if (!el) return;
    el.classList.remove("live-field-tick");
    void el.offsetWidth;
    el.classList.add("live-field-tick");
}

function reorderSuggestionCards() {
    const showCount = expanded ? allSuggestions.length : INITIAL_SHOW;
    const visible = allSuggestions.slice(0, showCount);
    const orderKey = visible.map((s) => s.id).join(",");
    const cvlKey = officialProposal ? `p${officialProposal.id}` : "";
    const nextOrderSig = `${cvlKey}|${orderKey}`;
    if (reorderSuggestionCards._lastSig === nextOrderSig) return;
    reorderSuggestionCards._lastSig = nextOrderSig;
    const container = suggestionsContainer;
    if (officialProposal) {
        const cvl = container.querySelector(`.suggestion-card-cvl[data-proposal-id="${officialProposal.id}"]`);
        if (cvl) container.appendChild(cvl);
    }
    visible.forEach((s) => {
        const card = container.querySelector(`.suggestion-card[data-id="${s.id}"]`);
        if (card) container.appendChild(card);
    });
}

function removeSuggestionCardSilently(id) {
    const card = suggestionsContainer.querySelector(`.suggestion-card[data-id="${id}"]`);
    if (card) {
        card.classList.add("suggestion-card-exit-soft");
        setTimeout(() => card.remove(), 280);
    }
}

/**
 * Charge les suggestions + proposition CVL + infos officielles via fetch (JSON).
 * StudentLiveSync : polling (`reason: "poll"`) adaptatif, early-exit si signature inchangée,
 * engagement moins fréquent, réconciliation votes optimistes, report DOM si interaction.
 */
async function loadSuggestions(opts = {}) {
    const reason = opts.reason || "user";
    try {
        if (reason === "poll" && document.hidden) return;
        const myGen = ++loadSuggestionsGeneration;

        const savedInput = input.value;
        const hadFocus = document.activeElement === input;
        const savedArguments = {};
        suggestionsContainer.querySelectorAll(".cvl-argument-panel:not(.hidden)").forEach((panel) => {
            const id = panel.dataset.suggestionId || panel.dataset.proposalId;
            const ta = panel.querySelector(".cvl-argument-input");
            if (id && ta) savedArguments[id] = ta.value;
        });
        const params = { category: currentCategory, sort: currentSort };
        if (currentDebateFilter) params.debate = "1";
        const [suggestionsRaw, proposalRaw, cvlInfo] = await Promise.all([
            API.get(`/api/suggestions?${new URLSearchParams(params)}`),
            API.get("/api/official-proposal"),
            API.get("/api/cvl-official-info"),
        ]);
        if (myGen !== loadSuggestionsGeneration) return;

        const contentSig = computeContentSignature(suggestionsRaw, proposalRaw);
        if (reason === "poll" && contentSig === lastPollContentSig) {
            pollFailStreak = 0;
            pollNoChangeStreak++;
            if (isTouchDevice() && engagementRefreshDue()) {
                await refreshEngagementBootstrap();
                if (myGen !== loadSuggestionsGeneration) return;
                engagementNextFetchAt = Date.now() + ENGAGEMENT_MIN_INTERVAL_MS;
                const deckSig = computeSwipeDeckSig();
                const needsDeckRebuild = deckSig !== lastSwipeDeckSig || swipeDeckItems.length === 0;
                if (needsDeckRebuild && !swipeUserInteracting) {
                    lastSwipeDeckSig = deckSig;
                    buildSwipeDeck();
                    renderSwipeView();
                } else if (needsDeckRebuild) pendingSwipeDeckRebuild = true;
            }
            return;
        }

        pollNoChangeStreak = 0;
        if (contentSig !== lastPollContentSig) pollBoostCycles = 3;
        lastPollContentSig = contentSig;
        pollFailStreak = 0;

        const merged = mergeLocalVoteHints(suggestionsRaw, proposalRaw);
        let suggestions = merged.suggestions.map((s) => {
            const prev = allSuggestions.find((x) => x.id === s.id);
            if (!prev) return s;
            return applyPollVoteMerge(prev, s);
        });
        suggestions = reconcileOptimisticVotes(suggestions);
        suggestions.forEach(normalizeSuggestionVoteState);
        const proposal = merged.proposal;
        syncVoteCacheFromServer(suggestions, proposalRaw);
        const newSig = buildIdsSignature(proposal, suggestions);
        const prevProposal = officialProposal;
        const proposalChanged = (!!proposal !== !!prevProposal) || (proposal && prevProposal && proposal.id !== prevProposal.id);

        const apiIds = new Set(suggestions.map((s) => s.id));
        for (const pid of _pendingSuggestionIds) {
            if (apiIds.has(pid)) {
                _pendingSuggestionIds.delete(pid);
            }
        }
        if (_pendingSuggestionIds.size > 0) {
            const kept = allSuggestions.filter((s) => s._pending && _pendingSuggestionIds.has(s.id));
            suggestions = [...kept, ...suggestions];
        }

        allSuggestions = suggestions;
        officialProposal = proposal;
        let swipeDeckRebuiltThisLoad = false;
        if (isTouchDevice()) {
            if (engagementRefreshDue() || reason !== "poll") {
                await refreshEngagementBootstrap();
                engagementNextFetchAt = Date.now() + ENGAGEMENT_MIN_INTERVAL_MS;
            }
            if (myGen !== loadSuggestionsGeneration) return;
            const deckSig = computeSwipeDeckSig();
            const needsDeckRebuild = deckSig !== lastSwipeDeckSig || swipeDeckItems.length === 0;
            if (needsDeckRebuild) {
                if (swipeUserInteracting) {
                    pendingSwipeDeckRebuild = true;
                } else {
                    lastSwipeDeckSig = deckSig;
                    buildSwipeDeck();
                    swipeDeckRebuiltThisLoad = true;
                    pendingSwipeDeckRebuild = false;
                }
            }
        }
        renderCvlOfficialInfo(cvlInfo || []);

        const skipHeavyDom = reason === "poll" && shouldDeferPollDom();
        if (skipHeavyDom) {
            deferredPollDomPending = true;
            scheduleDeferredPollDomApply();
        }

        const doDiscrete = async () => {
            reorderSuggestionCards._lastSig = null;
            lastIdsSignature = newSig;
            await renderSuggestionsDiscrete(reason === "poll");
        };

        if (!skipHeavyDom) {
            if (proposalChanged || !suggestionsContainer.children.length) {
                await doDiscrete();
            } else if (newSig === lastIdsSignature) {
                if (debateOrProposalStructureMismatch(proposal)) {
                    lastIdsSignature = newSig;
                    await renderSuggestionsDiscrete();
                } else if (isTouchDevice() && phoneUiMode === "list" && phoneListLikedOnly) {
                    lastIdsSignature = newSig;
                    await renderSuggestionsDiscrete();
                } else {
                    updateSuggestionsInPlace({ quiet: reason === "poll" });
                    reorderSuggestionCards();
                }
            } else {
                const showCount = expanded ? allSuggestions.length : INITIAL_SHOW;
                const visibleIds = allSuggestions.slice(0, showCount).map((s) => s.id);
                const domIds = [...suggestionsContainer.querySelectorAll(".suggestion-card[data-id]")].map((el) =>
                    parseInt(el.dataset.id, 10),
                );
                const visSet = new Set(visibleIds);
                const domSet = new Set(domIds);
                const visAdded = visibleIds.filter((id) => !domSet.has(id));
                const visRemoved = domIds.filter((id) => !visSet.has(id));

                if (visAdded.length === 0 && visRemoved.length === 0) {
                    lastIdsSignature = newSig;
                    if (debateOrProposalStructureMismatch(proposal)) await doDiscrete();
                    else {
                        updateSuggestionsInPlace({ quiet: reason === "poll" });
                        reorderSuggestionCards();
                    }
                } else if (visAdded.length === 1 && visRemoved.length === 0) {
                    lastIdsSignature = newSig;
                    updateSuggestionsInPlace({ quiet: reason === "poll" });
                    appendNewSuggestionsOnly();
                    reorderSuggestionCards();
                } else if (visRemoved.length === 1 && visAdded.length === 0) {
                    lastIdsSignature = newSig;
                    removeSuggestionCardSilently(visRemoved[0]);
                    updateSuggestionsInPlace({ quiet: reason === "poll" });
                    reorderSuggestionCards();
                } else if (visAdded.length === 1 && visRemoved.length === 1) {
                    lastIdsSignature = newSig;
                    removeSuggestionCardSilently(visRemoved[0]);
                    updateSuggestionsInPlace({ quiet: reason === "poll" });
                    appendNewSuggestionsOnly();
                    reorderSuggestionCards();
                } else {
                    await doDiscrete();
                }
            }
        }

        input.value = savedInput;
        submitBtn.disabled = savedInput.trim().length < 5 || savedInput.length > 500;
        charCount.textContent = `${savedInput.length} / 500`;
        Object.entries(savedArguments).forEach(([id, val]) => {
            const panel = suggestionsContainer.querySelector(`.cvl-argument-panel[data-suggestion-id="${id}"], .cvl-argument-panel[data-proposal-id="${id}"]`);
            const ta = panel?.querySelector(".cvl-argument-input");
            if (ta) ta.value = val;
        });
        if (hadFocus) input.focus();
        if (!skipHeavyDom && isTouchDevice() && phoneUiMode === "swipe") {
            if (pendingSwipeDeckRebuild && !swipeUserInteracting) {
                lastSwipeDeckSig = computeSwipeDeckSig();
                buildSwipeDeck();
                pendingSwipeDeckRebuild = false;
                swipeDeckRebuiltThisLoad = true;
            }
            if (swipeDeckRebuiltThisLoad) {
                renderSwipeView();
            }
        }
        if (!skipHeavyDom) {
            syncPollVoteDomToMatchSuggestions();
        }
        if (pollBoostCycles > 0) pollBoostCycles -= 1;
    } catch (err) {
        pollFailStreak++;
        console.warn("loadSuggestions", err);
    } finally {
        if (reason === "poll") scheduleNextPoll();
    }
}

function appendNewSuggestionsOnly() {
    const existingIds = new Set();
    suggestionsContainer.querySelectorAll(".suggestion-card[data-id], .suggestion-card-cvl[data-proposal-id]").forEach((el) => {
        if (el.dataset.id) existingIds.add("s" + el.dataset.id);
        else if (el.dataset.proposalId) existingIds.add("p" + el.dataset.proposalId);
    });
    const showCount = expanded ? allSuggestions.length : INITIAL_SHOW;
    const visible = allSuggestions.slice(0, showCount);
    const toAppend = visible.filter((s) => !existingIds.has("s" + s.id));
    if (!toAppend.length) return;

    const newCards = [];
    toAppend.forEach((s) => {
        const div = document.createElement("div");
        div.innerHTML = createSuggestionCard(s, visible.indexOf(s), showCount, false);
        const card = div.firstElementChild;
        card.classList.add("suggestion-card-new");
        newCards.push(card);
        suggestionsContainer.appendChild(card);
    });

    newCards.forEach((card) => {
        card.querySelectorAll(".cvl-vote-for, .cvl-vote-against").forEach((btn) => {
            btn.addEventListener("click", () => {
                if (btn.closest(".suggestion-card-cvl")) onProposalVoteClick(btn);
                else if (btn.closest(".suggestion-card-debate")) onSuggestionVoteClick(btn);
            });
        });
        card.querySelectorAll(".suggestion-vote-btn").forEach((btn) => {
            btn.addEventListener("click", () => {
                submitSuggestionVoteAction({ suggestionId: parseInt(btn.dataset.id, 10), mode: "simple_toggle" }).catch(
                    () => {},
                );
            });
        });
        card.querySelectorAll(".cvl-arguments-toggle").forEach((btn) => {
            btn.addEventListener("click", () => {
                const id = btn.dataset.id;
                const proposalId = btn.dataset.proposalId;
                const block = id
                    ? suggestionsContainer.querySelector(`.cvl-arguments[data-id="${id}"]`)
                    : suggestionsContainer.querySelector(`.cvl-arguments[data-proposal-id="${proposalId}"]`);
                if (!block) return;
                const isExpanded = block.classList.toggle("cvl-arguments-expanded");
                block.classList.toggle("cvl-arguments-collapsed", !isExpanded);
                btn.classList.toggle("expanded", isExpanded);
                btn.querySelector(".cvl-arguments-chevron").textContent = isExpanded ? "▼" : "▶";
                btn.setAttribute("aria-expanded", isExpanded);
            });
        });
        card.querySelectorAll(".cvl-argument-submit-suggestion").forEach((btn) => {
            btn.addEventListener("click", () => submitSuggestionVoteWithArgument(btn));
        });
        card.querySelectorAll(".cvl-add-arg-btn").forEach((btn) => {
            btn.addEventListener("click", () => {
                const panel = btn.closest(".cvl-add-arg-panel");
                const form = panel?.querySelector(".cvl-add-arg-form");
                if (form) {
                    form.classList.toggle("hidden");
                    if (!form.classList.contains("hidden")) form.querySelector("textarea")?.focus();
                }
            });
        });
        card.querySelectorAll(".cvl-add-arg-submit").forEach((btn) => {
            btn.addEventListener("click", () => submitAddArgument(btn));
        });
        setTimeout(() => card.classList.remove("suggestion-card-new"), 480);
    });

    if (allSuggestions.length > INITIAL_SHOW && !expanded) {
        fadeOverlay.classList.remove("hidden");
        expandText.textContent = `Voir les ${allSuggestions.length - INITIAL_SHOW} autres suggestions`;
    }
}

function syncSuggestionArgumentLists(card, s) {
    if (!s.needs_debate) return;
    const block = card.querySelector(`.cvl-arguments[data-id="${s.id}"]`);
    if (!block) return;
    const forUl = block.querySelector(".cvl-args-for ul");
    const againstUl = block.querySelector(".cvl-args-against ul");
    const forList = (s.arguments_for || []).map((a) => `<li class="cvl-arg-item cvl-arg-for">${escapeHtml(a.summary || a.original_text)}</li>`).join("");
    const againstList = (s.arguments_against || []).map((a) => `<li class="cvl-arg-item cvl-arg-against">${escapeHtml(a.summary || a.original_text)}</li>`).join("");
    if (forUl) forUl.innerHTML = forList || "<li class=\"none\">Aucun</li>";
    if (againstUl) againstUl.innerHTML = againstList || "<li class=\"none\">Aucun</li>";
}

function syncOfficialProposalArgumentLists(card, p) {
    if (!p.needs_debate) return;
    const block = card.querySelector(`.cvl-arguments[data-proposal-id="${p.id}"]`);
    if (!block) return;
    const forUl = block.querySelector(".cvl-args-for ul");
    const againstUl = block.querySelector(".cvl-args-against ul");
    const forList = (p.arguments_for || []).map((a) => `<li class="cvl-arg-item cvl-arg-for">${escapeHtml(a.summary || a.original_text)}</li>`).join("");
    const againstList = (p.arguments_against || []).map((a) => `<li class="cvl-arg-item cvl-arg-against">${escapeHtml(a.summary || a.original_text)}</li>`).join("");
    if (forUl) forUl.innerHTML = forList || "<li class=\"none\">Aucun</li>";
    if (againstUl) againstUl.innerHTML = againstList || "<li class=\"none\">Aucun</li>";
}

function updateSuggestionsInPlace(opts = {}) {
    const forceVoteUi = opts.forceVoteUi === true;
    const quiet = opts.quiet === true;
    const showCount = expanded ? allSuggestions.length : INITIAL_SHOW;
    const visible = allSuggestions.slice(0, showCount);
    visible.forEach((s) => {
        const card = suggestionsContainer.querySelector(`.suggestion-card[data-id="${s.id}"]`);
        if (!card) return;
        const liveSig = buildCardLiveSig(s);
        if (!voteLockOrClaimActive(s.id) && card.dataset.liveSig === liveSig) return;
        card.dataset.liveSig = liveSig;
        const icon = CATEGORY_ICONS[s.category] || "📌";
        const titleEl = card.querySelector(".suggestion-title");
        if (titleEl) {
            const t = `${icon} ${s.title}`;
            if (titleEl.textContent !== t) {
                if (!quiet) tickLiveField(titleEl);
            }
            titleEl.textContent = t;
        }
        let subEl = card.querySelector(".suggestion-subtitle");
        if (s.subtitle) {
            if (!subEl) {
                subEl = document.createElement("p");
                subEl.className = "suggestion-subtitle";
                titleEl?.parentNode?.insertBefore(subEl, titleEl.nextSibling);
            }
            subEl.textContent = s.subtitle;
        } else if (subEl) subEl.remove();

        const catBadge = card.querySelector(".badge-category");
        if (catBadge) catBadge.textContent = s.category;
        const stBadge = card.querySelector(".badge-status");
        if (stBadge) {
            stBadge.textContent = s.status;
            stBadge.setAttribute("data-status", s.status);
        }
        const voteLocked = voteLocksBySuggestionId.has(s.id) && !forceVoteUi;
        if (!s.needs_debate) {
            updateSimpleVoteControlDom(card, s, { silent: quiet });
        } else if (!voteLocked) {
            card.dataset.hasVoted = s.has_voted ? "true" : "false";
        }
        const meta = card.querySelector(".suggestion-meta");
        let locBadge = card.querySelector(".badge-location");
        if (s.location_name) {
            if (!locBadge && meta) {
                locBadge = document.createElement("span");
                locBadge.className = "badge badge-votes badge-location";
                meta.appendChild(locBadge);
            }
            if (locBadge) locBadge.textContent = `📍 ${s.location_name}`;
        } else if (locBadge) locBadge.remove();

        if (s.source === "nfc" && meta) {
            if (!card.querySelector(".badge-nfc-terrain")) {
                const nfcBadge = document.createElement("span");
                nfcBadge.className = "badge badge-nfc-terrain";
                nfcBadge.textContent = "📡 NFC";
                meta.appendChild(nfcBadge);
            }
            if (s.nfc_location_slug && !card.querySelector(".badge-nfc-link")) {
                const link = document.createElement("a");
                link.href = `/nfc/${s.nfc_location_slug}`;
                link.className = "badge badge-nfc-link";
                link.target = "_blank";
                link.textContent = "Voir sur le terrain ↗";
                link.addEventListener("click", e => e.stopPropagation());
                meta.appendChild(link);
            }
        }

        const voteFor = card.querySelector(".cvl-vote-for[data-id]");
        const voteAgainst = card.querySelector(".cvl-vote-against[data-id]");
        const voteCount = card.querySelector(".suggestion-vote-count");
        if (voteFor && !voteLocked) {
            const t = `Pour ${s.vote_for || 0}`;
            if (voteFor.textContent !== t) {
                if (!quiet) tickLiveField(voteFor);
            }
            voteFor.textContent = t;
            voteFor.classList.toggle("active", s.my_vote === "for");
        }
        if (voteAgainst && !voteLocked) {
            const t = `Contre ${s.vote_against || 0}`;
            if (voteAgainst.textContent !== t) {
                if (!quiet) tickLiveField(voteAgainst);
            }
            voteAgainst.textContent = t;
            voteAgainst.classList.toggle("active", s.my_vote === "against");
        }
        if (voteCount && !voteLocked) {
            const t = `${s.vote_count} soutien${s.vote_count !== 1 ? "s" : ""}${s.has_voted ? " · Soutenu" : ""}`;
            if (voteCount.textContent !== t) {
                if (!quiet) tickLiveField(voteCount);
            }
            voteCount.textContent = t;
        }
        const argsToggle = card.querySelector(".cvl-arguments-toggle[data-id]");
        if (argsToggle) {
            const total = (s.arguments_for?.length || 0) + (s.arguments_against?.length || 0);
            const lab = argsToggle.querySelector(".cvl-arguments-label");
            if (lab) lab.textContent = `Voir les arguments (${total})`;
        }
        syncSuggestionArgumentLists(card, s);
    });
    if (officialProposal) {
        const card = suggestionsContainer.querySelector(`.suggestion-card-cvl[data-proposal-id="${officialProposal.id}"]`);
        if (card) {
            const pSig = buildProposalLiveSig(officialProposal);
            if (card.dataset.liveSig === pSig) {
                return;
            }
            card.dataset.liveSig = pSig;
            const contentEl = card.querySelector(".cvl-content");
            if (contentEl && officialProposal.content != null) contentEl.innerHTML = officialProposal.content;
            const stBadge = card.querySelector(".badge-cvl");
            if (stBadge) stBadge.textContent = officialProposal.status;
            const voteFor = card.querySelector(".cvl-vote-for[data-vote='for']");
            const voteAgainst = card.querySelector(".cvl-vote-against[data-vote='against']");
            const voteSimple = card.querySelector(".cvl-votes-simple .cvl-vote-for");
            if (voteFor) {
                const t = `Pour ${officialProposal.vote_for}`;
                if (voteFor.textContent !== t) {
                    if (!quiet) tickLiveField(voteFor);
                }
                voteFor.textContent = t;
                voteFor.classList.toggle("active", officialProposal.my_vote === "for");
            }
            if (voteAgainst) {
                const t = `Contre ${officialProposal.vote_against}`;
                if (voteAgainst.textContent !== t) {
                    if (!quiet) tickLiveField(voteAgainst);
                }
                voteAgainst.textContent = t;
                voteAgainst.classList.toggle("active", officialProposal.my_vote === "against");
            }
            if (voteSimple && !officialProposal.needs_debate) {
                const t = `Soutenir · ${officialProposal.vote_for}`;
                if (voteSimple.textContent !== t) {
                    if (!quiet) tickLiveField(voteSimple);
                }
                voteSimple.textContent = t;
            }
            const argsToggle = card.querySelector(".cvl-arguments-toggle[data-proposal-id]");
            if (argsToggle && officialProposal.needs_debate) {
                const total = (officialProposal.arguments_for?.length || 0) + (officialProposal.arguments_against?.length || 0);
                const lab = argsToggle.querySelector(".cvl-arguments-label");
                if (lab) lab.textContent = `Voir les arguments (${total})`;
            }
            syncOfficialProposalArgumentLists(card, officialProposal);
        }
    }
}

/** @param silentPoll — rafraîchissement auto : pas de depop / pop / entrée (évite sauts visuels). */
async function renderSuggestionsDiscrete(silentPoll = false) {
    const container = suggestionsContainer;
    if (silentPoll) {
        // NFC-V2.4: invisible refresh — preserve scroll & height, no animation
        const scrollY = window.scrollY;
        container.style.minHeight = container.offsetHeight + "px";
        renderSuggestions(false);
        requestAnimationFrame(() => {
            container.style.minHeight = "";
            window.scrollTo({ top: scrollY, behavior: "instant" });
        });
        return;
    }
    const hadDepop = container.classList.contains("suggestions-depop");
    if (!hadDepop && container.children.length > 0) {
        container.classList.add("suggestions-depop");
        await new Promise((r) => setTimeout(r, 180));
    }
    renderSuggestions(true);
    container.classList.remove("suggestions-depop");
    container.classList.add("suggestions-pop");
    requestAnimationFrame(() => {
        requestAnimationFrame(() => container.classList.remove("suggestions-pop"));
    });
}

function renderSuggestionsSilent() {
    renderSuggestions(false);
}

function getListSourceForRender() {
    let base = allSuggestions;
    if (isTouchDevice() && phoneUiMode === "list" && phoneListLikedOnly) {
        base = base.filter((s) => s.has_voted);
    }
    const q = (phoneListSearchQuery || "").trim().toLowerCase();
    if (q) {
        base = base.filter((s) => {
            const hay = `${s.title || ""} ${s.subtitle || ""} ${s.category || ""}`.toLowerCase();
            return hay.includes(q);
        });
    }
    return base;
}

function renderSuggestions(withAnimation = true) {
    if (isTouchDevice() && phoneUiMode === "swipe") {
        syncPhoneUiChrome();
        renderSwipeView();
        return;
    }
    syncPhoneUiChrome();

    suggestionsContainer.classList.toggle("suggestions-no-anim", !withAnimation);
    const listSource = getListSourceForRender();
    let html = "";
    if (officialProposal) {
        html += createOfficialProposalCard(officialProposal);
    }
    if (!listSource.length && !officialProposal) {
        suggestionsContainer.innerHTML = "";
        emptyState.classList.remove("hidden");
        fadeOverlay.classList.add("hidden");
        if (withAnimation) {
            suggestionsContainer.classList.remove("suggestions-no-anim");
        }
        return;
    }
    emptyState.classList.add("hidden");

    if (isTouchDevice() && phoneUiMode === "list" && phoneListLikedOnly) {
        const myMood = engagementBootstrap?.my_mood;
        if (myMood) html += createMoodResultCard(myMood);
    }

    const showCount = expanded ? listSource.length : Math.min(INITIAL_SHOW, listSource.length);
    const visible = listSource.slice(0, showCount);
    html += visible.map((s, i) => createSuggestionCard(s, i, showCount, withAnimation, listSource.length)).join("");

    suggestionsContainer.innerHTML = html;

    suggestionsContainer.querySelectorAll(".cvl-vote-for, .cvl-vote-against").forEach((btn) => {
        btn.addEventListener("click", () => {
            if (btn.closest(".suggestion-card-cvl")) onProposalVoteClick(btn);
            else if (btn.closest(".suggestion-card-debate")) onSuggestionVoteClick(btn);
        });
    });
    suggestionsContainer.querySelectorAll(".cvl-argument-submit").forEach((btn) => {
        btn.addEventListener("click", () => submitProposalVoteWithArgument(btn));
    });
    suggestionsContainer.querySelectorAll(".cvl-argument-submit-suggestion").forEach((btn) => {
        btn.addEventListener("click", () => submitSuggestionVoteWithArgument(btn));
    });
    suggestionsContainer.querySelectorAll(".cvl-add-arg-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            const panel = btn.closest(".cvl-add-arg-panel");
            const form = panel?.querySelector(".cvl-add-arg-form");
            if (form) {
                form.classList.toggle("hidden");
                if (!form.classList.contains("hidden")) form.querySelector("textarea")?.focus();
            }
        });
    });
    suggestionsContainer.querySelectorAll(".cvl-add-arg-submit").forEach((btn) => {
        if (btn.classList.contains("cvl-official-add-arg-submit")) return;
        btn.addEventListener("click", () => submitAddArgument(btn));
    });
    suggestionsContainer.querySelectorAll(".cvl-official-add-arg-submit").forEach((btn) => {
        btn.addEventListener("click", () => submitOfficialAddArgument(btn));
    });
    suggestionsContainer.querySelectorAll(".suggestion-vote-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            submitSuggestionVoteAction({ suggestionId: parseInt(btn.dataset.id, 10), mode: "simple_toggle" }).catch(
                () => {},
            );
        });
    });
    suggestionsContainer.querySelectorAll(".cvl-arguments-toggle").forEach((btn) => {
        btn.addEventListener("click", () => {
            const id = btn.dataset.id;
            const proposalId = btn.dataset.proposalId;
            const block = id
                ? suggestionsContainer.querySelector(`.cvl-arguments[data-id="${id}"]`)
                : suggestionsContainer.querySelector(`.cvl-arguments[data-proposal-id="${proposalId}"]`);
            if (!block) return;
            const isExpanded = block.classList.toggle("cvl-arguments-expanded");
            block.classList.toggle("cvl-arguments-collapsed", !isExpanded);
            btn.classList.toggle("expanded", isExpanded);
            btn.querySelector(".cvl-arguments-chevron").textContent = isExpanded ? "▼" : "▶";
            btn.setAttribute("aria-expanded", isExpanded);
        });
    });

    if (withAnimation) {
        suggestionsContainer.classList.remove("suggestions-no-anim");
    }

    // Fade overlay
    const listSourceLen = getListSourceForRender().length;
    if (listSourceLen > INITIAL_SHOW && !expanded) {
        fadeOverlay.classList.remove("hidden");
        expandText.textContent = `Voir les ${listSourceLen - INITIAL_SHOW} autres suggestions`;
    } else if (expanded && listSourceLen > INITIAL_SHOW) {
        fadeOverlay.classList.remove("hidden");
        expandText.textContent = "Réduire";
        $(".expand-arrow").textContent = "↑";
    } else {
        fadeOverlay.classList.add("hidden");
    }
}

function createOfficialProposalCard(p) {
    const needsDebate = !!p.needs_debate;
    const forActive = p.my_vote === "for" ? " active" : "";
    const againstActive = p.my_vote === "against" ? " active" : "";
    const hasVoted = !!p.my_vote;

    let votesHtml;
    if (needsDebate) {
        votesHtml = `
            <div class="cvl-votes">
                <button class="cvl-vote-for${forActive}" data-vote="for">Pour ${p.vote_for}</button>
                <button class="cvl-vote-against${againstActive}" data-vote="against">Contre ${p.vote_against}</button>
            </div>
        `;
    } else {
        votesHtml = `
            <div class="cvl-votes cvl-votes-simple">
                <button class="cvl-vote-for${forActive}" data-vote="for">Soutenir · ${p.vote_for}</button>
            </div>
        `;
    }

    let argsHtml = "";
    if (needsDebate) {
        const forList = (p.arguments_for || []).map(a => `<li class="cvl-arg-item cvl-arg-for">${escapeHtml(a.summary || a.original_text)}</li>`).join("");
        const againstList = (p.arguments_against || []).map(a => `<li class="cvl-arg-item cvl-arg-against">${escapeHtml(a.summary || a.original_text)}</li>`).join("");
        const totalArgs = (p.arguments_for?.length || 0) + (p.arguments_against?.length || 0);
        argsHtml = `
            <div class="cvl-arguments-wrap">
                <button type="button" class="cvl-arguments-toggle" data-proposal-id="${p.id}" aria-expanded="false">
                    <span class="cvl-arguments-chevron">▶</span>
                    <span class="cvl-arguments-label">Voir les arguments (${totalArgs})</span>
                </button>
                <div class="cvl-arguments cvl-arguments-collapsed" data-proposal-id="${p.id}">
                    <div class="cvl-args-for"><strong>Pour :</strong><ul>${forList || "<li class=\"none\">Aucun</li>"}</ul></div>
                    <div class="cvl-args-against"><strong>Contre :</strong><ul>${againstList || "<li class=\"none\">Aucun</li>"}</ul></div>
                </div>
            </div>
        `;
    }

    const argumentPanelHtml = needsDebate && !hasVoted ? `
        <div class="cvl-argument-panel hidden" data-proposal-id="${p.id}">
            <p class="cvl-argument-prompt"></p>
            <textarea class="cvl-argument-input" placeholder="Votre argument (optionnel)..." rows="2" maxlength="500"></textarea>
            <button class="btn btn-sm cvl-argument-submit">Soumettre</button>
        </div>
    ` : "";

    const addArgOfficialHtml = needsDebate && hasVoted ? `
        <div class="cvl-add-arg-panel cvl-official-add-arg" data-proposal-id="${p.id}">
            <button type="button" class="cvl-add-arg-btn">+ Ajouter un argument ${p.my_vote === "for" ? "pour" : "contre"}</button>
            <div class="cvl-argument-panel hidden cvl-add-arg-form" data-proposal-id="${p.id}">
                <textarea class="cvl-argument-input" placeholder="Votre argument (rédigé comme pour un vote)..." rows="3" maxlength="500"></textarea>
                <button type="button" class="btn btn-sm cvl-official-add-arg-submit">Envoyer l'argument</button>
            </div>
        </div>
    ` : "";

    return `
        <div class="suggestion-card suggestion-card-cvl" data-proposal-id="${p.id}">
            <div class="cvl-logo">Proposition Officielle CVL</div>
            <div class="suggestion-card-header">
                <div class="suggestion-title-block cvl-content">${p.content || ""}</div>
                ${votesHtml}
            </div>
            ${argumentPanelHtml}
            ${addArgOfficialHtml}
            ${argsHtml}
            <div class="suggestion-meta">
                <span class="badge badge-cvl">${p.status}</span>
            </div>
        </div>
    `;
}

function onProposalVoteClick(btn) {
    const vote = btn.dataset.vote;
    const card = btn.closest(".suggestion-card-cvl");
    const panel = card?.querySelector(".cvl-argument-panel");
    const hasVoted = !!officialProposal?.my_vote;

    if (!officialProposal?.needs_debate || !panel) {
        voteProposal(vote);
        return;
    }

    if (btn.classList.contains("active")) return;

    if (hasVoted && officialProposal.my_vote !== vote) {
        voteProposal(vote);
        return;
    }

    const prompt = panel.querySelector(".cvl-argument-prompt");
    prompt.textContent = vote === "for"
        ? "Avez-vous un argument pour ? (optionnel)"
        : "Avez-vous un argument contre ? (optionnel)";
    panel.dataset.pendingVote = vote;
    panel.classList.remove("hidden");
    panel.querySelector(".cvl-argument-input").value = "";
}

async function submitProposalVoteWithArgument(btn) {
    const panel = btn.closest(".cvl-argument-panel");
    const vote = panel?.dataset.pendingVote;
    const input = panel?.querySelector(".cvl-argument-input");
    const argument = input?.value?.trim() || "";

    if (!vote) return;
    btn.disabled = true;
    try {
        const { data, status } = await API.post("/api/official-proposal/vote", { vote, argument });
        if (status === 429) {
            showFeedback((data && data.error) || "Trop de requêtes. Réessayez plus tard.", "error");
            return;
        }
        if (status >= 200 && status < 400 && data) {
            officialProposal = {
                ...officialProposal,
                vote_for: data.vote_for,
                vote_against: data.vote_against,
                my_vote: data.my_vote,
                arguments_for: data.arguments_for || [],
                arguments_against: data.arguments_against || [],
            };
            syncVoteCacheFromServer(allSuggestions, officialProposal);
            panel.classList.add("hidden");
            renderSuggestionsSilent();
        }
    } catch (err) { console.error(err); }
    btn.disabled = false;
}

function onSuggestionVoteClick(btn) {
    const vote = btn.dataset.vote;
    const id = parseInt(btn.dataset.id, 10);
    if (voteLocksBySuggestionId.has(id)) return;
    const card = btn.closest(".suggestion-card-debate");
    const panel = card?.querySelector(".cvl-argument-panel");
    const suggestion = allSuggestions.find((s) => s.id === id);
    if (!suggestion?.needs_debate) {
        void submitSuggestionVoteAction({ suggestionId: id, mode: "simple_toggle" }).catch(() => {});
        return;
    }
    if (!panel) {
        voteSuggestion(id, vote);
        return;
    }
    if (btn.classList.contains("active")) return;
    if (suggestion.has_voted && suggestion.my_vote !== vote) {
        voteSuggestion(id, vote);
        return;
    }
    const prompt = panel.querySelector(".cvl-argument-prompt");
    prompt.textContent = vote === "for"
        ? "Avez-vous un argument pour ? (optionnel)"
        : "Avez-vous un argument contre ? (optionnel)";
    panel.dataset.pendingVote = vote;
    panel.classList.remove("hidden");
    panel.querySelector(".cvl-argument-input").value = "";
}

async function submitSuggestionVoteWithArgument(btn) {
    const panel = btn.closest(".cvl-argument-panel");
    const id = parseInt(String(panel?.dataset.suggestionId || ""), 10);
    const vote = panel?.dataset.pendingVote;
    const input = panel?.querySelector(".cvl-argument-input");
    const argument = input?.value?.trim() || "";
    if (!Number.isFinite(id) || !vote) {
        showFeedback("Choisis d’abord Pour ou Contre, puis valide.", "warning");
        return;
    }
    btn.disabled = true;
    try {
        const ok = await voteSuggestion(id, vote, argument);
        if (ok) panel?.classList.add("hidden");
    } catch (err) {
        console.warn("submitSuggestionVoteWithArgument", err);
        showFeedback("Erreur de connexion.", "error");
    }
    btn.disabled = false;
}

async function submitAddArgument(btn) {
    const form = btn.closest(".cvl-add-arg-form");
    const panel = btn.closest(".cvl-add-arg-panel");
    const id = parseInt(String(panel?.dataset.suggestionId || ""), 10);
    const ta = form?.querySelector(".cvl-argument-input");
    const argument = ta?.value?.trim() || "";
    const s = allSuggestions.find((x) => x.id === id);
    if (!Number.isFinite(id) || !argument || argument.length < 5 || !s?.has_voted) {
        if (argument && argument.length < 5) showFeedback("Argument trop court (5 caractères min).", "warning");
        return;
    }
    btn.disabled = true;
    try {
        const { data, status } = await API.post(`/api/suggestions/${id}/argument`, {
            side: s.my_vote,
            argument,
        });
        if (status === 429) {
            showFeedback((data && data.error) || "Trop de requêtes.", "error");
            return;
        }
        if (data && data.error && status >= 400) {
            showFeedback(data.error, "error");
            return;
        }
        if (s && status === 200) {
            s.vote_for = data.vote_for ?? s.vote_for;
            s.vote_against = data.vote_against ?? s.vote_against;
            s.arguments_for = data.arguments_for || [];
            s.arguments_against = data.arguments_against || [];
        }
        ta.value = "";
        form?.classList.add("hidden");
        syncVoteCacheFromServer(allSuggestions, officialProposal);
        renderSuggestionsSilent();
    } catch (err) { console.error(err); }
    btn.disabled = false;
}

async function submitOfficialAddArgument(btn) {
    const form = btn.closest(".cvl-add-arg-form");
    const ta = form?.querySelector(".cvl-argument-input");
    const argument = ta?.value?.trim() || "";
    if (!officialProposal || !argument || !officialProposal.my_vote) return;
    btn.disabled = true;
    try {
        const { data, status } = await API.post("/api/official-proposal/argument", {
            side: officialProposal.my_vote,
            argument,
        });
        if (status === 429) {
            showFeedback((data && data.error) || "Trop de requêtes.", "error");
            return;
        }
        if (status >= 200 && status < 400 && data) {
            officialProposal = {
                ...officialProposal,
                vote_for: data.vote_for,
                vote_against: data.vote_against,
                arguments_for: data.arguments_for || [],
                arguments_against: data.arguments_against || [],
            };
            ta.value = "";
            form?.classList.add("hidden");
            syncVoteCacheFromServer(allSuggestions, officialProposal);
            renderSuggestionsSilent();
        }
    } catch (err) { console.error(err); }
    btn.disabled = false;
}

async function voteProposal(vote, argument = "") {
    if (pendingOfficialProposalVote) return;
    pendingOfficialProposalVote = true;
    try {
        const { data, status } = await API.post("/api/official-proposal/vote", { vote, argument: argument || undefined });
        if (status === 429) {
            showFeedback((data && data.error) || "Trop de requêtes.", "error");
            return;
        }
        if (data && !data.error) {
            officialProposal = {
                ...officialProposal,
                vote_for: data.vote_for,
                vote_against: data.vote_against,
                my_vote: data.my_vote,
                arguments_for: data.arguments_for || [],
                arguments_against: data.arguments_against || [],
            };
            syncVoteCacheFromServer(allSuggestions, officialProposal);
            renderSuggestionsSilent();
        }
    } catch (err) { console.error(err); }
    finally {
        pendingOfficialProposalVote = false;
    }
}

function createSuggestionCard(s, index, totalVisible, withAnimation = true, totalListCount = null) {
    const icon = CATEGORY_ICONS[s.category] || "📌";
    const needsDebate = !!s.needs_debate;
    const votedClass = s.has_voted ? " voted" : "";
    const forActive = s.my_vote === "for" ? " active" : "";
    const againstActive = s.my_vote === "against" ? " active" : "";

    const listLen = totalListCount != null ? totalListCount : allSuggestions.length;
    const isFading = withAnimation && !expanded && index === INITIAL_SHOW - 1 && totalVisible === INITIAL_SHOW && listLen > INITIAL_SHOW;
    const fadeClass = isFading ? " suggestion-card-fade" : "";
    const hotClass = s.hot ? " suggestion-card-hot" : "";
    const userVotedBand = !needsDebate && s.has_voted === true ? " suggestion-card--user-voted" : "";
    const hvAttr = s.has_voted ? "true" : "false";
    const delay = withAnimation ? index * 60 : 0;

    const subtitle = s.subtitle ? `<p class="suggestion-subtitle">${escapeHtml(s.subtitle)}</p>` : "";

    let termTimer = "";
    if (s.status === "Terminée" && s.terminée_seconds_remaining != null && s.terminée_seconds_remaining > 0) {
        const total = s.terminée_seconds_remaining;
        const h = Math.floor(total / 3600);
        const m = Math.floor((total % 3600) / 60);
        const sec = total % 60;
        const label = h > 0 ? `${h}h ${m}m` : `${m}m ${sec}s`;
        termTimer = `<span class="badge badge-terminée-timer" title="Après ce délai, la suggestion disparaît de cette page et du display TV, puis part en calibration IA.">⏱ Visible encore ${label}</span>`;
    }

    let voteBtn;
    if (needsDebate) {
        voteBtn = `
            <div class="cvl-votes">
                <button class="cvl-vote-for${forActive}" data-id="${s.id}" data-vote="for">Pour ${s.vote_for || 0}</button>
                <button class="cvl-vote-against${againstActive}" data-id="${s.id}" data-vote="against">Contre ${s.vote_against || 0}</button>
            </div>
        `;
    } else if (s.status === "Terminée") {
        voteBtn = `
        <div class="hype-wrap" data-id="${s.id}">
            <button type="button" class="hype-btn" data-id="${s.id}" aria-label="Hype">
                <span class="hype-flame">🔥</span>
                <span class="hype-count">${s.hype_count || 0}</span>
            </button>
            <span class="hype-label">Hype</span>
        </div>`;
    } else {
        const usePhoneList = isTouchDevice() && phoneUiMode === "list";
        const wrapVotedClass = s.has_voted ? " suggestion-vote-wrap--voted" : "";
        const phoneListClass = usePhoneList ? " suggestion-vote-wrap--phone-list" : "";
        const idleClass = usePhoneList && !s.has_voted ? " suggestion-vote-btn--phone-idle" : "";
        const ariaVote = s.has_voted
            ? "Retirer mon soutien"
            : `Soutenir — ${s.vote_count} soutien${s.vote_count !== 1 ? "s" : ""} actuellement`;
        const btnText = usePhoneList
            ? ""
            : s.has_voted
              ? `✓ Soutenu · ${s.vote_count}`
              : `♥ Soutenir · ${s.vote_count}`;
        const btnIcon = usePhoneList ? (s.has_voted ? "♥" : "♡") : "";
        const countBelowHtml = usePhoneList ? s.vote_count : "";
        voteBtn = `
        <div class="suggestion-vote-wrap${wrapVotedClass}${phoneListClass}" data-has-voted="${hvAttr}">
            <button type="button" class="suggestion-vote-btn${votedClass}${idleClass}" data-id="${s.id}" data-has-voted="${hvAttr}" aria-pressed="${s.has_voted ? "true" : "false"}" aria-label="${escapeHtml(ariaVote)}">
                <span class="suggestion-vote-btn-text">${btnText}</span>
                <span class="suggestion-vote-btn-icon" aria-hidden="true">${btnIcon}</span>
            </button>
            <span class="suggestion-vote-count-below">${countBelowHtml}</span>
        </div>`;
    }

    let argsHtml = "";
    if (needsDebate) {
        const forList = (s.arguments_for || []).map(a => `<li class="cvl-arg-item cvl-arg-for">${escapeHtml(a.summary || a.original_text)}</li>`).join("");
        const againstList = (s.arguments_against || []).map(a => `<li class="cvl-arg-item cvl-arg-against">${escapeHtml(a.summary || a.original_text)}</li>`).join("");
        const totalArgs = (s.arguments_for?.length || 0) + (s.arguments_against?.length || 0);
        argsHtml = `
            <div class="cvl-arguments-wrap">
                <button type="button" class="cvl-arguments-toggle" data-id="${s.id}" aria-expanded="false">
                    <span class="cvl-arguments-chevron">▶</span>
                    <span class="cvl-arguments-label">Voir les arguments (${totalArgs})</span>
                </button>
                <div class="cvl-arguments cvl-arguments-collapsed" data-id="${s.id}">
                    <div class="cvl-args-for"><strong>Pour :</strong><ul>${forList || "<li class=\"none\">Aucun</li>"}</ul></div>
                    <div class="cvl-args-against"><strong>Contre :</strong><ul>${againstList || "<li class=\"none\">Aucun</li>"}</ul></div>
                </div>
            </div>
        `;
    }

    const argumentPanelHtml = needsDebate && !s.has_voted ? `
        <div class="cvl-argument-panel hidden" data-suggestion-id="${s.id}">
            <p class="cvl-argument-prompt"></p>
            <textarea class="cvl-argument-input" placeholder="Votre argument (optionnel)..." rows="2" maxlength="300"></textarea>
            <button class="btn btn-sm cvl-argument-submit cvl-argument-submit-suggestion">Soumettre</button>
        </div>
    ` : "";
    const addArgPanelHtml = needsDebate && s.has_voted ? `
        <div class="cvl-add-arg-panel" data-suggestion-id="${s.id}">
            <button type="button" class="cvl-add-arg-btn">+ Ajouter un argument ${s.my_vote === "for" ? "pour" : "contre"}</button>
            <div class="cvl-argument-panel hidden cvl-add-arg-form" data-suggestion-id="${s.id}">
                <textarea class="cvl-argument-input" placeholder="Votre argument..." rows="2" maxlength="300"></textarea>
                <button class="btn btn-sm cvl-argument-submit cvl-add-arg-submit">Envoyer</button>
            </div>
        </div>
    ` : "";

    return `
        <div class="suggestion-card${fadeClass}${needsDebate ? " suggestion-card-debate" : ""}${hotClass}${userVotedBand}" style="animation-delay:${delay}ms" data-id="${s.id}" data-has-voted="${hvAttr}">
            <div class="suggestion-card-header">
                <div class="suggestion-title-block">
                    <span class="suggestion-title">${icon} ${escapeHtml(s.title)}</span>
                    ${subtitle}
                </div>
                ${voteBtn}
            </div>
            ${argumentPanelHtml}
            ${addArgPanelHtml}
            ${argsHtml}
            <div class="suggestion-meta">
                ${s.status === "En attente" ? '<span class="badge badge-processing">⏳ En cours de traitement</span>' : `<span class="badge badge-category">${s.category}</span><span class="badge badge-status" data-status="${s.status}">${s.status}</span>`}
                ${termTimer}
                ${s.location_name ? `<span class="badge badge-votes badge-location">📍 ${escapeHtml(s.location_name)}</span>` : ""}
                ${s.source === "nfc" ? `<span class="badge badge-nfc-terrain">📡 NFC</span>` : ""}
                ${s.source === "nfc" && s.nfc_location_name ? `<span class="badge badge-nfc-lieu">📍 ${escapeHtml(s.nfc_location_name)}${s.nfc_building ? " · " + escapeHtml(s.nfc_building) : ""}${s.nfc_floor ? " · " + escapeHtml(s.nfc_floor) : ""}</span>` : ""}
                ${s.source === "nfc" && s.nfc_location_slug ? `<a href="/nfc/${escapeHtml(s.nfc_location_slug)}" class="badge badge-nfc-link" target="_blank" onclick="event.stopPropagation()">Voir sur le terrain ↗</a>` : ""}
                ${s.source === "nfc" && s.heat_score != null ? `<span class="badge badge-nfc-heat">🔥 ${s.heat_score}pts${s.heat_level === "urgent" ? " ⚠️" : s.heat_level === "important" ? " 🔶" : ""}</span>` : ""}
            </div>
            <div class="suggestion-heart-burst" aria-hidden="true"></div>
        </div>
    `;
}

function toggleExpand() {
    expanded = !expanded;
    $(".expand-arrow").textContent = expanded ? "↑" : "↓";
    renderSuggestions();
}

// --------------- Pending card après envoi instantané ---------------

function _injectPendingSuggestion(suggestion) {
    const pending = {
        id: suggestion.id,
        title: suggestion.title || suggestion.original_text || "Suggestion en cours…",
        original_text: suggestion.original_text || "",
        category: null,
        status: "En attente",
        vote_count: suggestion.vote_count || 1,
        needs_debate: false,
        location_name: null,
        has_voted: true,
        my_vote: "for",
        _pending: true,
    };
    _pendingSuggestionIds.add(suggestion.id);
    allSuggestions.unshift(pending);
    renderSuggestions();
}

// --------------- Submit Flow ---------------

async function handleSubmit(opts = {}) {
    const text = input.value.trim();
    if (!text || text.length < 5 || !submissionsOpen) return;

    setLoading(true);
    hideFeedback();
    hidePrecisionModal();

    try {
        const payload = { text, ...opts };
        const { data, status } = await API.post("/api/suggestions/submit", payload);

        if (status === 429) {
            showFeedback(data.error || "Trop de requêtes ou trop de soumissions. Réessayez plus tard.", "error");
            setLoading(false);
            return;
        }
        if (status === 201 || status === 200) {
            if (data.status === "ask_precision") {
                showPrecisionModal(data, text);
                setLoading(false);
                return;
            }
            clearInput();
            let msg = data.message || "Votre suggestion a bien été soumise !";
            showFeedback(msg, "success");
            setLoading(false);
            if (data.status === "submitted" && data.suggestion) {
                _injectPendingSuggestion(data.suggestion);
            }
            loadSuggestions({ reason: "user" });
        } else {
            showFeedback(data.error || "Erreur lors de la soumission.", "error");
        }
    } catch (err) {
        showFeedback("Erreur de connexion au serveur.", "error");
    }

    setLoading(false);
}

function showPrecisionModal(data, text) {
    const modal = document.getElementById("precision-modal");
    if (!modal) return;
    const msgEl = modal.querySelector(".precision-message");
    if (msgEl) msgEl.textContent = `Une suggestion similaire existe : « ${data.existing_title || ""} ». Votre message apporte-t-il des précisions sur ce problème ?`;
    modal.dataset.existingId = data.existing?.id || "";
    modal.dataset.pendingText = text;
    modal.classList.remove("hidden");
}

function hidePrecisionModal() {
    const modal = document.getElementById("precision-modal");
    if (modal) modal.classList.add("hidden");
}

async function onPrecisionChoice(isPrecision) {
    const modal = document.getElementById("precision-modal");
    if (!modal) return;
    const text = modal.dataset.pendingText || "";
    const existingId = parseInt(modal.dataset.existingId);
    hidePrecisionModal();
    if (!text) return;
    setLoading(true);
    try {
        const payload = isPrecision
            ? { text, confirm_precision: true, existing_id: existingId }
            : { text, force_new: true };
        const { data, status } = await API.post("/api/suggestions/submit", payload);
        if (status === 429) {
            showFeedback(data.error || "Trop de requêtes. Réessayez plus tard.", "error");
            setLoading(false);
            return;
        }
        if (status === 201 || status === 200) {
            clearInput();
            showFeedback(data.message || "C'est enregistré !", "success");
            loadSuggestions();
        } else {
            showFeedback(data.error || "Erreur.", "error");
        }
    } catch (err) {
        showFeedback("Erreur de connexion.", "error");
    }
    setLoading(false);
}

// --------------- Vote — couche unique ----------------

/** Carte swipe active (layer courant) — même cible que patchSwipeVoteUiForCurrentCard. */
function getSwipeActiveCardForSuggestion(suggestionId) {
    if (!swipeDeckInner) return null;
    return (
        swipeDeckInner.querySelector(`#swipe-active-layer .swipe-card[data-id="${suggestionId}"]`) ||
        swipeDeckInner.querySelector(`.swipe-card[data-id="${suggestionId}"]`)
    );
}

/**
 * Nœud pour lire data-has-voted (toggle) : voteDomCard si fourni, sinon swipe active, sinon carte liste, sinon bouton.
 */
function getDomNodeForToggleVote(suggestionId, voteDomCard) {
    if (voteDomCard) {
        const hid = voteDomCard.dataset?.id != null ? parseInt(voteDomCard.dataset.id, 10) : NaN;
        if (hid === suggestionId) return voteDomCard;
    }
    if (isTouchDevice() && phoneUiMode === "swipe") {
        const c = getSwipeActiveCardForSuggestion(suggestionId);
        if (c) return c;
    }
    const listCard = suggestionsContainer.querySelector(`.suggestion-card[data-id="${suggestionId}"]`);
    if (listCard) return listCard;
    return suggestionsContainer.querySelector(`.suggestion-vote-btn[data-id="${suggestionId}"]`);
}

/**
 * removeVote pour POST : true = retirer le soutien. Basé sur data-has-voted du nœud actif uniquement ;
 * si l’attribut manque, repli sur s.has_voted (jamais seul comme règle principale quand data est présent).
 */
function removeVoteFromDataHasVotedAttribute(suggestionId, voteDomCard, s) {
    const el = getDomNodeForToggleVote(suggestionId, voteDomCard);
    if (!el) return s.has_voted === true;
    const raw = el.dataset.hasVoted;
    if (raw === "true") return true;
    if (raw === "false") return false;
    return s.has_voted === true;
}

function rollbackVoteDomForSuggestion(suggestionId) {
    const s = allSuggestions.find((x) => x.id === suggestionId);
    if (!s) return;
    if (isTouchDevice() && phoneUiMode === "swipe") {
        patchSwipeVoteUiForCurrentCard();
    } else if (s.needs_debate) {
        updateSuggestionsInPlace({ forceVoteUi: true });
    } else {
        syncSimpleVoteListCardDom(s);
    }
}

/** Normalise has_voted / my_vote après fetch ou merge poll (évite undefined, chaînes, etc.). */
function normalizeSuggestionVoteState(s) {
    if (!s) return;
    s.has_voted = s.has_voted === true;
    if (!s.has_voted) s.my_vote = null;
    else if (s.my_vote == null || s.my_vote === "") s.my_vote = "for";
}

/** Après réponse vote : allSuggestions est déjà à jour — recopie sur liste + swipe (data-has-voted, classes, compteurs). */
function refreshVoteDomAfterSuggestionAction(s) {
    if (s.needs_debate) {
        if (isTouchDevice() && phoneUiMode === "swipe") {
            patchSwipeVoteUiForCurrentCard();
        } else {
            renderSuggestionsSilent();
        }
        return;
    }
    syncSimpleVoteListCardDom(s);
    if (isTouchDevice() && phoneUiMode === "swipe") {
        patchSwipeVoteUiForCurrentCard();
    }
}

/** Feedback localisé sur le seul bouton vote (liste), sans toucher aux autres cartes. */
function playListVoteButtonPop(suggestionId) {
    if (isTouchDevice() && phoneUiMode === "swipe") return;
    const btn = suggestionsContainer.querySelector(`.suggestion-vote-btn[data-id="${suggestionId}"]`);
    if (!btn) return;
    btn.classList.remove("suggestion-vote-btn--pop");
    void btn.offsetWidth;
    btn.classList.add("suggestion-vote-btn--pop");
    window.setTimeout(() => btn.classList.remove("suggestion-vote-btn--pop"), 420);
}

/**
 * Toutes les actions vote / soutien sur une suggestion passent par ici (liste, swipe, débat, feuille).
 * @param {object} p
 * @param {number} p.suggestionId
 * @param {"simple_toggle"|"legacy"} [p.mode] — simple_toggle = bascule soutien (mobile + liste) ; legacy = compat voteSuggestion(...)
 * @param {"for"|"against"} [p.voteType]
 * @param {string} [p.argument]
 * @param {object} [p.opts]
 */
async function submitSuggestionVoteAction(p) {
    const id = p.suggestionId;
    const mode = p.mode || "legacy";
    const voteType = p.voteType;
    const argument = p.argument;
    const opts = p.opts || {};

    const s = allSuggestions.find((x) => x.id === id);
    if (!s) return false;

    const prevSnapshot = {
        has_voted: s.has_voted === true,
        my_vote: s.my_vote,
        vote_count: s.vote_count,
        vote_for: s.vote_for,
        vote_against: s.vote_against,
    };

    let removeVote = false;
    if (s.needs_debate) {
        removeVote = false;
    } else if (mode === "simple_toggle") {
        removeVote = removeVoteFromDataHasVotedAttribute(id, opts.voteDomCard || null, s);
    } else {
        if (opts.removeVote === true) removeVote = true;
        else removeVote = s.has_voted === true;
    }

    if (!s.needs_debate) {
        // simple_toggle : ne pas bloquer sur s.has_voted (souvent désynchronisé du DOM / poll).
        if (mode !== "simple_toggle") {
            if (removeVote && s.has_voted !== true) return false;
            if (!removeVote && s.has_voted === true) return false;
        }
        // Cooldown uniquement sur l’ajout (anti-spam) — ne jamais bloquer le retrait (unlike).
        if (!removeVote && Date.now() < (suggestionVoteCooldownUntil[id] || 0)) return false;
    }

    const lockToken = tryAcquireVoteLock(id);
    if (lockToken === null) return false;

    const debateVoteBtns = () =>
        suggestionsContainer.querySelectorAll(`.cvl-vote-for[data-id="${id}"], .cvl-vote-against[data-id="${id}"]`);
    suggestionsContainer.querySelectorAll(`.suggestion-vote-btn[data-id="${id}"]`).forEach((b) => {
        b.disabled = true;
        b.setAttribute("aria-busy", "true");
    });
    if (s.needs_debate) {
        debateVoteBtns().forEach((b) => {
            b.disabled = true;
            b.setAttribute("aria-busy", "true");
        });
    }

    try {
        const body = {};
        if (s.needs_debate) {
            body.vote_type = voteType || "for";
            if (argument) body.argument = argument;
        } else {
            body.remove_vote = removeVote === true;
        }
        const { data, status } = await API.post(`/api/suggestions/${id}/vote`, body);
        if (status === 429 && data && typeof data.vote_count === "number") {
            s.has_voted = data.has_voted === true;
            s.my_vote = data.has_voted === true ? data.my_vote || "for" : null;
            s.vote_count = data.vote_count;
            s.vote_for = data.vote_for;
            s.vote_against = data.vote_against;
            if (data.arguments_for) s.arguments_for = data.arguments_for;
            if (data.arguments_against) s.arguments_against = data.arguments_against;
            if (data.server_ts) lastVoteServerTs[id] = Math.max(lastVoteServerTs[id] || 0, data.server_ts);
            syncVoteCacheFromServer(allSuggestions, officialProposal);
            refreshVoteDomAfterSuggestionAction(s);
            if (!s.needs_debate && !(isTouchDevice() && phoneUiMode === "swipe")) {
                playListVoteButtonPop(id);
            }
            showFeedback((data && data.error) || "Trop de requêtes. Réessaie dans quelques secondes.", "warning");
            return false;
        }
        if (status === 429) {
            showFeedback((data && data.error) || "Trop de requêtes. Réessayez plus tard.", "error");
            Object.assign(s, prevSnapshot);
            normalizeSuggestionVoteState(s);
            rollbackVoteDomForSuggestion(id);
            return false;
        }
        if (status === 200) {
            const simple = !s.needs_debate;
            s.has_voted = data.has_voted === true;
            s.my_vote = data.has_voted === true ? data.my_vote || "for" : null;
            s.vote_count = data.vote_count;
            s.vote_for = data.vote_for;
            s.vote_against = data.vote_against;
            s.arguments_for = data.arguments_for || [];
            s.arguments_against = data.arguments_against || [];
            if (data.server_ts) lastVoteServerTs[id] = Math.max(lastVoteServerTs[id] || 0, data.server_ts);
            voteOptimisticUntil[id] = Date.now() + 14000;
            if (simple) {
                if (data.has_voted === true) suggestionVoteCooldownUntil[id] = Date.now() + 720;
                else delete suggestionVoteCooldownUntil[id];
            }
            syncVoteCacheFromServer(allSuggestions, officialProposal);
            refreshVoteDomAfterSuggestionAction(s);
            if (simple && !(isTouchDevice() && phoneUiMode === "swipe")) {
                playListVoteButtonPop(id);
            }
            // FIX-2: cache UX — soutien enregistré (serveur prime déjà sur has_voted au prochain fetch)
            if (simple && data.has_voted === true && !removeVote && isTouchDevice() && phoneUiMode === "swipe") {
                SwipeHistory.markLiked(String(id));
            }
            const addedSupport = simple && data.has_voted && !removeVote;
            if (addedSupport && isTouchDevice() && phoneUiMode === "swipe") {
                requestAnimationFrame(() => {
                    const card =
                        suggestionsContainer.querySelector(`.suggestion-card[data-id="${id}"]`) ||
                        (swipeDeckInner && swipeDeckInner.querySelector(`.swipe-card[data-id="${id}"]`));
                    const bx = opts.igBurstAt?.x;
                    const by = opts.igBurstAt?.y;
                    if (bx != null && by != null) {
                        triggerIgLikeBurst(bx, by);
                    } else if (card) {
                        const r = card.getBoundingClientRect();
                        triggerIgLikeBurst(r.left + r.width / 2, r.top + r.height / 2);
                    } else {
                        triggerIgLikeBurst(window.innerWidth / 2, window.innerHeight / 2);
                    }
                });
            }
            return true;
        }
        const errMsg = data && data.error;
        if (typeof errMsg === "string" && errMsg.trim()) {
            showFeedback(errMsg, "error");
        } else if (status >= 400) {
            showFeedback("Impossible de mettre à jour le vote. Réessaie.", "error");
        }
        Object.assign(s, prevSnapshot);
        normalizeSuggestionVoteState(s);
        rollbackVoteDomForSuggestion(id);
    } catch (err) {
        console.warn("submitSuggestionVoteAction", err);
        Object.assign(s, prevSnapshot);
        normalizeSuggestionVoteState(s);
        rollbackVoteDomForSuggestion(id);
        showFeedback("Erreur de connexion au serveur.", "error");
    } finally {
        releaseVoteLock(id, lockToken);
        const s2 = allSuggestions.find((x) => x.id === id);
        suggestionsContainer.querySelectorAll(`.suggestion-vote-btn[data-id="${id}"]`).forEach((b) => {
            b.disabled = false;
            b.removeAttribute("aria-busy");
            if (s2 && !s2.needs_debate) {
                b.setAttribute("aria-pressed", s2.has_voted ? "true" : "false");
                b.dataset.hasVoted = s2.has_voted ? "true" : "false";
            }
        });
        if (s2 && !s2.needs_debate) syncSimpleVoteListCardDom(s2);
        if (s2 && isTouchDevice() && phoneUiMode === "swipe") patchSwipeVoteUiForCurrentCard();
        debateVoteBtns().forEach((b) => {
            b.disabled = false;
            b.removeAttribute("aria-busy");
        });
    }
    return false;
}

/** Compatibilité : anciens appels (débat, feuille swipe, etc.) */
async function voteSuggestion(id, voteType, argument, opts = {}) {
    return submitSuggestionVoteAction({
        suggestionId: id,
        mode: "legacy",
        voteType,
        argument,
        opts,
    });
}

/** Cœur plein écran façon Instagram (double tap / like). */
function triggerIgLikeBurst(clientX, clientY) {
    const el = document.createElement("div");
    el.className = "ig-like-burst";
    el.setAttribute("aria-hidden", "true");
    el.textContent = "♥";
    el.style.left = `${clientX}px`;
    el.style.top = `${clientY}px`;
    document.body.appendChild(el);
    requestAnimationFrame(() => el.classList.add("ig-like-burst--pop"));
    setTimeout(() => el.remove(), 900);
}

function toggleSwipeModeHints(isDebate, listEmpty) {
    const hintNon = document.querySelector(".swipe-hint-nondebate");
    const hintDeb = document.querySelector(".swipe-hint-debate");
    const vdeb = document.getElementById("swipe-vdebate-labels");
    if (listEmpty) {
        if (hintNon) hintNon.hidden = false;
        if (hintDeb) hintDeb.hidden = true;
        if (vdeb) vdeb.style.display = "none";
        return;
    }
    if (hintNon) hintNon.hidden = isDebate;
    if (hintDeb) hintDeb.hidden = !isDebate;
    if (vdeb) vdeb.style.display = isDebate ? "" : "none";
}

function closeDebateArgSheet() {
    if (!debateArgSheetEl) return;
    debateArgSheetEl.classList.remove("debate-arg-sheet--open");
    const node = debateArgSheetEl;
    setTimeout(() => {
        if (node.parentNode) node.remove();
        if (debateArgSheetEl === node) debateArgSheetEl = null;
    }, 320);
}

function openDebateVoteSheet(suggestionId, voteType) {
    closeDebateArgSheet();
    const s = allSuggestions.find((x) => x.id === suggestionId);
    const title = voteType === "for" ? "Pour" : "Contre";
    const forPrev = swipeDebateArgPreviewLines(s?.arguments_for, "Aucun argument « pour » pour l’instant.");
    const againstPrev = swipeDebateArgPreviewLines(s?.arguments_against, "Aucun argument contre pour l’instant.");
    const wrap = document.createElement("div");
    wrap.className = "debate-arg-sheet";
    wrap.innerHTML = `
        <div class="debate-arg-sheet-backdrop" data-dismiss="1"></div>
        <div class="debate-arg-sheet-panel" role="dialog" aria-modal="true" aria-labelledby="debate-arg-sheet-title">
            <div class="debate-arg-sheet-handle" aria-hidden="true"></div>
            <h3 id="debate-arg-sheet-title" class="debate-arg-sheet-title">Vote ${title}</h3>
            <p class="debate-arg-sheet-sub">Tu peux voter sans texte. Un argument est optionnel et modéré avant publication.</p>
            <div class="debate-arg-sheet-published" aria-label="Arguments déjà publiés">
                <div class="debate-arg-sheet-published-col"><span class="debate-arg-sheet-published-h">Pour</span>${forPrev}</div>
                <div class="debate-arg-sheet-published-col"><span class="debate-arg-sheet-published-h">Contre</span>${againstPrev}</div>
            </div>
            <label class="debate-arg-sheet-label" for="debate-arg-sheet-ta">Ton argument (optionnel)</label>
            <textarea id="debate-arg-sheet-ta" class="debate-arg-sheet-input" rows="3" maxlength="500" placeholder="Ajouter un argument…"></textarea>
            <div class="debate-arg-sheet-actions">
                <button type="button" class="btn btn-ghost debate-arg-cancel" data-dismiss="1">Annuler</button>
                <button type="button" class="btn btn-primary debate-arg-submit">Valider mon vote</button>
            </div>
        </div>
    `;
    document.body.appendChild(wrap);
    debateArgSheetEl = wrap;
    const ta = wrap.querySelector(".debate-arg-sheet-input");
    const submitBtn = wrap.querySelector(".debate-arg-submit");
    const onDismiss = () => {
        closeDebateArgSheet();
        renderSwipeView();
    };
    wrap.querySelectorAll("[data-dismiss]").forEach((b) => b.addEventListener("click", onDismiss));
    wrap.querySelector(".debate-arg-submit").addEventListener("click", async () => {
        if (submitBtn.disabled) return;
        const text = (ta.value || "").trim();
        submitBtn.disabled = true;
        const ok = await voteSuggestion(suggestionId, voteType, text || undefined, {});
        submitBtn.disabled = false;
        if (!ok) return;
        closeDebateArgSheet();
        swipeGoNext();
    });
    requestAnimationFrame(() => wrap.classList.add("debate-arg-sheet--open"));
    setTimeout(() => ta.focus(), 350);
}

// --------------- Téléphone — swipe (une fiche) ---------------

function syncPhoneUiChrome() {
    if (!isTouchDevice()) return;
    document.body.classList.add("student-phone-ui");
    if (phoneUiMode === "list") {
        document.body.classList.add("st-phone-list");
        if (filtersSection) filtersSection.classList.remove("filters-hidden-phone");
        if (phoneSwipeContent) phoneSwipeContent.classList.add("phone-swipe-content--hidden");
    } else {
        document.body.classList.remove("st-phone-list");
        if (filtersSection) filtersSection.classList.add("filters-hidden-phone");
        if (phoneSwipeContent) phoneSwipeContent.classList.remove("phone-swipe-content--hidden");
    }
    document.body.classList.toggle("st-phone-swipe", phoneUiMode === "swipe");
    if (btnPhoneModeSwipe && btnPhoneModeList && btnPhoneModeLiked) {
        btnPhoneModeSwipe.classList.toggle("active", phoneUiMode === "swipe");
        btnPhoneModeList.classList.toggle("active", phoneUiMode === "list" && !phoneListLikedOnly);
        btnPhoneModeLiked.classList.toggle("active", phoneUiMode === "list" && phoneListLikedOnly);
    }
}

function shuffle(arr) {
    const a = arr.slice();
    for (let i = a.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
}

async function refreshEngagementBootstrap() {
    try {
        engagementBootstrap = await API.get("/api/engagement/bootstrap");
    } catch (e) {
        engagementBootstrap = null;
    }
}

/** Cartes engagement (hors morpion « vide ») : chaque type au plus une fois par deck. */
function buildEngagementGamePool(includeTtt) {
    const done = engagementBootstrap?.cards_done_today || [];
    const pool = [];
    const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
    const ids = allSuggestions
        .filter((s) => {
            if (swipeConsumedIds.has(s.id)) return false;
            if (s.has_voted) return false;
            if (s.my_vote === "for" || s.my_vote === "against") return false;
            if (SwipeHistory.isConsumed(String(s.id))) return false;
            return true;
        })
        .map((s) => s.id);
    const guessEl = (engagementBootstrap?.guess_eligible_ids || []).filter((id) => {
        if (swipeConsumedIds.has(id)) return false;
        const sug = allSuggestions.find((x) => x.id === id);
        if (!sug) return true;
        if (sug.has_voted || SwipeHistory.isConsumed(String(id))) return false;
        return true;
    });
    const dlm = engagementBootstrap?.dilemma;

    if (!done.includes("importance") && ids.length) pool.push({ kind: "special", type: "importance", refId: pick(ids) });
    if (!done.includes("activity")) pool.push({ kind: "special", type: "activity" });
    if (!done.includes("guess") && guessEl.length) pool.push({ kind: "special", type: "guess", refId: pick(guessEl) });
    if (!done.includes("message")) pool.push({ kind: "special", type: "message" });
    if (!done.includes("peer_msg") && engagementBootstrap?.peer_message) pool.push({ kind: "special", type: "peer_msg" });
    if (!done.includes("mood")) pool.push({ kind: "special", type: "mood" });
    if (!done.includes("dilemma") && dlm && dlm.id) pool.push({ kind: "special", type: "dilemma" });
    if (includeTtt && !done.includes("ttt")) pool.push({ kind: "special", type: "ttt" });
    return pool;
}

function buildSwipeDeck() {
    if (!isTouchDevice()) return;

    // FIX-2: filtrage hybride — API (has_voted, my_vote) prime ; cache local = complément UX
    const seenIds = new Set();
    const durable = [];
    for (const s of allSuggestions) {
        if (seenIds.has(s.id)) continue;
        seenIds.add(s.id);
        const serverConsumed = s.has_voted === true;
        const localConsumed = SwipeHistory.isConsumed(String(s.id));
        if (serverConsumed || localConsumed) continue;
        durable.push(s);
    }

    const fresh = durable.filter((s) => !SwipeHistory.isSeen(String(s.id)));

    const sugItems = [];
    for (const s of fresh) {
        sugItems.push({ kind: "suggestion", id: s.id });
    }
    const shuffledSug = shuffle(sugItems);

    const gamePool = buildEngagementGamePool(true);
    const shuffledGames = shuffle(gamePool);

    let deck;
    if (shuffledSug.length > 0) {
        deck = [...shuffledSug, ...shuffledGames];
    } else if (durable.length > 0) {
        // FIX-7: tout « vu » mais encore des fiches disponibles — proposer revoir les ignorés
        deck = [{ kind: "end", type: "replay_seen" }, ...shuffledGames];
    } else if (shuffledGames.length > 0) {
        deck = [...shuffledGames];
    } else {
        deck = [{ kind: "special", type: "ttt", emptyState: true }];
    }

    swipeDeckItems = deck;
    restoreSwipeIndexAfterDeckBuild();
}

async function engagementPingSwipe() {
    try {
        await API.post("/api/engagement/ping", { type: "swipe" });
    } catch (e) {
        /* ignore */
    }
}

async function engagementPingPresence() {
    try {
        await API.post("/api/engagement/ping", { type: "presence" });
    } catch (e) {
        /* ignore */
    }
}

function clampSwipeIndex() {
    if (swipeDeckItems.length === 0) {
        swipeIndex = 0;
        return;
    }
    if (swipeIndex >= swipeDeckItems.length) swipeIndex = swipeDeckItems.length - 1;
    if (swipeIndex < 0) swipeIndex = 0;
}

const TTT_LINES = [
    [0, 1, 2],
    [3, 4, 5],
    [6, 7, 8],
    [0, 3, 6],
    [1, 4, 7],
    [2, 5, 8],
    [0, 4, 8],
    [2, 4, 6],
];

function tttWinner(cells) {
    for (const [a, b, c] of TTT_LINES) {
        if (cells[a] && cells[a] === cells[b] && cells[a] === cells[c]) return cells[a];
    }
    return cells.every(Boolean) ? "draw" : null;
}

function tttEmptyIndices(cells) {
    const r = [];
    for (let i = 0; i < 9; i++) if (!cells[i]) r.push(i);
    return r;
}

function tttMinimax(cells, maxTurn) {
    const w = tttWinner(cells);
    if (w === "O") return 1;
    if (w === "X") return -1;
    if (w === "draw") return 0;
    const moves = tttEmptyIndices(cells);
    if (maxTurn) {
        let best = -2;
        for (const m of moves) {
            const next = [...cells];
            next[m] = "O";
            best = Math.max(best, tttMinimax(next, false));
        }
        return best;
    }
    let best = 2;
    for (const m of moves) {
        const next = [...cells];
        next[m] = "X";
        best = Math.min(best, tttMinimax(next, true));
    }
    return best;
}

function tttBestMoveO(cells) {
    let bestScore = -2;
    let bestM = -1;
    for (const m of tttEmptyIndices(cells)) {
        const next = [...cells];
        next[m] = "O";
        const sc = tttMinimax(next, false);
        if (sc > bestScore) {
            bestScore = sc;
            bestM = m;
        }
    }
    return bestM;
}

function tttRedrawGrid() {
    const cells = document.querySelectorAll(".ttt-cell");
    if (!cells.length || !tttBoard) return;
    cells.forEach((el, i) => {
        el.textContent = tttBoard[i] || "";
    });
}

function tttFinish(msg) {
    tttGameOver = true;
    const st = document.getElementById("swipe-ttt-status");
    if (st) st.textContent = msg;
    const btn = document.getElementById("swipe-ttt-done");
    if (btn) btn.classList.remove("hidden");
}

function handleTttCellClick(idx) {
    const item = swipeDeckItems[swipeIndex];
    if (!item || item.kind !== "special" || item.type !== "ttt") return;
    if (tttGameOver || !tttBoard) return;
    if (tttBoard[idx]) return;
    tttBoard[idx] = "X";
    tttRedrawGrid();
    const w = tttWinner(tttBoard);
    if (w === "X") return tttFinish("Tu as gagné !");
    if (w === "draw") return tttFinish("Match nul.");
    const ai = tttBestMoveO(tttBoard);
    if (ai >= 0) tttBoard[ai] = "O";
    tttRedrawGrid();
    const w2 = tttWinner(tttBoard);
    if (w2 === "O") tttFinish("L’IA gagne.");
    else if (w2 === "draw") tttFinish("Match nul.");
}

function initTttUi() {
    const grid = document.getElementById("swipe-ttt-grid");
    if (!grid) return;
    tttBoard = Array(9).fill(null);
    tttGameOver = false;
    tttRedrawGrid();
    const st = document.getElementById("swipe-ttt-status");
    if (st) st.textContent = "";
    const btn = document.getElementById("swipe-ttt-done");
    if (btn) btn.classList.add("hidden");
    if (!grid.dataset.tttStopProp) {
        grid.dataset.tttStopProp = "1";
        ["touchstart", "touchmove", "touchend"].forEach((ev) => {
            grid.addEventListener(ev, (e) => e.stopPropagation(), { passive: true });
        });
    }
}

const MOOD_LABELS = { bien: "😄 Bien", bof: "😐 Bof", fatigue: "😴 Fatigué", stresse: "😤 Stressé" };
function createMoodResultCard(myMood) {
    const btns = Object.entries(MOOD_LABELS).map(([k, label]) => {
        const sel = k === myMood ? " mood-result-btn--selected" : "";
        return `<span class="mood-result-btn${sel}">${label}</span>`;
    }).join("");
    return `<div class="mood-result-card"><div class="mood-result-inner"><span class="mood-result-badge">Humeur</span><p class="mood-result-title">Ton humeur aujourd'hui</p><div class="mood-result-grid">${btns}</div></div></div>`;
}

function createSpecialCardHtml(item) {
    const b = engagementBootstrap || {};
    const connected = b.connected_today ?? 0;
    const pct = b.percentile_most_active ?? 50;
    const myScore = b.my_activity_score ?? 0;

    if (item.type === "importance") {
        const s = allSuggestions.find((x) => x.id === item.refId);
        if (!s) return `<div class="swipe-card swipe-card--special"><div class="swipe-card-inner"><p>—</p></div></div>`;
        return `
        <div class="swipe-card swipe-card--dating swipe-card--special" data-special="1">
            <div class="swipe-card-inner">
                <span class="swipe-eng-badge">Engagement</span>
                <p class="swipe-eng-title">Cette suggestion était-elle importante ?</p>
                <p class="swipe-eng-ref">${escapeHtml(s.title)}</p>
                <div class="swipe-eng-grid4">
                    <button type="button" class="swipe-eng-btn" data-eng="imp" data-sid="${s.id}" data-level="4">🔥 Très importante</button>
                    <button type="button" class="swipe-eng-btn" data-eng="imp" data-sid="${s.id}" data-level="3">👍 Importante</button>
                    <button type="button" class="swipe-eng-btn" data-eng="imp" data-sid="${s.id}" data-level="2">😐 Bof</button>
                    <button type="button" class="swipe-eng-btn" data-eng="imp" data-sid="${s.id}" data-level="1">👎 Pas importante</button>
                </div>
                <p class="swipe-card-hint">Glisse pour passer · ou choisis une option</p>
            </div>
        </div>`;
    }
    if (item.type === "activity") {
        return `
        <div class="swipe-card swipe-card--dating swipe-card--special" data-special="1">
            <div class="swipe-card-inner">
                <span class="swipe-eng-badge">Aujourd’hui</span>
                <p class="swipe-eng-title">Il y a <strong>${connected}</strong> personne${connected > 1 ? "s" : ""} connectée${connected > 1 ? "s" : ""} depuis ce matin.</p>
                <p class="swipe-eng-sub">Tu es plus actif que <strong>${pct}%</strong> des élèves aujourd’hui (score = 2× likes + swipes, chiffres réels).</p>
                <p class="swipe-eng-metric">Ton score d’activité : <strong>${myScore.toFixed(1)}</strong> <span class="swipe-eng-hint">(2× likes + swipes)</span></p>
                <button type="button" class="btn btn-primary swipe-eng-continue" data-eng="act-dismiss">Continuer</button>
            </div>
        </div>`;
    }
    if (item.type === "guess") {
        const s = allSuggestions.find((x) => x.id === item.refId);
        const gr = s ? swipeGuessReveal[s.id] : null;
        if (!s) return `<div class="swipe-card swipe-card--special"><div class="swipe-card-inner"><p>—</p></div></div>`;
        if (gr) {
            const ok = gr.correct;
            return `
            <div class="swipe-card swipe-card--dating swipe-card--special" data-special="1">
                <div class="swipe-card-inner">
                    <span class="swipe-eng-badge">Résultat</span>
                    <p class="swipe-eng-title">Réponse réelle : <strong>${gr.actual_pct}%</strong></p>
                    <p class="swipe-eng-ref">${escapeHtml(s.title)}</p>
                    <p class="swipe-eng-guess-feedback">${ok ? "🎯 Bravo !" : "😮 Tu t’es trompé"}</p>
                    <button type="button" class="btn btn-primary swipe-eng-continue" data-eng="guess-next">Continuer</button>
                </div>
            </div>`;
        }
        return `
        <div class="swipe-card swipe-card--dating swipe-card--special" data-special="1">
            <div class="swipe-card-inner">
                <span class="swipe-eng-badge">Devine</span>
                <p class="swipe-eng-title">Selon toi, cette idée plaît à combien d’élèves ?</p>
                <p class="swipe-eng-ref">${escapeHtml(s.title)}</p>
                <div class="swipe-eng-guess-row">
                    <button type="button" class="swipe-eng-btn" data-eng="guess" data-sid="${s.id}" data-bucket="lt30">&lt; 30%</button>
                    <button type="button" class="swipe-eng-btn" data-eng="guess" data-sid="${s.id}" data-bucket="mid">30–60%</button>
                    <button type="button" class="swipe-eng-btn" data-eng="guess" data-sid="${s.id}" data-bucket="gt60">&gt; 60%</button>
                </div>
            </div>
        </div>`;
    }
    if (item.type === "peer_msg") {
        const pm = engagementBootstrap?.peer_message;
        if (!pm || !pm.body) {
            return `<div class="swipe-card swipe-card--dating swipe-card--special"><div class="swipe-card-inner"><p class="swipe-peer-msg-empty">Aucun message d’un autre élève pour l’instant.</p></div></div>`;
        }
        const pseudo = escapeHtml(pm.display_name || "—");
        const body = escapeHtml(pm.body);
        return `
        <div class="swipe-card swipe-card--dating swipe-card--special swipe-card--peer-msg" data-special="1">
            <div class="swipe-card-inner">
                <span class="swipe-eng-badge">Surprise</span>
                <p class="swipe-eng-title swipe-peer-msg-title">Ohp, un message pour vous !</p>
                <p class="swipe-peer-msg-pseudo">— ${pseudo}</p>
                <blockquote class="swipe-peer-msg-body">${body}</blockquote>
                <p class="swipe-card-hint">Un message d’un autre élève (pas le tien), choisi pour toi aujourd’hui.</p>
                <button type="button" class="btn btn-primary swipe-eng-continue" data-eng="peer-msg-dismiss">Continuer</button>
            </div>
        </div>`;
    }
    if (item.type === "message") {
        const cardId = `swipe-msg-${Date.now().toString(36)}`;
        return `
        <div class="swipe-card swipe-card--dating swipe-card--special special-card-message" data-special="1" data-card-id="${cardId}">
            <div class="swipe-card-inner">
                <span class="swipe-eng-badge">Message</span>
                <p class="swipe-eng-title">Un message aux autres ?</p>
                <input type="text" class="swipe-eng-input" data-role="pseudo-input" maxlength="80" placeholder="Ton pseudo" autocomplete="nickname" />
                <textarea class="swipe-eng-textarea" data-role="message-input" maxlength="500" rows="4" placeholder="Ton message (modéré par l’IA, pas réécrit)…"></textarea>
                <p class="special-card-char"><span data-role="char-count">0</span>/500</p>
                <div class="special-card-actions">
                    <button type="button" class="btn btn-primary" data-action="submit-message" data-card-id="${cardId}" disabled>Envoyer</button>
                    <button type="button" class="btn btn-ghost" data-action="skip-message" data-card-id="${cardId}">Passer</button>
                </div>
            </div>
        </div>`;
    }
    if (item.type === "mood") {
        return `
        <div class="swipe-card swipe-card--dating swipe-card--special" data-special="1">
            <div class="swipe-card-inner">
                <span class="swipe-eng-badge">Humeur</span>
                <p class="swipe-eng-title">Ton humeur aujourd’hui</p>
                <p class="swipe-eng-sub">Aujourd’hui tu te sens :</p>
                <div class="swipe-eng-grid4">
                    <button type="button" class="swipe-eng-btn" data-eng="mood" data-mood="bien">😄 Bien</button>
                    <button type="button" class="swipe-eng-btn" data-eng="mood" data-mood="bof">😐 Bof</button>
                    <button type="button" class="swipe-eng-btn" data-eng="mood" data-mood="fatigue">😴 Fatigué</button>
                    <button type="button" class="swipe-eng-btn" data-eng="mood" data-mood="stresse">😤 Stressé</button>
                </div>
            </div>
        </div>`;
    }
    if (item.type === "dilemma") {
        const d = b.dilemma;
        if (!d || !d.id) {
            return `<div class="swipe-card swipe-card--special swipe-card--dilemma"><div class="swipe-card-inner"><p>—</p></div></div>`;
        }
        const pctA = d.pct_a ?? 0;
        const pctB = d.pct_b ?? 0;
        const my = d.my_side;
        if (my) {
            const labelA = escapeHtml(d.option_a);
            const labelB = escapeHtml(d.option_b);
            return `
        <div class="swipe-card swipe-card--dating swipe-card--special swipe-card--dilemma" data-special="1">
            <div class="swipe-card-inner">
                <span class="swipe-eng-badge">Dilemme du jour</span>
                <p class="swipe-eng-title">${escapeHtml(d.title)}</p>
                <div class="dilemma-results" aria-live="polite">
                    <div class="dilemma-bar-row"><span class="dilemma-opt">${labelA}</span><span class="dilemma-pct">${pctA}%</span></div>
                    <div class="dilemma-bar dilemma-bar--a" role="presentation"><span style="width:${pctA}%"></span></div>
                    <div class="dilemma-bar-row"><span class="dilemma-opt">${labelB}</span><span class="dilemma-pct">${pctB}%</span></div>
                    <div class="dilemma-bar dilemma-bar--b" role="presentation"><span style="width:${pctB}%"></span></div>
                    <p class="dilemma-votes-total">${d.votes_total ?? 0} vote(s)</p>
                </div>
                <p class="dilemma-you">Ton choix : <strong>${my === "a" ? labelA : labelB}</strong></p>
                <button type="button" class="btn btn-primary swipe-eng-continue" data-eng="dilemma-next">Continuer</button>
            </div>
        </div>`;
        }
        return `
        <div class="swipe-card swipe-card--dating swipe-card--special swipe-card--dilemma" data-special="1">
            <div class="swipe-card-inner">
                <span class="swipe-eng-badge">Dilemme du jour</span>
                <p class="swipe-eng-title">${escapeHtml(d.title)}</p>
                <div class="dilemma-vote-grid">
                    <button type="button" class="swipe-eng-btn dilemma-btn-a" data-eng="dilemma-vote" data-did="${d.id}" data-side="a">${escapeHtml(d.option_a)}</button>
                    <button type="button" class="swipe-eng-btn dilemma-btn-b" data-eng="dilemma-vote" data-did="${d.id}" data-side="b">${escapeHtml(d.option_b)}</button>
                </div>
                <button type="button" class="btn btn-ghost dilemma-skip" data-eng="dilemma-skip">Passer</button>
            </div>
        </div>`;
    }
    if (item.type === "ttt") {
        const cells = Array.from({ length: 9 }, (_, i) => `<button type="button" class="ttt-cell" data-idx="${i}" aria-label="Case ${i + 1}"></button>`).join("");
        if (item.emptyState) {
            return `
        <div class="swipe-card swipe-card--dating swipe-card--special swipe-card--ttt swipe-card--ttt-empty" data-special="1">
            <div class="swipe-card-inner">
                <p class="swipe-eng-empty-banner" role="status">Plus de nouvelles suggestions pour l’instant — mini-jeu ci-dessous.</p>
                <span class="swipe-eng-badge">Mini-jeu</span>
                <p class="swipe-eng-title">Morpion</p>
                <p class="swipe-eng-sub">Tu joues les <strong>X</strong>, l’IA les <strong>O</strong> (niveau difficile).</p>
                <div class="ttt-grid" id="swipe-ttt-grid">${cells}</div>
                <p class="ttt-status" id="swipe-ttt-status" aria-live="polite"></p>
                <div class="swipe-eng-empty-actions">
                    <button type="button" class="btn btn-primary" data-eng="ttt-replay">Rejouer</button>
                    <button type="button" class="btn btn-secondary" data-eng="swipe-empty-retry">Actualiser les suggestions</button>
                    <button type="button" class="btn btn-ghost swipe-eng-continue hidden" id="swipe-ttt-done" data-eng="ttt-dismiss">Continuer</button>
                </div>
            </div>
        </div>`;
        }
        return `
        <div class="swipe-card swipe-card--dating swipe-card--special swipe-card--ttt" data-special="1">
            <div class="swipe-card-inner">
                <span class="swipe-eng-badge">Mini-jeu</span>
                <p class="swipe-eng-title">Morpion</p>
                <p class="swipe-eng-sub">Tu joues les <strong>X</strong>, l’IA les <strong>O</strong> (niveau difficile).</p>
                <div class="ttt-grid" id="swipe-ttt-grid">${cells}</div>
                <p class="ttt-status" id="swipe-ttt-status" aria-live="polite"></p>
                <div class="swipe-eng-ttt-actions">
                    <button type="button" class="btn btn-secondary" data-eng="ttt-replay">Rejouer</button>
                    <button type="button" class="btn btn-primary swipe-eng-continue hidden" id="swipe-ttt-done" data-eng="ttt-dismiss">Continuer</button>
                </div>
            </div>
        </div>`;
    }
    return `<div class="swipe-card swipe-card--special"><div class="swipe-card-inner"></div></div>`;
}

function swipeDebateArgPreviewLines(args, emptyLabel) {
    const list = (args || []).slice(0, 10);
    if (!list.length) return `<p class="swipe-debate-args-empty">${emptyLabel}</p>`;
    return `<ul class="swipe-debate-args-ul">${list
        .map((a) => {
            const t = (a.summary || a.original_text || "").trim();
            return t ? `<li>${escapeHtml(t.length > 200 ? `${t.slice(0, 200)}…` : t)}</li>` : "";
        })
        .filter(Boolean)
        .join("")}</ul>`;
}

function createSwipeCardHtml(s) {
    const icon = CATEGORY_ICONS[s.category] || "📌";
    const liked = s.has_voted === true;
    const debate = !!s.needs_debate;
    const vf = s.vote_for ?? 0;
    const va = s.vote_against ?? 0;
    const hotClass = s.hot ? " swipe-card--hot" : "";
    if (debate) {
        const forLines = swipeDebateArgPreviewLines(s.arguments_for, "Pas encore d’argument pour.");
        const againstLines = swipeDebateArgPreviewLines(s.arguments_against, "Pas encore d’argument contre.");
        return `
        <div class="swipe-card swipe-card--dating swipe-card--debate${hotClass}" data-id="${s.id}" data-debate="1" data-has-voted="${s.has_voted ? "true" : "false"}">
            <div class="swipe-card-inner">
                <span class="swipe-debate-pill">Débat</span>
                <div class="suggestion-title-block">
                    <span class="swipe-card-emoji" aria-hidden="true">${icon}</span>
                    <span class="suggestion-title swipe-card-title">${escapeHtml(s.title)}</span>
                    ${s.subtitle ? `<p class="suggestion-subtitle">${escapeHtml(s.subtitle)}</p>` : ""}
                </div>
                <div class="swipe-card-meta">
                    <span class="badge badge-category">${escapeHtml(s.category)}</span>
                    <span class="badge badge-status" data-status="${escapeHtml(s.status)}">${escapeHtml(s.status)}</span>
                </div>
                <div class="swipe-debate-scores">
                    <span class="swipe-debate-score swipe-debate-score--for">Pour ${vf}</span>
                    <span class="swipe-debate-score swipe-debate-score--against">Contre ${va}</span>
                </div>
                <div class="swipe-debate-args-preview" aria-label="Arguments publiés">
                    <div class="swipe-debate-args-block">
                        <span class="swipe-debate-args-label">Pour</span>
                        ${forLines}
                    </div>
                    <div class="swipe-debate-args-block">
                        <span class="swipe-debate-args-label">Contre</span>
                        ${againstLines}
                    </div>
                </div>
                <p class="swipe-card-hint swipe-card-hint-debate-inner">↑ Glisse vers le haut : POUR · ↓ vers le bas : CONTRE</p>
            </div>
        </div>`;
    }
    const nfcInfo = s.source === "nfc" ? `
        <div class="swipe-card-nfc-info">
            <span class="badge badge-nfc-terrain">📡 NFC</span>
            ${s.nfc_location_name ? `<span class="swipe-card-nfc-loc">📍 ${escapeHtml(s.nfc_location_name)}${s.nfc_building ? " · " + escapeHtml(s.nfc_building) : ""}${s.nfc_floor ? " · " + escapeHtml(s.nfc_floor) : ""}</span>` : ""}
            ${s.nfc_location_slug ? `<a href="/nfc/${escapeHtml(s.nfc_location_slug)}" class="swipe-card-nfc-link" target="_blank">Voir sur le terrain ↗</a>` : ""}
        </div>` : "";
    return `
        <div class="swipe-card swipe-card--dating${hotClass}${liked ? " swipe-card--liked" : ""}" data-id="${s.id}" data-has-voted="${liked ? "true" : "false"}">
            <div class="swipe-card-inner">
                <div class="suggestion-title-block">
                    <span class="swipe-card-emoji" aria-hidden="true">${icon}</span>
                    <span class="suggestion-title swipe-card-title">${escapeHtml(s.title)}</span>
                    ${s.subtitle ? `<p class="suggestion-subtitle">${escapeHtml(s.subtitle)}</p>` : ""}
                </div>
                ${nfcInfo}
                <div class="swipe-card-meta">
                    ${s.status === "En attente" ? '<span class="badge badge-processing">⏳ En cours de traitement</span>' : `<span class="badge badge-category">${escapeHtml(s.category)}</span><span class="badge badge-status" data-status="${escapeHtml(s.status)}">${escapeHtml(s.status)}</span>`}
                </div>
                <div class="swipe-card-footer">
                    <span class="swipe-card-votes-big${liked ? " swipe-card-votes-big--liked" : ""}">♥ ${s.vote_count}</span>
                    <span class="swipe-card-hint">${liked ? "Double tap · retirer le soutien" : "Double tap · soutenir"}</span>
                </div>
                <div class="suggestion-heart-burst" aria-hidden="true"></div>
            </div>
        </div>
    `;
}

function wireSwipeCvlFromHTML(container) {
    if (!container) return;
    container.querySelectorAll(".cvl-vote-for, .cvl-vote-against").forEach((btn) => {
        btn.addEventListener("click", () => {
            if (btn.closest(".suggestion-card-cvl")) onProposalVoteClick(btn);
        });
    });
    container.querySelectorAll(".cvl-argument-submit").forEach((btn) => {
        btn.addEventListener("click", () => submitProposalVoteWithArgument(btn));
    });
    container.querySelectorAll(".cvl-add-arg-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            const panel = btn.closest(".cvl-add-arg-panel");
            const form = panel?.querySelector(".cvl-add-arg-form");
            if (form) {
                form.classList.toggle("hidden");
                if (!form.classList.contains("hidden")) form.querySelector("textarea")?.focus();
            }
        });
    });
    container.querySelectorAll(".cvl-official-add-arg-submit").forEach((btn) => {
        btn.addEventListener("click", () => submitOfficialAddArgument(btn));
    });
    container.querySelectorAll(".cvl-arguments-toggle").forEach((btn) => {
        btn.addEventListener("click", () => {
            const proposalId = btn.dataset.proposalId;
            const block = proposalId ? container.querySelector(`.cvl-arguments[data-proposal-id="${proposalId}"]`) : null;
            if (!block) return;
            const isExpanded = block.classList.toggle("cvl-arguments-expanded");
            block.classList.toggle("cvl-arguments-collapsed", !isExpanded);
            btn.classList.toggle("expanded", isExpanded);
            const chev = btn.querySelector(".cvl-arguments-chevron");
            if (chev) chev.textContent = isExpanded ? "▼" : "▶";
            btn.setAttribute("aria-expanded", isExpanded);
        });
    });
}

function htmlForSwipeDeckItem(item) {
    if (!item) return "";
    if (item.kind === "end") return createEndCardHtml(item);
    if (item.kind === "special") return createSpecialCardHtml(item);
    if (item.kind === "suggestion") {
        const s = allSuggestions.find((x) => x.id === item.id);
        return s ? createSwipeCardHtml(s) : "";
    }
    return "";
}

// FIX-7: carte de fin / relecture des ignorés
function createEndCardHtml(item) {
    const hasIgnored = (JSON.parse(localStorage.getItem("swipe_seen_ids") || "[]")).length > 0;
    const replay = item.type === "replay_seen";
    const sessionDone = item.type === "session_done";
    const title = sessionDone
        ? "Tu as tout parcouru"
        : replay
          ? "Tout est marqué « vu »"
          : "Tu as tout exploré !";
    const text = sessionDone
        ? "Accède à tes likes (soutenus), à la liste complète, ou revois les fiches que tu n’as pas encore soutenues."
        : replay
          ? "Tu peux revoir les fiches ignorées ou passer en liste / favoris."
          : "Tu as vu les suggestions disponibles pour le moment.";
    return `
    <div class="swipe-card swipe-card--dating swipe-card--end-card" data-special="1" data-card-id="end">
        <div class="swipe-card-inner">
            <div class="end-card-content">
                <div class="end-card-icon" aria-hidden="true">✅</div>
                <h3 class="end-card-title">${title}</h3>
                <p class="end-card-text">${text}</p>
                <div class="end-card-actions">
                    <button type="button" class="btn btn-primary" data-action="go-favorites" data-card-id="end">⭐ Accéder à mes likes</button>
                    <button type="button" class="btn btn-secondary" data-action="go-list" data-card-id="end">📋 Accéder à la liste</button>
                    ${hasIgnored ? `<button type="button" class="btn btn-ghost" data-action="replay-seen" data-card-id="end">🔄 Revoir ceux que je n’ai pas likés</button>` : ""}
                </div>
            </div>
        </div>
    </div>`;
}

/** Direction de la dernière transition : "next" | "prev" | null */
let _swipeTransitionDir = null;
let _swipeAnimating = false;

/** FIX-3: pile 3 slots — slot0 interactif, 1–2 en arrière-plan (CSS pointer-events: none) */
function injectSwipeStackSlots(html0, html1, html2) {
    if (typeof swipeGestureReset === "function") swipeGestureReset();
    const empty = `<div class="swipe-card-slot-empty" aria-hidden="true"></div>`;
    const dir = _swipeTransitionDir;
    _swipeTransitionDir = null;
    const enterCls = dir === "next" ? "swipe-enter-from-right"
        : dir === "prev" ? "swipe-enter-from-left"
        : "swipe-layer-entering";
    const w0 = html0
        ? `<div id="swipe-active-layer" class="swipe-card-layer ${enterCls}">${html0}</div>`
        : empty;
    const w1 = html1 ? `<div class="swipe-card-layer swipe-card-layer--back">${html1}</div>` : empty;
    const w2 = html2 ? `<div class="swipe-card-layer swipe-card-layer--back">${html2}</div>` : empty;
    swipeDeckInner.innerHTML = `
    <div class="swipe-slots-root">
      <div class="swipe-card-slot swipe-card-slot-0" data-slot="0">${w0}</div>
      <div class="swipe-card-slot swipe-card-slot-1 swipe-card-slot--inactive" data-slot="1">${w1}</div>
      <div class="swipe-card-slot swipe-card-slot-2 swipe-card-slot--inactive" data-slot="2">${w2}</div>
    </div>`;
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            const el = document.getElementById("swipe-active-layer");
            if (!el) return;
            if (dir === "next") el.classList.replace("swipe-enter-from-right", "swipe-enter-active");
            else if (dir === "prev") el.classList.replace("swipe-enter-from-left", "swipe-enter-active");
            else el.classList.add("swipe-layer-enter-active");
        });
    });
    persistSwipeDeckAnchor();
}

function _animateSwipeExit(dir, cb) {
    const layer = swipeDeckInner?.querySelector("#swipe-active-layer");
    if (!layer) { cb(); return; }
    _swipeAnimating = true;
    const cls = dir === "next" ? "swipe-exit-to-left" : "swipe-exit-to-right";
    layer.classList.add(cls);
    const done = () => { _swipeAnimating = false; cb(); };
    layer.addEventListener("animationend", done, { once: true });
    setTimeout(done, 360);
}

function renderSwipeView() {
    if (!isTouchDevice()) return;
    syncPhoneUiChrome();
    if (!swipeCvlSlot || !swipeDeckInner) return;

    if (officialProposal) {
        swipeCvlSlot.innerHTML = createOfficialProposalCard(officialProposal);
        swipeCvlSlot.classList.remove("hidden");
        wireSwipeCvlFromHTML(swipeCvlSlot);
    } else {
        swipeCvlSlot.innerHTML = "";
        swipeCvlSlot.classList.add("hidden");
    }

    clampSwipeIndex();
    const list = swipeDeckItems;
    if (swipeCounter) swipeCounter.textContent = list.length ? String(swipeIndex + 1) : "0";
    if (swipeCounterTotal) swipeCounterTotal.textContent = list.length ? String(list.length) : "0";
    document.querySelectorAll(".swipe-nope, .swipe-yep, .swipe-label-up, .swipe-label-down").forEach((el) => {
        el.style.opacity = "0";
    });
    if (!list.length) {
        if (typeof swipeGestureReset === "function") swipeGestureReset();
        swipeDeckInner.innerHTML = `<div class="swipe-deck-empty"><p>Aucune suggestion pour le moment.</p></div>`;
        toggleSwipeModeHints(false, true);
        return;
    }
    const item = list[swipeIndex];
    const next = list[swipeIndex + 1];
    const next2 = list[swipeIndex + 2];

    if (item.kind === "special") {
        document.querySelectorAll(".swipe-hint-nondebate, .swipe-hint-debate").forEach((el) => {
            el.hidden = true;
        });
        const vdeb = document.getElementById("swipe-vdebate-labels");
        if (vdeb) vdeb.style.display = "none";
        injectSwipeStackSlots(htmlForSwipeDeckItem(item), htmlForSwipeDeckItem(next), htmlForSwipeDeckItem(next2));
        if (item.type === "ttt") {
            requestAnimationFrame(() => initTttUi());
        }
        return;
    }
    if (item.kind === "end") {
        document.querySelectorAll(".swipe-hint-nondebate, .swipe-hint-debate").forEach((el) => {
            el.hidden = true;
        });
        const vdeb = document.getElementById("swipe-vdebate-labels");
        if (vdeb) vdeb.style.display = "none";
        injectSwipeStackSlots(htmlForSwipeDeckItem(item), htmlForSwipeDeckItem(next), htmlForSwipeDeckItem(next2));
        return;
    }
    const s = allSuggestions.find((x) => x.id === item.id);
    if (!s) {
        if (typeof swipeGestureReset === "function") swipeGestureReset();
        swipeDeckInner.innerHTML = `<div class="swipe-deck-empty"><p>—</p></div>`;
        return;
    }
    toggleSwipeModeHints(!!s.needs_debate, false);
    injectSwipeStackSlots(htmlForSwipeDeckItem(item), htmlForSwipeDeckItem(next), htmlForSwipeDeckItem(next2));
}

function _swipeGoNextImmediate() {
    const list = swipeDeckItems;
    if (list.length === 0) return;

    if (swipeIndex >= list.length - 1) {
        const cur = list[swipeIndex];
        if (cur && cur.kind === 'end') return;
        if (cur && cur.kind === 'suggestion') {
            swipeConsumedIds.add(cur.id);
            saveSwipeConsumedIds();
            SwipeHistory.markSeen(String(cur.id));
        }
        if (!list.some(it => it.kind === 'end')) {
            swipeDeckItems.push({ kind: 'end', type: 'session_done' });
        }
        swipeIndex = swipeDeckItems.length - 1;
        _swipeTransitionDir = 'next';
        renderSwipeView();
        return;
    }

    const cur = list[swipeIndex];
    if (cur && cur.kind === 'suggestion') {
        swipeConsumedIds.add(cur.id);
        saveSwipeConsumedIds();
        SwipeHistory.markSeen(String(cur.id));
    }
    swipeIndex++;
    engagementPingSwipe();
    _swipeTransitionDir = 'next';
    renderSwipeView();
}

function swipeGoNext() {
    if (_swipeAnimating) return;
    const layer = swipeDeckInner ? swipeDeckInner.querySelector('#swipe-active-layer') : null;
    const m = layer && layer.style.transform ? layer.style.transform.match(/translateX\(([^)]+)\)/) : null;
    const tx = m ? parseFloat(m[1]) : 0;
    if (Math.abs(tx) > 60) { _swipeGoNextImmediate(); return; }
    _animateSwipeExit('next', _swipeGoNextImmediate);
}

function swipeGoPrev() {
    if (_swipeAnimating) return;
    const list = swipeDeckItems;
    if (list.length === 0 || swipeIndex <= 0) return;
    const layer = swipeDeckInner ? swipeDeckInner.querySelector('#swipe-active-layer') : null;
    const m = layer && layer.style.transform ? layer.style.transform.match(/translateX\(([^)]+)\)/) : null;
    const tx = m ? parseFloat(m[1]) : 0;
    const doRender = () => {
        swipeIndex--;
        engagementPingSwipe();
        _swipeTransitionDir = 'prev';
        renderSwipeView();
    };
    if (Math.abs(tx) > 60) { doRender(); return; }
    _animateSwipeExit('prev', doRender);
}

function attachSwipeDeckGestures() {
    if (!swipeDeckInner || swipeDeckInner.dataset.swipeBound === "1") return;
    swipeDeckInner.dataset.swipeBound = "1";

    swipeDeckInner.addEventListener(
        "touchstart",
        () => {
            swipeUserInteracting = true;
        },
        { passive: true, capture: true },
    );
    swipeDeckInner.addEventListener(
        "touchend",
        () => {
            clearTimeout(swipeInteractClearTimer);
            swipeInteractClearTimer = setTimeout(() => {
                swipeUserInteracting = false;
                if (pendingSwipeDeckRebuild) {
                    loadSuggestions();
                }
            }, 420);
        },
        { passive: true, capture: true },
    );

    let startX = 0;
    let startY = 0;
    let tracking = false;
    /** Verrou d’axe jusqu’au touchend : h | v_up | v_down */
    let gestureLocked = null;
    let gestureInvalid = false;
    /** Échantillons pour vélocité (derniers ~90 ms) */
    let moveSamples = [];

    function getLayer() {
        return swipeDeckInner.querySelector("#swipe-active-layer");
    }

    function labelEls() {
        const wrap = document.getElementById("swipe-deck-wrap");
        return {
            nope: wrap?.querySelector(".swipe-nope"),
            yep: wrap?.querySelector(".swipe-yep"),
            up: wrap?.querySelector(".swipe-label-up"),
            down: wrap?.querySelector(".swipe-label-down"),
        };
    }

    function resetLabels() {
        const { nope, yep, up, down } = labelEls();
        if (nope) nope.style.opacity = "0";
        if (yep) yep.style.opacity = "0";
        if (up) {
            up.style.opacity = "0";
            up.classList.remove("swipe-dir-threshold");
        }
        if (down) {
            down.style.opacity = "0";
            down.classList.remove("swipe-dir-threshold");
        }
    }

    // FIX-FINAL-2: remise à zéro sûre (closure + refs DOM courantes avant destruction de #swipe-active-layer)
    function resetGestureState() {
        tracking = false;
        gestureLocked = null;
        gestureInvalid = false;
        moveSamples = [];
        swipeDragging = false;
        swipeLastTap = 0;
        resetLabels();
        const layer = getLayer();
        if (layer) {
            layer.style.transition = "";
            springLayer(layer);
        }
    }

    function springLayer(layer) {
        if (!layer) return;
        layer.style.transition = "transform 0.35s cubic-bezier(0.34, 1.4, 0.64, 1)";
        layer.style.transform = "translate(0, 0) rotate(0deg)";
    }

    /** Zones angulaires + bande morte diagonale ; null = ambigu / trop court */
    function classifySwipeIntent(mx, my) {
        const dist = Math.hypot(mx, my);
        if (dist < GESTURE_CONFIG.CLASSIFY_MIN_DIST) return null;
        const ax = Math.abs(mx);
        const ay = Math.abs(my);
        const ratio = ay / (ax + 0.001);
        if (ratio > 0.52 && ratio < 1.9) return null;
        if (ax > ay * 1.32) return { kind: "h" };
        if (ay > ax * 1.38) {
            if (my < 0) return { kind: "v_up" };
            if (my > 0) return { kind: "v_down" };
        }
        return null;
    }

    function endVelocitySamples() {
        const now = performance.now();
        const recent = moveSamples.filter((s) => now - s.t <= 90);
        if (recent.length < 2) return { vx: 0, vy: 0 };
        const a = recent[0];
        const b = recent[recent.length - 1];
        const dt = (b.t - a.t) / 1000;
        if (dt < 0.008) return { vx: 0, vy: 0 };
        return { vx: (b.x - a.x) / dt, vy: (b.y - a.y) / dt };
    }

    swipeDeckInner.addEventListener(
        "touchstart",
        (e) => {
            if (e.touches.length !== 1) {
                gestureInvalid = true;
                tracking = false;
                return;
            }
            const curIt = swipeDeckItems[swipeIndex];
            const layer = getLayer();
            if (!layer) return;
            const t = e.touches[0];
            startX = t.clientX;
            startY = t.clientY;
            gestureLocked = null;
            gestureInvalid = false;
            moveSamples = [{ t: performance.now(), x: t.clientX, y: t.clientY }];
            tracking = true;
            swipeDragging = false;
            layer.style.transition = "none";
            resetLabels();
        },
        { passive: true },
    );

    swipeDeckInner.addEventListener(
        "touchmove",
        (e) => {
            if (!tracking) return;
            if (e.touches.length !== 1) {
                gestureInvalid = true;
                return;
            }
            const layer = getLayer();
            if (!layer) return;
            const t = e.touches[0];
            const mx = t.clientX - startX;
            const my = t.clientY - startY;
            const now = performance.now();
            moveSamples.push({ t: now, x: t.clientX, y: t.clientY });
            if (moveSamples.length > 10) moveSamples.shift();

            const g = classifySwipeIntent(mx, my);
            if (!gestureLocked && g) gestureLocked = g;

            if (gestureLocked && (Math.abs(mx) > 12 || Math.abs(my) > 12)) swipeDragging = true;

            const item = swipeDeckItems[swipeIndex];
            const cur = item && item.kind === "suggestion" ? allSuggestions.find((x) => x.id === item.id) : null;
            const isDeb = cur && cur.needs_debate;
            const inner = layer.querySelector(".swipe-card-inner");
            const scrollBlockedUp = !!(inner && inner.scrollTop > 2);
            const { nope, yep, up, down } = labelEls();

            const k = gestureLocked?.kind;
            const _isEndCard = item && item.kind === "end";
            if (k === "h") {
                let effMx = mx;
                if (_isEndCard && mx < 0) effMx = mx * 0.18;
                const rot = effMx * GESTURE_CONFIG.ROT_X_FACTOR;
                layer.style.transform = `translateX(${effMx}px) translateZ(0) rotate(${rot}deg)`;
                const p = Math.min(1, Math.abs(mx) / 100);
                if (nope && yep) {
                    if (mx < 0) {
                        nope.style.opacity = String(p);
                        yep.style.opacity = "0";
                    } else if (mx > 0) {
                        yep.style.opacity = String(p);
                        nope.style.opacity = "0";
                    }
                }
                if (up) up.style.opacity = "0";
                if (down) down.style.opacity = "0";
            } else if (k === "v_up" || k === "v_down") {
                if (isDeb) {
                    layer.style.transform = `translateY(${my}px) translateZ(0) rotate(${my * 0.018}deg)`;
                    let pu = 0;
                    let pd = 0;
                    if (k === "v_up" && !scrollBlockedUp) {
                        pu = my < 0 ? Math.min(1, Math.abs(my) / 108) : 0;
                    }
                    if (k === "v_down") {
                        pd = my > 0 ? Math.min(1, Math.abs(my) / 96) : 0;
                    }
                    if (up) {
                        up.style.opacity = String(pu);
                        up.classList.toggle("swipe-dir-threshold", pu > 0.82);
                    }
                    if (down) {
                        down.style.opacity = String(pd);
                        down.classList.toggle("swipe-dir-threshold", pd > 0.82);
                    }
                    if (nope) nope.style.opacity = "0";
                    if (yep) yep.style.opacity = "0";
                } else {
                    layer.style.transform = `translateY(${my * 0.26}px) translateZ(0)`;
                    if (nope) nope.style.opacity = "0";
                    if (yep) yep.style.opacity = "0";
                }
            }
        },
        { passive: true },
    );

    swipeDeckInner.addEventListener(
        "touchend",
        (e) => {
            if (!tracking) return;
            tracking = false;
            swipeDragging = false;
            const layer = getLayer();
            const t = e.changedTouches[0];
            const mx = t.clientX - startX;
            const my = t.clientY - startY;
            const adx = Math.abs(mx);
            const ady = Math.abs(my);
            const vel = endVelocitySamples();
            const item = swipeDeckItems[swipeIndex];
            const s = item && item.kind === "suggestion" ? allSuggestions.find((x) => x.id === item.id) : null;
            const inner = layer?.querySelector(".swipe-card-inner");
            const scrollBlockedUp = !!(inner && inner.scrollTop > 2);

            if (gestureInvalid) {
                springLayer(layer);
                resetLabels();
                swipeLastTap = 0;
                return;
            }

            const commitHorizontal = () => {
                const goNext = mx < 0;
                const isEnd = item && item.kind === "end";
                if (goNext && isEnd) {
                    springLayer(layer);
                    resetLabels();
                    swipeLastTap = 0;
                    return;
                }
                if (!goNext && swipeIndex <= 0) {
                    springLayer(layer);
                    resetLabels();
                    swipeLastTap = 0;
                    return;
                }
                const curItem = swipeDeckItems[swipeIndex];
                const w = window.innerWidth;
                const exitX = mx < 0 ? -w * 1.1 : w * 1.1;
                if (layer) {
                    layer.style.transition = "transform 0.32s cubic-bezier(0.4, 0, 0.2, 1)";
                    layer.style.transform = `translateX(${exitX}px) rotate(${mx * GESTURE_CONFIG.EXIT_ROT_FACTOR}deg)`;
                }
                resetLabels();
                setTimeout(() => {
                    void (async () => {
                        if (goNext && curItem && curItem.kind === "special" && curItem.type === "peer_msg") {
                            try {
                                const { status } = await API.post("/api/engagement/peer-msg-dismiss", {});
                                if (status === 200) await refreshEngagementBootstrap();
                            } catch (e) {
                                /* ignore */
                            }
                        }
                        if (goNext) swipeGoNext();
                        else swipeGoPrev();
                    })();
                }, 340);
                swipeLastTap = 0;
            };

            const k = gestureLocked?.kind;
            const TH_UP = GESTURE_CONFIG.TH_UP;
            const TH_DN = GESTURE_CONFIG.TH_DN;

            if (k === "h" && adx > GESTURE_CONFIG.COMMIT_H_ADX_MIN && ady < GESTURE_CONFIG.COMMIT_H_ADY_MAX && adx > ady * GESTURE_CONFIG.COMMIT_RATIO_H) {
                commitHorizontal();
                return;
            }

            if (k === "v_up" && s && s.needs_debate && !scrollBlockedUp) {
                const velOk = vel.vy < GESTURE_CONFIG.VEL_VY_UP;
                const distOk = my < -TH_UP;
                const notTooHorizontal = adx < 96;
                if (distOk && notTooHorizontal && (velOk || my < GESTURE_CONFIG.MY_DIST_UP)) {
                    const sid = s.id;
                    if (layer) {
                        layer.style.transition = "transform 0.3s cubic-bezier(0.4, 0, 0.2, 1)";
                        layer.style.transform = `translateY(${-window.innerHeight * 1.15}px) rotate(-6deg)`;
                    }
                    resetLabels();
                    setTimeout(() => {
                        if (swipeDeckInner) swipeDeckInner.innerHTML = "";
                        if (typeof swipeGestureReset === "function") swipeGestureReset();
                        openDebateVoteSheet(sid, "for");
                    }, 340);
                    swipeLastTap = 0;
                    return;
                }
                springLayer(layer);
                resetLabels();
                swipeLastTap = 0;
                return;
            }

            if (k === "v_down" && s && s.needs_debate) {
                if (my > TH_DN && adx < 100) {
                    const sid = s.id;
                    if (layer) {
                        layer.style.transition = "transform 0.3s cubic-bezier(0.4, 0, 0.2, 1)";
                        layer.style.transform = `translateY(${window.innerHeight * 1.15}px) rotate(6deg)`;
                    }
                    resetLabels();
                    setTimeout(() => {
                        if (swipeDeckInner) swipeDeckInner.innerHTML = "";
                        if (typeof swipeGestureReset === "function") swipeGestureReset();
                        openDebateVoteSheet(sid, "against");
                    }, 340);
                    swipeLastTap = 0;
                    return;
                }
                springLayer(layer);
                resetLabels();
                swipeLastTap = 0;
                return;
            }

            if ((k === "v_up" || k === "v_down") && s && !s.needs_debate) {
                springLayer(layer);
                resetLabels();
                swipeLastTap = 0;
                return;
            }

            if (k === "h") {
                springLayer(layer);
                resetLabels();
                swipeLastTap = 0;
                return;
            }

            springLayer(layer);
            resetLabels();

            if (gestureLocked || adx > 18 || ady > 18) {
                swipeLastTap = 0;
                return;
            }

            const card = e.target.closest(".swipe-card");
            if (!card || e.target.closest("button")) return;
            const curItem = swipeDeckItems[swipeIndex];
            if (!curItem || curItem.kind !== "suggestion") return;
            const now = Date.now();
            if (now - swipeLastTap < GESTURE_CONFIG.DOUBLE_TAP_MS) {
                swipeLastTap = 0;
                const id = parseInt(card.dataset.id, 10);
                const sug = allSuggestions.find((x) => x.id === id);
                if (!sug || sug.needs_debate) return;
                if (Date.now() < (swipeDoubleTapVoteCooldownUntil[id] || 0)) return;
                swipeDoubleTapVoteCooldownUntil[id] = Date.now() + 520;
                const cx = t.clientX;
                const cy = t.clientY;
                void submitSuggestionVoteAction({
                    suggestionId: id,
                    mode: "simple_toggle",
                    opts: { igBurstAt: { x: cx, y: cy }, voteDomCard: card },
                }).then((ok) => {
                    if (ok) swipeDoubleTapVoteCooldownUntil[id] = Date.now() + 520;
                });
            } else {
                swipeLastTap = now;
            }
        },
        { passive: true },
    );

    swipeGestureReset = resetGestureState;
}

const PENDING_COMMUNITY_MSG_KEY = "pending_community_messages_v1";

function pushPendingCommunityMessage(obj) {
    try {
        const raw = localStorage.getItem(PENDING_COMMUNITY_MSG_KEY);
        const arr = raw ? JSON.parse(raw) : [];
        // FIX-FINAL-7: un seul pending par client_message_id (réessai manuel / retry)
        const filtered = arr.filter((x) => x.client_message_id !== obj.client_message_id);
        filtered.push(obj);
        localStorage.setItem(PENDING_COMMUNITY_MSG_KEY, JSON.stringify(filtered));
    } catch (e) {
        /* ignore */
    }
}

function removePendingCommunityMessageByClientId(clientId) {
    if (!clientId) return;
    try {
        const raw = localStorage.getItem(PENDING_COMMUNITY_MSG_KEY);
        if (!raw) return;
        const arr = JSON.parse(raw).filter((x) => x.client_message_id !== clientId);
        localStorage.setItem(PENDING_COMMUNITY_MSG_KEY, JSON.stringify(arr));
    } catch (e) {
        /* ignore */
    }
}

// FIX-6b: retry messages en attente (même endpoint qu’avant : /api/engagement/message)
async function retryPendingCommunityMessages() {
    if (!navigator.onLine) return;
    let arr;
    try {
        const raw = localStorage.getItem(PENDING_COMMUNITY_MSG_KEY);
        if (!raw) return;
        arr = JSON.parse(raw);
    } catch {
        return;
    }
    if (!Array.isArray(arr) || !arr.length) return;
    const remaining = [];
    for (const msg of arr) {
        try {
            const { data, status } = await API.post("/api/engagement/message", {
                display_name: msg.display_name,
                message: msg.message,
                client_message_id: msg.client_message_id,
            });
            if (status === 200 && data && (data.ok === true || data.deduped === true)) continue;
            throw new Error("fail");
        } catch {
            const rc = (msg._retryCount || 0) + 1;
            if (rc < 3) remaining.push({ ...msg, _retryCount: rc });
        }
    }
    try {
        localStorage.setItem(PENDING_COMMUNITY_MSG_KEY, JSON.stringify(remaining));
    } catch (e) {
        /* ignore */
    }
}

async function submitMessageToOthers(cardId, displayName, messageText) {
    const trimmed = (messageText || "").trim();
    const pn = (displayName || "").trim();
    if (trimmed.length < 3 || pn.length < 1) {
        showFeedback("Pseudo et message requis (3 caractères min. pour le message).", "error");
        return;
    }
    const cardEl = document.querySelector(`.special-card-message[data-card-id="${cardId}"]`);
    // FIX-FINAL-7: même id pour réessai « Réessayer » — évite doublon serveur si la 1ʳᵉ requête a réussi
    let client_message_id = cardEl?.dataset?.stableClientMsgId || "";
    if (!client_message_id) {
        client_message_id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
        if (cardEl) cardEl.dataset.stableClientMsgId = client_message_id;
    }
    const payload = { display_name: pn, message: trimmed, client_message_id };
    const btn = document.querySelector(`[data-action="submit-message"][data-card-id="${cardId}"]`);
    if (btn) {
        btn.disabled = true;
        btn.textContent = "Envoi…";
    }
    try {
        const { data, status } = await API.post("/api/engagement/message", payload);
        if (status >= 400) throw new Error((data && data.error) || "Erreur");
        removePendingCommunityMessageByClientId(client_message_id);
        await refreshEngagementBootstrap();
        showFeedback("Message envoyé.", "success");
        swipeGoNext();
    } catch (err) {
        console.warn("[FIX-6] submitMessageToOthers", err);
        pushPendingCommunityMessage({ ...payload, _retryCount: 0 });
        if (btn) {
            btn.disabled = false;
            btn.textContent = "Réessayer";
        }
        showFeedback("Erreur réseau — message mis en attente.", "warning");
    }
}

// FIX-5: actions data-action sur le deck (carte fin, message…)
function handleDeckDataAction(e) {
    const btn = e.target.closest("[data-action]");
    if (!btn) return false;
    const action = btn.dataset.action;
    if (action === "submit-message") {
        e.preventDefault();
        const cid = btn.dataset.cardId;
        const card = document.querySelector(`.special-card-message[data-card-id="${cid}"]`);
        const pseudo = card?.querySelector("[data-role='pseudo-input']")?.value || "";
        const msg = card?.querySelector("[data-role='message-input']")?.value || "";
        void submitMessageToOthers(cid, pseudo, msg);
        return true;
    }
    if (action === "skip-message") {
        e.preventDefault();
        swipeGoNext();
        return true;
    }
    if (action === "go-favorites") {
        e.preventDefault();
        btnPhoneModeLiked?.click();
        return true;
    }
    if (action === "go-list") {
        e.preventDefault();
        btnPhoneModeList?.click();
        return true;
    }
    if (action === "replay-seen") {
        e.preventDefault();
        SwipeHistory.resetSeen();
        lastSwipeDeckSig = "";
        buildSwipeDeck();
        clampSwipeIndex();
        swipeIndex = 0;
        renderSwipeView();
        return true;
    }
    return false;
}

async function handleEngagementClick(ev) {
    const t = ev.target.closest("[data-eng]");
    if (!t) return;
    // FIX-FINAL-4: évite double navigation / focus fantôme sur boutons Continuer
    if (t.closest("button, .btn")) ev.preventDefault();
    const eng = t.dataset.eng;
    if (eng === "peer-msg-dismiss") {
        ev.preventDefault();
        const { status } = await API.post("/api/engagement/peer-msg-dismiss", {});
        if (status === 200) await refreshEngagementBootstrap();
        swipeGoNext();
        return;
    }
    if (eng === "ttt-replay") {
        ev.preventDefault();
        initTttUi();
        return;
    }
    if (eng === "swipe-empty-retry") {
        ev.preventDefault();
        await loadSuggestions({ reason: "user" });
        lastSwipeDeckSig = computeSwipeDeckSig();
        buildSwipeDeck();
        clampSwipeIndex();
        renderSwipeView();
        return;
    }
    if (eng === "imp") {
        const sid = parseInt(t.dataset.sid, 10);
        const level = parseInt(t.dataset.level, 10);
        const { data, status } = await API.post("/api/engagement/importance", { suggestion_id: sid, level });
        if (status === 200 && data.ok) {
            const s = allSuggestions.find((x) => x.id === sid);
            if (s) {
                s.importance_score = data.importance_score;
                s.hot = (data.importance_score || 0) >= 70;
            }
            await refreshEngagementBootstrap();
            swipeGoNext();
        } else showFeedback((data && data.error) || "Erreur", "error");
        return;
    }
    if (eng === "act-dismiss") {
        await API.post("/api/engagement/activity-card-dismiss", {});
        await refreshEngagementBootstrap();
        swipeGoNext();
        return;
    }
    if (eng === "guess") {
        const sid = parseInt(t.dataset.sid, 10);
        const bucket = t.dataset.bucket;
        const { data, status } = await API.post("/api/engagement/guess", { suggestion_id: sid, bucket });
        if (status === 200) {
            swipeGuessReveal[sid] = {
                actual_pct: data.actual_pct,
                correct: data.correct,
            };
            renderSwipeView();
        } else showFeedback((data && data.error) || "Erreur", "error");
        return;
    }
    if (eng === "guess-next") {
        swipeGoNext();
        return;
    }
    /* FIX-5 / FIX-6 : message — délégué via data-action + submitMessageToOthers (handleDeckDataAction) */
    if (eng === "mood") {
        const mood = t.dataset.mood;
        const { data, status } = await API.post("/api/engagement/mood", { mood });
        if (status === 200 && data.ok) {
            await refreshEngagementBootstrap();
            swipeGoNext();
        } else showFeedback((data && data.error) || "Erreur", "error");
        return;
    }
    if (eng === "dilemma-vote") {
        const did = parseInt(t.dataset.did, 10);
        const side = (t.dataset.side || "").toLowerCase();
        const { data, status } = await API.post("/api/engagement/dilemma-vote", { dilemma_id: did, side });
        if (status === 200) {
            engagementBootstrap = engagementBootstrap || {};
            engagementBootstrap.dilemma = data;
            renderSwipeView();
        } else if (status === 409) {
            await refreshEngagementBootstrap();
            renderSwipeView();
        } else showFeedback((data && data.error) || "Erreur", "error");
        return;
    }
    if (eng === "dilemma-skip") {
        const { data, status } = await API.post("/api/engagement/dilemma-skip", {});
        if (status === 200) {
            await refreshEngagementBootstrap();
            buildSwipeDeck();
            clampSwipeIndex();
            renderSwipeView();
        } else showFeedback((data && data.error) || "Erreur", "error");
        return;
    }
    if (eng === "dilemma-next") {
        swipeGoNext();
        return;
    }
    if (eng === "ttt-dismiss") {
        const { data, status } = await API.post("/api/engagement/ttt-dismiss", {});
        if (status === 200) {
            await refreshEngagementBootstrap();
            buildSwipeDeck();
            clampSwipeIndex();
            renderSwipeView();
        } else showFeedback((data && data.error) || "Erreur", "error");
        return;
    }
}

function setupPhoneSwipe() {
    if (!isTouchDevice()) return;
    const swipeDeckWrap = document.getElementById("swipe-deck-wrap");
    if (swipeDeckWrap && !swipeDeckWrap.dataset.engListener) {
        swipeDeckWrap.dataset.engListener = "1";
        swipeDeckWrap.addEventListener("click", (e) => {
            const cell = e.target.closest(".ttt-cell");
            if (cell && cell.closest("#swipe-ttt-grid")) {
                handleTttCellClick(parseInt(cell.dataset.idx, 10));
                return;
            }
            if (handleDeckDataAction(e)) return;
            handleEngagementClick(e);
        });
    }
    if (swipeDeckWrap && !swipeDeckWrap.dataset.msgInputListener) {
        swipeDeckWrap.dataset.msgInputListener = "1";
        swipeDeckWrap.addEventListener("input", (e) => {
            const ta = e.target.closest("[data-role='message-input']");
            if (!ta) return;
            const card = ta.closest("[data-card-id]");
            const cid = card?.dataset.cardId;
            const cnt = card?.querySelector("[data-role='char-count']");
            if (cnt) cnt.textContent = String(ta.value.length);
            const pseudo = card?.querySelector("[data-role='pseudo-input']")?.value?.trim() || "";
            const sub = cid
                ? document.querySelector(`[data-action="submit-message"][data-card-id="${cid}"]`)
                : null;
            if (sub) sub.disabled = ta.value.trim().length < 3 || pseudo.length < 1;
        });
    }
    if (listSearchInput) {
        listSearchInput.addEventListener("input", () => {
            phoneListSearchQuery = listSearchInput.value || "";
            swipeIndex = 0;
            renderSuggestions(true);
        });
    }
    if (btnPhoneModeSwipe) {
        btnPhoneModeSwipe.addEventListener("click", async () => {
            phoneUiMode = "swipe";
            phoneListLikedOnly = false;
            try {
                localStorage.setItem("phone_ui_mode", "swipe");
                localStorage.removeItem("phone_ui_liked");
            } catch (err) {
                /* ignore */
            }
            syncPhoneUiChrome();
            await refreshEngagementBootstrap();
            lastSwipeDeckSig = "";
            buildSwipeDeck();
            lastSwipeDeckSig = computeSwipeDeckSig();
            engagementPingPresence();
            renderSwipeView();
            scheduleSuggestionsPoll();
        });
    }
    if (btnPhoneModeList) {
        btnPhoneModeList.addEventListener("click", () => {
            phoneUiMode = "list";
            phoneListLikedOnly = false;
            try {
                localStorage.setItem("phone_ui_mode", "list");
                localStorage.removeItem("phone_ui_liked");
            } catch (err) {
                /* ignore */
            }
            syncPhoneUiChrome();
            renderSuggestions(true);
            scheduleSuggestionsPoll();
        });
    }
    if (btnPhoneModeLiked) {
        btnPhoneModeLiked.addEventListener("click", () => {
            phoneUiMode = "list";
            phoneListLikedOnly = true;
            try {
                localStorage.setItem("phone_ui_mode", "list");
                localStorage.setItem("phone_ui_liked", "1");
            } catch (err) {
                /* ignore */
            }
            expanded = true;
            syncPhoneUiChrome();
            renderSuggestions(true);
            scheduleSuggestionsPoll();
        });
    }
    if (filtersMobileToggle && filtersSection) {
        filtersMobileToggle.addEventListener("click", () => {
            const open = filtersSection.classList.toggle("filters-open");
            filtersMobileToggle.setAttribute("aria-expanded", open ? "true" : "false");
        });
    }
    attachSwipeDeckGestures();
}

// --------------- UI Helpers ---------------

function showFeedback(message, type) {
    feedback.textContent = message;
    feedback.className = `feedback ${type} feedback-enter`;
    feedback.classList.remove("hidden");
    requestAnimationFrame(() => feedback.classList.remove("feedback-enter"));
    setTimeout(() => {
        feedback.classList.add("feedback-exit");
        setTimeout(() => {
            feedback.classList.add("hidden");
            feedback.classList.remove("feedback-exit");
        }, 300);
    }, 4000);
}

function hideFeedback() {
    feedback.classList.add("hidden");
}

function setLoading(loading) {
    submitBtn.disabled = loading;
    submitBtn.querySelector(".btn-text").classList.toggle("hidden", loading);
    submitBtn.querySelector(".btn-loader").classList.toggle("hidden", !loading);
}

function clearInput() {
    input.value = "";
    charCount.textContent = "0 / 500";
    submitBtn.disabled = true;
}

function escapeHtml(str) {
    const d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
}

// ── Hype Button: spammable flame for completed suggestions ──────────────
const _hypeState = {};
function _hypeClick(sid) {
    if (!_hypeState[sid]) _hypeState[sid] = { pending: 0, timer: null };
    const st = _hypeState[sid];
    st.pending++;
    const countEl = document.querySelector(`.hype-btn[data-id="${sid}"] .hype-count`);
    if (countEl) countEl.textContent = parseInt(countEl.textContent || "0", 10) + 1;
    const btn = document.querySelector(`.hype-btn[data-id="${sid}"]`);
    if (btn) { btn.classList.add("hype-btn--pop"); requestAnimationFrame(() => requestAnimationFrame(() => btn.classList.remove("hype-btn--pop"))); }
    clearTimeout(st.timer);
    st.timer = setTimeout(() => _hypeFlush(sid), 3000);
}
async function _hypeFlush(sid) {
    const st = _hypeState[sid];
    if (!st || st.pending <= 0) return;
    const count = st.pending;
    st.pending = 0;
    try {
        const r = await fetch(`/api/suggestions/${sid}/hype`, {
            method: "POST", credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ count }),
        });
        const d = await r.json();
        if (d.hype_count != null) {
            const countEl = document.querySelector(`.hype-btn[data-id="${sid}"] .hype-count`);
            if (countEl) countEl.textContent = d.hype_count;
            const s = allSuggestions.find(x => x.id === sid);
            if (s) s.hype_count = d.hype_count;
        }
    } catch { /* silent */ }
}
document.addEventListener("click", (e) => {
    const btn = e.target.closest(".hype-btn");
    if (btn) { e.preventDefault(); _hypeClick(parseInt(btn.dataset.id, 10)); }
});

// NFC-V2.2-AI: anonymous session notification centre
const _notifState = { open: false, data: [], pollTimer: null };

function _notifQs() {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    const sc = window.innerWidth < 380 ? "small" : window.innerWidth < 600 ? "medium" : "large";
    return `timezone=${encodeURIComponent(tz)}&screen_category=${encodeURIComponent(sc)}`;
}

async function _notifFetch() {
    try {
        const r = await fetch(`/api/notifications?${_notifQs()}`, { credentials: "same-origin" });
        if (!r.ok) return;
        const d = await r.json();
        _notifState.data = d.notifications || [];
        const bell = document.getElementById("notif-bell");
        const badge = document.getElementById("notif-badge");
        if (!bell) return;
        if (d.unread_count > 0 || _notifState.data.length > 0) {
            bell.style.display = "";
            if (d.unread_count > 0) {
                badge.textContent = d.unread_count > 99 ? "99+" : String(d.unread_count);
                badge.style.display = "";
            } else {
                badge.style.display = "none";
            }
        }
        if (_notifState.open) _notifRenderList();
    } catch (e) { /* silent */ }
}

function _notifRelTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return "à l'instant";
    if (diff < 3600) return `il y a ${Math.floor(diff / 60)} min`;
    if (diff < 86400) return `il y a ${Math.floor(diff / 3600)} h`;
    return `il y a ${Math.floor(diff / 86400)} j`;
}

function _notifRenderList() {
    const list = document.getElementById("notif-list");
    if (!list) return;
    if (!_notifState.data.length) {
        list.innerHTML = '<p class="notif-empty">Aucune notification</p>';
        return;
    }
    list.innerHTML = _notifState.data.map(n =>
        `<div class="notif-item ${n.is_read ? "" : "notif-unread"}" data-nid="${n.id}">
            <span class="notif-dot"></span>
            <div>
                <div class="notif-msg">${escapeHtml(n.message)}</div>
                <div class="notif-time">${_notifRelTime(n.created_at)}</div>
            </div>
        </div>`
    ).join("");
}

function _notifToggle() {
    const panel = document.getElementById("notif-panel");
    if (!panel) return;
    _notifState.open = !_notifState.open;
    panel.style.display = _notifState.open ? "" : "none";
    if (_notifState.open) {
        _notifRenderList();
        _notifFetch();
    }
}

async function _notifMarkAllRead() {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    const sc = window.innerWidth < 380 ? "small" : window.innerWidth < 600 ? "medium" : "large";
    try {
        await fetch("/api/notifications/read", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ all: true, timezone: tz, screen_category: sc }),
        });
        _notifState.data.forEach(n => n.is_read = true);
        _notifRenderList();
        const badge = document.getElementById("notif-badge");
        if (badge) badge.style.display = "none";
    } catch (e) { /* silent */ }
}

function _notifInit() {
    const bell = document.getElementById("notif-bell");
    if (bell) bell.addEventListener("click", _notifToggle);
    const markAll = document.getElementById("notif-mark-all");
    if (markAll) markAll.addEventListener("click", _notifMarkAllRead);
    document.addEventListener("click", (e) => {
        if (_notifState.open &&
            !e.target.closest("#notif-panel") &&
            !e.target.closest("#notif-bell")) {
            _notifState.open = false;
            const panel = document.getElementById("notif-panel");
            if (panel) panel.style.display = "none";
        }
    });
    _notifFetch();
    _notifState.pollTimer = setInterval(_notifFetch, 60000);
}

document.addEventListener("DOMContentLoaded", () => { init(); _notifInit(); });
