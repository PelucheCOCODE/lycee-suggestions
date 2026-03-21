/**
 * Pagination du panneau bus avec transition + resize (display + displaybus).
 */
(function () {
    const G = typeof window !== "undefined" ? window : globalThis;

    function measureRowsPerPage(containerEl) {
        let mainH = containerEl.clientHeight;
        if (mainH < 80) {
            const ph = containerEl.parentElement;
            mainH = ph ? ph.clientHeight : Math.max(200, window.innerHeight * 0.55);
        }
        const usable = Math.max(100, mainH - 8);
        const wrap = document.createElement("div");
        wrap.style.cssText = "visibility:hidden;position:absolute;left:-9999px;width:min(96vw,1200px);";
        wrap.innerHTML =
            typeof busBoardTableHtml === "function"
                ? busBoardTableHtml(
                      [
                          {
                              route_name: "00",
                              stop_name: "Arrêt",
                              side_a: {
                                  direction: "Sens A",
                                  urgency: "normal",
                                  primary: { minutes: 9, label: "9 min", urgency: "normal", is_imminent: false },
                                  secondary: { minutes: 21, label: "21 min" },
                              },
                              side_b: {
                                  direction: "Sens B",
                                  urgency: "normal",
                                  primary: { minutes: 12, label: "12 min", urgency: "normal", is_imminent: false },
                                  secondary: { minutes: 28, label: "28 min" },
                              },
                          },
                      ],
                      { showHead: true },
                  )
                : "";
        document.body.appendChild(wrap);
        const headEl = wrap.querySelector(".bus-dep-colhead");
        const rowEl = wrap.querySelector(".bus-dep-row");
        const headH = headEl ? headEl.offsetHeight + 8 : 0;
        const rowH = rowEl ? Math.max(56, rowEl.offsetHeight + 6) : 80;
        wrap.remove();
        return Math.max(2, Math.floor((usable - headH) / rowH));
    }

    function splitPages(deps, containerEl) {
        const per = measureRowsPerPage(containerEl);
        if (!deps || deps.length <= per) return [deps || []];
        const out = [];
        for (let i = 0; i < deps.length; i += per) {
            out.push(deps.slice(i, i + per));
        }
        return out;
    }

    function initScroll(root) {
        root.querySelectorAll(".bus-scroll-text").forEach((el) => {
            if (el.scrollWidth > el.parentElement.offsetWidth) el.classList.add("bus-scroll-animate");
        });
    }

    /**
     * @returns {{ destroy: function, refresh: function }}
     */
    G.busBoardPagesMount = function busBoardPagesMount(containerEl, initialDeps, options) {
        const o = options || {};
        const pageSwitchMs = o.pageSwitchMs || 12000;
        const pageHintEl = o.pageHintEl || null;
        const onPageChange = o.onPageChange || null;

        let departures = (initialDeps || []).slice();
        let pages = [];
        let pageIndex = 0;
        let rotateTimer = null;

        function updateHint() {
            if (!pageHintEl) return;
            if (pages.length <= 1) {
                pageHintEl.hidden = true;
                pageHintEl.textContent = "";
            } else {
                pageHintEl.hidden = false;
                pageHintEl.textContent = `Page ${pageIndex + 1} / ${pages.length} · alternance ${pageSwitchMs / 1000}s`;
            }
        }

        function renderPageIndex(i, animate) {
            const chunk = pages[i % pages.length] || [];
            const inner = containerEl.querySelector(".bus-dep-page-inner");
            if (!inner) return;
            const html =
                typeof busBoardTableHtml === "function"
                    ? busBoardTableHtml(chunk, { showHead: true })
                    : "";
            if (animate && pages.length > 1) {
                inner.classList.add("bus-dep-page--exit");
                setTimeout(() => {
                    inner.innerHTML = html;
                    inner.classList.remove("bus-dep-page--exit");
                    inner.classList.add("bus-dep-page--enter");
                    requestAnimationFrame(() => {
                        inner.classList.remove("bus-dep-page--enter");
                        initScroll(containerEl);
                    });
                    updateHint();
                    if (onPageChange) onPageChange(pageIndex, pages.length);
                }, 300);
            } else {
                inner.innerHTML = html;
                initScroll(containerEl);
                updateHint();
                if (onPageChange) onPageChange(pageIndex, pages.length);
            }
        }

        function startRotation() {
            if (rotateTimer) clearInterval(rotateTimer);
            rotateTimer = null;
            if (pages.length <= 1) return;
            rotateTimer = setInterval(() => {
                pageIndex = (pageIndex + 1) % pages.length;
                renderPageIndex(pageIndex, true);
            }, pageSwitchMs);
        }

        function rebuild() {
            if (rotateTimer) {
                clearInterval(rotateTimer);
                rotateTimer = null;
            }
            pages = splitPages(departures, containerEl);
            if (pageIndex >= pages.length) pageIndex = 0;
            containerEl.innerHTML = '<div class="bus-dep-page-inner"></div>';
            renderPageIndex(pageIndex, false);
            startRotation();
        }

        containerEl.innerHTML = '<div class="bus-dep-page-inner"></div>';
        pages = splitPages(departures, containerEl);
        renderPageIndex(0, false);
        startRotation();

        let resizeT = null;
        function onResize() {
            pages = splitPages(departures, containerEl);
            if (pageIndex >= pages.length) pageIndex = 0;
            renderPageIndex(pageIndex, false);
            startRotation();
        }
        const resizeHandler = () => {
            clearTimeout(resizeT);
            resizeT = setTimeout(onResize, 200);
        };
        window.addEventListener("resize", resizeHandler, { passive: true });

        requestAnimationFrame(() => {
            rebuild();
        });

        return {
            destroy() {
                if (rotateTimer) clearInterval(rotateTimer);
                window.removeEventListener("resize", resizeHandler);
                containerEl.innerHTML = "";
            },
            refresh(newDeps) {
                departures = (newDeps || []).slice();
                pageIndex = 0;
                rebuild();
            },
        };
    };
})();
