function _swipeGoNextImmediate() {
    const list = swipeDeckItems;
    if (list.length === 0) return;

    if (swipeIndex >= list.length - 1) {
        const cur = list[swipeIndex];
        if (cur && cur.kind === "suggestion") {
            swipeConsumedIds.add(cur.id);
            saveSwipeConsumedIds();
            SwipeHistory.markSeen(String(cur.id));
            showFeedback("Plus de nouvelles suggestions.", "info");
            buildSwipeDeck();
            lastSwipeDeckSig = computeSwipeDeckSig();
            swipeIndex = 0;
            try { sessionStorage.removeItem(SWIPE_DECK_ANCHOR_KEY); } catch (e) { /* ignore */ }
            _swipeTransitionDir = "next";
            renderSwipeView();
            return;
        }
        if (cur && cur.kind === "special") {
            swipeDeckItems = [{ kind: "end", type: "session_done" }];
            swipeIndex = 0;
            try { sessionStorage.removeItem(SWIPE_DECK_ANCHOR_KEY); } catch (e) { /* ignore */ }
            _swipeTransitionDir = "next";
            renderSwipeView();
            return;
        }
        showFeedback("Plus de nouvelles suggestions.", "info");
        return;
    }

    const cur = list[swipeIndex];
    if (cur && cur.kind === "suggestion") {
        swipeConsumedIds.add(cur.id);
        saveSwipeConsumedIds();
        SwipeHistory.markSeen(String(cur.id));
    }
    swipeIndex++;
    engagementPingSwipe();
    _swipeTransitionDir = "next";
    renderSwipeView();
}

function swipeGoNext() {
    if (_swipeAnimating) return;
    const layer = swipeDeckInner ? swipeDeckInner.querySelector("#swipe-active-layer") : null;
    const m = layer && layer.style.transform ? layer.style.transform.match(/translateX\(([^)]+)\)/) : null;
    const tx = m ? parseFloat(m[1]) : 0;
    if (Math.abs(tx) > 60) { _swipeGoNextImmediate(); return; }
    _animateSwipeExit("next", _swipeGoNextImmediate);
}

function swipeGoPrev() {
    if (_swipeAnimating) return;
    const list = swipeDeckItems;
    if (list.length === 0 || swipeIndex <= 0) return;
    const layer = swipeDeckInner ? swipeDeckInner.querySelector("#swipe-active-layer") : null;
    const m = layer && layer.style.transform ? layer.style.transform.match(/translateX\(([^)]+)\)/) : null;
    const tx = m ? parseFloat(m[1]) : 0;
    const doRender = () => {
        swipeIndex--;
        engagementPingSwipe();
        _swipeTransitionDir = "prev";
        renderSwipeView();
    };
    if (Math.abs(tx) > 60) { doRender(); return; }
    _animateSwipeExit("prev", doRender);
}

