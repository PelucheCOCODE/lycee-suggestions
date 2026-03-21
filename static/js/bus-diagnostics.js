/**
 * Diagnostics console pour /api/display/bus — activer avec localStorage.bus_debug=1 ou ?bus_debug=1
 * L’API renvoie alors diagnostics si ?debug=1 (voir app.py display_bus).
 */
(function (G) {
    function busDebugEnabled() {
        try {
            if (typeof localStorage !== "undefined" && localStorage.getItem("bus_debug") === "1") {
                return true;
            }
        } catch (e) {
            /* ignore */
        }
        try {
            return /(?:^|[?&])bus_debug=1(?:&|$)/i.test(location.search || "");
        } catch (e2) {
            return false;
        }
    }

    function busDisplayApiUrl(path) {
        var p = path || "/api/display/bus";
        if (!busDebugEnabled()) return p;
        var sep = p.indexOf("?") >= 0 ? "&" : "?";
        return p + sep + "debug=1";
    }

    function busLogPayload(data, source, extra) {
        if (!busDebugEnabled()) return;
        var tag = "[Bus diagnostic]";
        var src = source || "bus";
        if (!data) {
            console.warn(tag, src, "réponse vide");
            return;
        }
        if (data.diagnostics) {
            console.groupCollapsed(tag + " " + src + " — serveur (diagnostics)");
            console.log(data.diagnostics);
            if (data.diagnostics.settings && typeof console.table === "function") {
                console.table(data.diagnostics.settings);
            }
            if (data.diagnostics.h2_pipeline) {
                console.info(tag + " — pipeline H2", data.diagnostics.h2_pipeline);
            }
            if (data.diagnostics.dropped_routes_with_reason && data.diagnostics.dropped_routes_with_reason.length) {
                console.info(tag + " — routes perdues au bundle", data.diagnostics.dropped_routes_with_reason);
            }
            console.groupEnd();
        }
        var deps = data.departures || [];
        var routes = [];
        for (var i = 0; i < deps.length; i++) {
            var r = (deps[i] && deps[i].route_name) || "";
            if (r) routes.push(r);
        }
        var uniq = [];
        var seen = {};
        for (var j = 0; j < routes.length; j++) {
            if (!seen[routes[j]]) {
                seen[routes[j]] = true;
                uniq.push(routes[j]);
            }
        }
        var h2 = 0;
        for (var k = 0; k < deps.length; k++) {
            if (String((deps[k] && deps[k].route_name) || "").trim() === "H2") h2++;
        }
        console.info(tag + " " + src + " — résumé", {
            available: data.available,
            reason: data.reason,
            source: data.source,
            departures_count: deps.length,
            routes: uniq,
            h2_rows: h2,
        });
        if (extra && typeof extra === "object") {
            console.info(tag + " " + src + " — client", extra);
        }
    }

    G.busDebugEnabled = busDebugEnabled;
    G.busDisplayApiUrl = busDisplayApiUrl;
    G.busLogPayload = busLogPayload;
})(typeof window !== "undefined" ? window : globalThis);
