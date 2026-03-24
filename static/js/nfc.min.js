// NFC-V2.3-UI: frontend mobile-first pour page NFC lieu
(function () {
    "use strict";

    const app = document.getElementById("nfc-app");
    if (!app) return;
    const SLUG = app.dataset.slug;
    const API_BASE = `/api/nfc/${SLUG}`;
    const POLL_INTERVAL = 12000;

    let locationData = null;
    let suggestions = [];
    let pollTimer = null;
    let scanValid = false;
    let scanRemaining = 0;

    function esc(s) {
        const d = document.createElement("div");
        d.textContent = s || "";
        return d.innerHTML;
    }

    function timeAgo(isoStr) {
        if (!isoStr) return "";
        const diff = (Date.now() - new Date(isoStr).getTime()) / 1000;
        if (diff < 60) return "À l'instant";
        if (diff < 3600) return `il y a ${Math.floor(diff / 60)} min`;
        if (diff < 86400) return `il y a ${Math.floor(diff / 3600)}h`;
        return `il y a ${Math.floor(diff / 86400)}j`;
    }

    function haptic() {
        try { navigator.vibrate?.(12); } catch (e) { /* */ }
    }

    // ── Toast ────────────────────────────────────────────────────────────────

    const toastEl = document.getElementById("nfc-toast");
    let toastTimeout = null;

    function showToast(msg, type) {
        if (!toastEl) return;
        toastEl.textContent = msg;
        toastEl.className = `nfc-toast nfc-toast--${type || "info"}`;
        toastEl.hidden = false;
        clearTimeout(toastTimeout);
        toastTimeout = setTimeout(() => { toastEl.hidden = true; }, 3500);
    }

    // ── API ──────────────────────────────────────────────────────────────────

    async function apiFetch(url, opts) {
        try {
            const r = await fetch(url, {
                credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                ...opts,
            });
            const data = await r.json();
            return { data, status: r.status };
        } catch (e) {
            return { data: null, status: 0 };
        }
    }

    // ── Chargement données ──────────────────────────────────────────────────

    async function loadData(silent) {
        const { data, status } = await apiFetch(API_BASE);
        if (status !== 200 || !data) {
            if (!silent) showToast("Erreur de chargement.", "error");
            return;
        }
        locationData = data.location;
        suggestions = data.suggestions || [];
        // NFC-V2.3-UI: état du scan token
        scanValid = !!data.scan_valid;
        scanRemaining = data.scan_remaining || 0;
        render();
    }

    // ── NFC-V2.3-UI: Rendu ─────────────────────────────────────────────────

    // NFC-V2.4: rendu d'une carte suggestion avec heat score + support
    function _renderCard(s) {
        const heatClass = s.heat_level === "urgent" ? "nfc-card--hot"
                        : s.heat_level === "important" ? "nfc-card--warm" : "";
        const statusClass = s.status === "resolved" ? " nfc-card--resolved"
                          : s.status === "in_progress" ? " nfc-card--in-progress" : "";
        const recurBadge = s.is_recurring
            ? `<span class="nfc-badge nfc-badge--recur">🔄 Récurrent</span>` : "";
        const confirmBtn = s.status === "resolved" ? ""
            : s.already_confirmed
            ? `<button class="nfc-btn-confirm nfc-btn-confirm--done" disabled>✓ Déjà confirmé</button>`
            : `<button class="nfc-btn-confirm${!scanValid ? " nfc-btn-confirm--locked" : ""}" data-sid="${s.id}" ${!scanValid ? "disabled" : ""}>👍 Confirmer (sur place)</button>`;
        // NFC-V2.4: bouton soutenir = vote principal
        const alreadyVoted = s.has_voted;
        const supportBtn = s.status === "resolved" ? ""
            : alreadyVoted
            ? `<button class="nfc-btn-support nfc-btn-support--done" data-support-sid="${s.id}" disabled>✓ Soutenu</button>`
            : `<button class="nfc-btn-support" data-support-sid="${s.id}">👍 Soutenir</button>`;

        const badges = [];
        if (s.heat_level === "urgent") badges.push('<span class="nfc-badge nfc-badge--urgent">⚠️ Urgent</span>');
        else if (s.heat_level === "important") badges.push('<span class="nfc-badge nfc-badge--important">🔶 Important</span>');
        if (s.status === "in_progress") badges.push('<span class="nfc-badge nfc-badge--in-progress">🔧 En cours</span>');
        if (s.status === "resolved") badges.push('<span class="nfc-badge nfc-badge--resolved">✅ Résolu</span>');
        if (s.processing) badges.push('<span class="nfc-badge nfc-badge--processing">⏳ Traitement</span>');
        badges.push(recurBadge);

        const replyHtml = s.admin_reply
            ? `<div class="nfc-card-reply">
                <div class="nfc-card-reply-header"><span class="nfc-card-reply-label">💬 Réponse de l'administration</span>${s.admin_reply_at ? `<span class="nfc-card-reply-time">${timeAgo(s.admin_reply_at)}</span>` : ""}</div>
                <div class="nfc-card-reply-text">${esc(s.admin_reply)}</div>
              </div>`
            : "";

        const followBtn = scanValid && s.status !== "resolved"
            ? `<button class="nfc-btn-follow" data-sid="${s.id}" title="Suivre ce problème">🔔</button>`
            : "";

        // NFC-V2.4: afficher heat score + last activity
        const heatInfo = s.heat_score != null ? `<span title="Score de chaleur">${s.heat_score}pts</span>` : "";
        const actInfo = s.last_activity_minutes != null
            ? `<span>${s.last_activity_minutes < 60 ? `activité il y a ${s.last_activity_minutes} min` : s.last_activity_minutes < 1440 ? `activité il y a ${Math.floor(s.last_activity_minutes / 60)}h` : `activité il y a ${Math.floor(s.last_activity_minutes / 1440)}j`}</span>`
            : "";

        return `
        <div class="nfc-card ${heatClass}${statusClass}" data-sid="${s.id}">
            <div class="nfc-card-top">
                <h3 class="nfc-card-title">${esc(s.title)}</h3>
                <div class="nfc-card-badges">${badges.filter(Boolean).join("")}</div>
            </div>
            ${replyHtml}
            <div class="nfc-card-meta">
                <span class="nfc-card-confirms" title="${s.confirmation_count} confirmation(s)">🔥 ${s.confirmation_count}</span>
                ${s.vote_count ? `<span title="${s.vote_count} soutien(s)">👍 ${s.vote_count}</span>` : ""}
                ${heatInfo}
                ${actInfo}
                <span class="nfc-card-time">${timeAgo(s.created_at)}</span>
                ${followBtn}
            </div>
            ${confirmBtn || supportBtn ? `<div class="nfc-card-action" style="display:flex;gap:0.4rem">${confirmBtn}${supportBtn}</div>` : ""}
            </div>`;
    }

    function render() {
        const container = document.getElementById("nfc-problems");
        const countEl = document.getElementById("nfc-problem-count");
        if (countEl) countEl.textContent = suggestions.length;

        // NFC-V2.3-UI: scan banner
        const scanBanner = document.getElementById("nfc-scan-banner");
        if (scanBanner) {
            if (!scanValid) {
                scanBanner.hidden = false;
                scanBanner.innerHTML = '<span class="nfc-scan-banner-icon">📱</span> Scannez le tag NFC pour pouvoir agir sur ce lieu.';
            } else {
                scanBanner.hidden = true;
            }
        }

        const btnSuggest = document.getElementById("nfc-open-suggest");
        if (btnSuggest) {
            btnSuggest.disabled = !scanValid;
            btnSuggest.style.opacity = scanValid ? "1" : "0.5";
        }

        if (!suggestions.length) {
            container.innerHTML = `
                <div class="nfc-empty">
                    <div class="nfc-empty-icon">✅</div>
                    <p>Aucun problème signalé pour le moment.</p>
                    <p class="nfc-empty-sub">Scannez le tag et soyez le premier à signaler un souci !</p>
                </div>`;
            return;
        }

        // NFC-V2.3-UI: separate active (open, in_progress) from resolved/archived
        const active = suggestions.filter(s => s.status === "open" || s.status === "in_progress");
        const resolved = suggestions.filter(s => s.status === "resolved");

        let html = "";
        if (active.length) {
            html += `<div class="nfc-section-label">Problèmes actifs (${active.length})</div>`;
            html += active.map(_renderCard).join("");
        }
        if (resolved.length) {
            html += `<div class="nfc-section-label">Résolus (${resolved.length})</div>`;
            html += resolved.map(_renderCard).join("");
        }
        if (!active.length && !resolved.length) {
            html = suggestions.map(_renderCard).join("");
        }
        container.innerHTML = html;
    }

    // ── Confirmation ─────────────────────────────────────────────────────────

    async function confirmProblem(sid) {
        haptic();
        const btn = document.querySelector(`.nfc-btn-confirm[data-sid="${sid}"]`);
        if (btn) { btn.disabled = true; btn.textContent = "…"; }

        const { data, status } = await apiFetch(`${API_BASE}/confirm`, {
            method: "POST",
            body: JSON.stringify({ suggestion_id: sid }),
        });

        if (status === 403 && data?.scan_required) {
            showToast("Scannez le tag NFC pour agir.", "warning");
            scanValid = false; render();
            return;
        }
        if (status === 429 && data?.retry_after) {
            showToast(data.error || "Patientez un peu.", "warning");
            if (btn) { btn.disabled = false; btn.textContent = "👍 C'est toujours le cas"; }
            return;
        }
        if (status !== 200 || !data?.ok) {
            showToast(data?.error || "Erreur.", "error");
            if (btn) { btn.disabled = false; btn.textContent = "👍 C'est toujours le cas"; }
            return;
        }

        showToast("Merci pour votre confirmation !", "success");
        if (btn) {
            btn.classList.add("nfc-btn-confirm--done");
            btn.textContent = "✓ Confirmé";
            btn.disabled = true;
        }

        const s = suggestions.find((x) => x.id === sid);
        if (s) {
            s.confirmation_count = data.confirmation_count;
            s.already_confirmed = true;
            s.last_confirmed_at = data.last_confirmed_at;
        }
        const countSpan = document.querySelector(`.nfc-card[data-sid="${sid}"] .nfc-card-confirms`);
        if (countSpan) countSpan.innerHTML = `🔥 ${data.confirmation_count}`;
    }

    // ── Nouvelle suggestion ──────────────────────────────────────────────────

    const overlay = document.getElementById("nfc-sheet-overlay");
    const sheet = document.getElementById("nfc-sheet");
    const textarea = document.getElementById("nfc-suggest-text");
    const charCount = document.getElementById("nfc-char-count");
    const btnOpen = document.getElementById("nfc-open-suggest");
    const btnCancel = document.getElementById("nfc-cancel-suggest");
    const btnSubmit = document.getElementById("nfc-submit-suggest");

    function openSheet() {
        if (!scanValid) { showToast("Scannez le tag NFC pour signaler un problème.", "warning"); return; }
        if (overlay) overlay.hidden = false;
        if (textarea) { textarea.value = ""; textarea.focus(); }
        if (charCount) charCount.textContent = "0";
    }
    function closeSheet() {
        if (overlay) overlay.hidden = true;
    }

    if (btnOpen) btnOpen.addEventListener("click", openSheet);
    if (btnCancel) btnCancel.addEventListener("click", closeSheet);
    if (overlay) overlay.addEventListener("click", (e) => {
        if (e.target === overlay) closeSheet();
    });
    if (textarea) textarea.addEventListener("input", () => {
        if (charCount) charCount.textContent = textarea.value.length;
    });

    if (btnSubmit) btnSubmit.addEventListener("click", async () => {
        const text = (textarea?.value || "").trim();
        if (text.length < 5) {
            showToast("Décrivez le problème (5 caractères min).", "warning");
            return;
        }
        btnSubmit.disabled = true;
        btnSubmit.textContent = "Envoi…";
        haptic();

        const { data, status } = await apiFetch(`${API_BASE}/suggest`, {
            method: "POST",
            body: JSON.stringify({ text }),
        });

        if (status === 403 && data?.scan_required) {
            showToast("Scannez le tag NFC pour agir.", "warning");
            scanValid = false; render(); closeSheet();
            btnSubmit.disabled = false; btnSubmit.textContent = "Envoyer";
            return;
        }
        if (status === 429 && data?.retry_after) {
            showToast(data.error || "Patientez un peu.", "warning");
            btnSubmit.disabled = false; btnSubmit.textContent = "Envoyer";
            return;
        }
        if (data?.confirm_instead) {
            showToast(data.message || "Un problème similaire existe déjà.", "info");
            closeSheet();
            btnSubmit.disabled = false; btnSubmit.textContent = "Envoyer";
            const ex = data.existing;
            if (ex) {
                const card = document.querySelector(`.nfc-card[data-sid="${ex.id}"]`);
                if (card) {
                    card.classList.add("nfc-card--highlight");
                    card.scrollIntoView({ behavior: "smooth", block: "center" });
                    setTimeout(() => card.classList.remove("nfc-card--highlight"), 2500);
                }
            }
            return;
        }
        if (status !== 200 || !data?.ok) {
            showToast(data?.error || "Erreur.", "error");
            btnSubmit.disabled = false; btnSubmit.textContent = "Envoyer";
            return;
        }

        showToast("Signalement enregistré, merci !", "success");
        closeSheet();
        btnSubmit.disabled = false; btnSubmit.textContent = "Envoyer";
        await loadData(true);
    });

    // ── Follow (suivre) ──────────────────────────────────────────────────────

    document.getElementById("nfc-problems")?.addEventListener("click", async (e) => {
        const followBtn = e.target.closest(".nfc-btn-follow");
        if (followBtn) {
            const sid = parseInt(followBtn.dataset.sid, 10);
            const { data, status } = await apiFetch(`${API_BASE}/follow`, {
                method: "POST", body: JSON.stringify({ suggestion_id: sid }),
            });
            if (data?.ok) {
                followBtn.textContent = "🔔✓";
                followBtn.disabled = true;
                showToast("Vous serez notifié des mises à jour.", "success");
            }
            return;
        }
        // NFC-V2.4: soutenir = même vote que la page principale
        const supportBtn = e.target.closest(".nfc-btn-support:not([disabled])");
        if (supportBtn) {
            const sid = parseInt(supportBtn.dataset.supportSid, 10);
            supportBtn.disabled = true;
            haptic();
            const { data, status } = await apiFetch(`/api/suggestions/${sid}/vote`, {
                method: "POST", body: JSON.stringify({ vote_type: "for" }),
            });
            if (data?.has_voted) {
                showToast("Merci pour votre soutien !", "success");
                supportBtn.textContent = "✓ Soutenu";
                supportBtn.classList.add("nfc-btn-support--done");
                const s = suggestions.find(x => x.id === sid);
                if (s) { s.vote_count = data.vote_count; s.has_voted = true; }
            } else if (status === 429) {
                showToast("Merci, vous avez déjà soutenu.", "info");
                supportBtn.textContent = "✓ Soutenu";
            } else {
                supportBtn.disabled = false;
                showToast(data?.error || "Erreur.", "error");
            }
            return;
        }
        const btn = e.target.closest(".nfc-btn-confirm:not([disabled])");
        if (!btn) return;
        const sid = parseInt(btn.dataset.sid, 10);
        if (Number.isFinite(sid)) confirmProblem(sid);
    });

    // ── Polling ──────────────────────────────────────────────────────────────

    function startPolling() {
        stopPolling();
        pollTimer = setInterval(() => loadData(true), POLL_INTERVAL);
    }
    function stopPolling() {
        if (pollTimer) clearInterval(pollTimer);
    }

    document.addEventListener("visibilitychange", () => {
        if (document.hidden) stopPolling();
        else { loadData(true); startPolling(); }
    });

    // ── Init ─────────────────────────────────────────────────────────────────

    loadData(false).then(startPolling);
})();
