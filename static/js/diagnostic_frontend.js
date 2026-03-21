/**
 * diagnostic_frontend.js — page élève (student)
 * Lance : runLyceeVoteDiagnostic() dans la console, ou ?debug=diag dans l’URL.
 * Attend window.__lyceeDiag (exposé par student.js).
 */
(function () {
    const OK = "color: #22c55e; font-weight: bold";
    const FAIL = "color: #ef4444; font-weight: bold";
    const INFO = "color: #60a5fa";
    const WARN = "color: #f59e0b";
    const HEAD = "color: #a78bfa; font-weight: bold; font-size: 13px";

    function runLyceeVoteDiagnostic() {
        const issues = [];

        function ok(msg) {
            console.log(`%c  ✓ ${msg}`, OK);
        }
        function fail(msg) {
            console.log(`%c  ✗ ${msg}`, FAIL);
            issues.push(msg);
        }
        function info(msg) {
            console.log(`%c  → ${msg}`, INFO);
        }
        function warn(msg) {
            console.log(`%c  ⚠ ${msg}`, WARN);
        }
        function head(msg) {
            console.log(`%c\n${msg}`, HEAD);
        }

        const D = typeof window !== "undefined" ? window.__lyceeDiag : null;
        const allSuggestions = D && Array.isArray(D.allSuggestions) ? D.allSuggestions : null;
        const voteLocksBySuggestionId = D && D.voteLocksBySuggestionId;
        const lastVoteServerTs = D && D.lastVoteServerTs;
        const voteOptimisticUntil = D && D.voteOptimisticUntil;

        head("═══ DIAGNOSTIC FRONTEND LIKE/UNLIKE (lycee-suggestions) ═══");
        head("1. Variables globales (__lyceeDiag)");

        allSuggestions
            ? ok(`allSuggestions[] accessible (${allSuggestions.length} suggestions)`)
            : fail(
                  "allSuggestions[] introuvable — window.__lyceeDiag absent ou vide (student.js chargé ?)",
              );

        voteLocksBySuggestionId && typeof voteLocksBySuggestionId.has === "function"
            ? ok("voteLocksBySuggestionId accessible")
            : warn("voteLocksBySuggestionId introuvable");

        lastVoteServerTs && typeof lastVoteServerTs === "object"
            ? ok("lastVoteServerTs accessible")
            : warn("lastVoteServerTs introuvable");

        voteOptimisticUntil && typeof voteOptimisticUntil === "object"
            ? ok("voteOptimisticUntil accessible")
            : warn("voteOptimisticUntil introuvable");

        head("2. Cohérence allSuggestions[] ↔ DOM liste (.suggestion-card[data-id])");

        if (allSuggestions) {
            let divergences = 0;

            allSuggestions.forEach((s) => {
                const card = document.querySelector(`.suggestion-card[data-id="${s.id}"]`);
                if (!card) return;

                const domHasVoted = card.dataset.hasVoted;
                const memHasVoted = s.has_voted ? "true" : "false";
                const btn = card.querySelector(".suggestion-vote-btn");
                const cssVoted =
                    card.classList.contains("suggestion-card--user-voted") ||
                    (btn && btn.classList.contains("voted"));

                let domVoteCount = "";
                if (btn && btn.textContent) {
                    const m = btn.textContent.match(/·\s*(\d+)\s*$/);
                    domVoteCount = m ? m[1] : "";
                }

                const memVoteCount = String(s.vote_count ?? "?");

                const domOk = domHasVoted === memHasVoted;
                const cssOk = cssVoted === (s.has_voted === true);

                if (!domOk || !cssOk) {
                    divergences++;
                    console.group(`%c  ✗ Divergence suggestion #${s.id} "${(s.title || "").slice(0, 30)}"`, FAIL);
                    console.log(`    Mémoire has_voted : ${memHasVoted}`);
                    console.log(`    DOM data-has-voted: ${domHasVoted ?? "(absent)"}`);
                    console.log(`    CSS user-voted    : ${cssVoted}`);
                    console.log(`    Mémoire vote_count: ${memVoteCount}`);
                    console.log(`    DOM (bouton)      : ${domVoteCount || "(non parsé)"}`);
                    console.groupEnd();
                }
            });

            divergences === 0
                ? ok("Aucune divergence mémoire ↔ DOM liste")
                : fail(`${divergences} divergence(s) mémoire ↔ DOM liste`);
        }

        head("3. État DOM swipe (#swipe-active-layer)");

        const swipeLayer = document.getElementById("swipe-active-layer");
        const swipeCard = swipeLayer?.querySelector(".swipe-card[data-id]");

        if (!swipeLayer) {
            info("Pas de #swipe-active-layer — mode swipe inactif ou desktop");
        } else if (!swipeCard) {
            info("#swipe-active-layer vide — aucune carte suggestion swipe active");
        } else {
            const swipeId = swipeCard.dataset.id;
            const swipeHasVoted = swipeCard.dataset.hasVoted;
            const swipeCss = swipeCard.classList.contains("swipe-card--liked");

            info(`Carte swipe active : suggestion #${swipeId}`);
            info(`  data-has-voted  : ${swipeHasVoted ?? "(ABSENT)"}`);
            info(`  classe --liked  : ${swipeCss}`);

            if (swipeHasVoted !== "true" && swipeHasVoted !== "false") {
                fail(
                    "data-has-voted absent ou invalide sur la carte swipe — toggle peut être incohérent",
                );
            } else if ((swipeHasVoted === "true") !== swipeCss) {
                fail(
                    `Divergence carte swipe : data-has-voted="${swipeHasVoted}" mais .swipe-card--liked=${swipeCss}`,
                );
            } else {
                ok("Carte swipe cohérente (data-has-voted ↔ .swipe-card--liked)");
            }

            if (allSuggestions && swipeId) {
                const memSugg = allSuggestions.find((x) => String(x.id) === String(swipeId));
                if (memSugg) {
                    const memHv = memSugg.has_voted ? "true" : "false";
                    if (memHv !== swipeHasVoted) {
                        fail(
                            `Divergence swipe ↔ mémoire : DOM="${swipeHasVoted}" mémoire="${memHv}" pour #${swipeId}`,
                        );
                    } else {
                        ok(`Carte swipe alignée avec allSuggestions[] pour #${swipeId}`);
                    }
                }
            }
        }

        head("4. Scripts student");

        const scriptTags = Array.from(document.querySelectorAll("script[src]"))
            .map((s) => s.src)
            .filter((s) => /student/i.test(s));
        if (scriptTags.length) {
            info(`Scripts : ${scriptTags.join(", ")}`);
        }

        head("5. Cookies de session");

        const cookies = document.cookie || "";
        if (!cookies) {
            warn("Aucun cookie — session Flask peut être absente (HTTP non sécurisé / blocage)");
        } else {
            const hasLycee = cookies.includes("lycee_session");
            const hasGeneric = /(^|;\s*)session=/.test(cookies);
            hasLycee || hasGeneric
                ? ok(`Cookie de session détecté (${hasLycee ? "lycee_session" : "session"})`)
                : warn("Cookies présents mais pas lycee_session / session");
            info(`Cookies (extrait) : ${cookies.slice(0, 220)}`);
        }

        head("6. GET /api/suggestions");

        fetch("/api/suggestions", { credentials: "same-origin" })
            .then((r) => {
                if (!r.ok) {
                    fail(`GET /api/suggestions → ${r.status}`);
                    return null;
                }
                return r.json();
            })
            .then((data) => {
                if (!data) {
                    printSummary(issues);
                    return;
                }
                const suggestions = Array.isArray(data) ? data : data.suggestions || [];
                info(`${suggestions.length} suggestions reçues`);

                const withHasVoted = suggestions.filter((s) => "has_voted" in s);
                withHasVoted.length === suggestions.length
                    ? ok("Toutes les suggestions ont has_voted")
                    : fail(`${suggestions.length - withHasVoted.length} entrées sans has_voted`);

                const withServerTs = suggestions.filter((s) => "server_ts" in s);
                withServerTs.length === suggestions.length
                    ? ok("Toutes les suggestions ont server_ts")
                    : warn(`${suggestions.length - withServerTs.length} entrées sans server_ts`);

                const voted = suggestions.filter((s) => s.has_voted);
                info(`${voted.length} suggestion(s) likée(s) côté serveur`);
                voted.forEach((s) => info(`  → #${s.id} "${(s.title || "").slice(0, 40)}"`));

                printSummary(issues);
            })
            .catch((e) => {
                fail(`Erreur fetch GET /api/suggestions : ${e.message}`);
                printSummary(issues);
            });

        function printSummary(iss) {
            head("═══ RÉSULTAT ═══");
            if (iss.length === 0) {
                console.log("%c  ✓ Aucun problème détecté côté cohérence DOM / API.", OK);
                console.log("%c  → En cas de bug UX, vérifier submitSuggestionVoteAction + patchSwipe.", INFO);
                console.log("%c  → Côté serveur : python diagnostic_votes.py", INFO);
            } else {
                console.log(`%c  ✗ ${iss.length} problème(s) :`, FAIL);
                iss.forEach((i) => console.log(`%c    • ${i}`, FAIL));
            }
        }
    }

    window.runLyceeVoteDiagnostic = runLyceeVoteDiagnostic;

    function scheduleAutoDiag() {
        const sp = new URLSearchParams(window.location.search);
        if (sp.get("debug") !== "diag") return;
        console.log("%c[lycee] Diagnostic auto dans ~2s (?debug=diag) — ou runLyceeVoteDiagnostic()", INFO);
        setTimeout(() => runLyceeVoteDiagnostic(), 2000);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", scheduleAutoDiag);
    } else {
        scheduleAutoDiag();
    }
})();

