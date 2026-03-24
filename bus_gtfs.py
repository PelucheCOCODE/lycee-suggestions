"""
GTFS statique (réseau STRAN / Ycéo) — chargement, services actifs, prochains départs.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import threading
import time
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.request import Request, urlopen

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

log = logging.getLogger(__name__)

PARIS_TZ_NAME = "Europe/Paris"


def _paris_tz():
    if ZoneInfo:
        try:
            return ZoneInfo(PARIS_TZ_NAME)
        except Exception:
            pass
    return timezone.utc


def parse_gtfs_hms(s: str) -> tuple[int, int, int]:
    if not s or not isinstance(s, str):
        return (0, 0, 0)
    parts = s.strip().split(":")
    h = int(parts[0]) if len(parts) > 0 else 0
    m = int(parts[1]) if len(parts) > 1 else 0
    sec = int(parts[2]) if len(parts) > 2 else 0
    return (h, m, sec)


def hms_to_seconds(h: int, m: int, sec: int) -> int:
    return h * 3600 + m * 60 + sec


def service_date_to_departure_dt(service_day: date, dep_str: str) -> datetime:
    """Convertit stop_times departure_time (peut être >= 24h) en datetime absolu."""
    h, m, s = parse_gtfs_hms(dep_str)
    total_sec = hms_to_seconds(h, m, s)
    extra_days = total_sec // 86400
    rem = total_sec % 86400
    tz = _paris_tz()
    base = datetime.combine(service_day, datetime.min.time(), tzinfo=tz) + timedelta(days=extra_days, seconds=rem)
    return base


@dataclass
class GtfsData:
    routes: dict[str, dict] = field(default_factory=dict)
    trips: dict[str, dict] = field(default_factory=dict)
    stops: dict[str, dict] = field(default_factory=dict)
    stop_times_by_stop: dict[str, list[dict]] = field(default_factory=dict)
    calendar: list[dict] = field(default_factory=list)
    calendar_dates: list[dict] = field(default_factory=list)
    parent_to_children: dict[str, list[str]] = field(default_factory=dict)
    all_service_ids: set[str] = field(default_factory=set)
    source_path: str = ""


_gtfs_lock = threading.Lock()
_gtfs_data: GtfsData | None = None
_warned_missing_stops: set[str] = set()


def _read_csv_from_zip(z: zipfile.ZipFile, name: str) -> list[dict]:
    try:
        with z.open(name) as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            return list(reader)
    except KeyError:
        return []


def _parse_gtfs_zip_bytes(data: bytes) -> GtfsData | None:
    if not data or len(data) < 100:
        return None
    try:
        z = zipfile.ZipFile(io.BytesIO(data), "r")
    except zipfile.BadZipFile:
        return None
    stops_rows = _read_csv_from_zip(z, "stops.txt")
    routes_rows = _read_csv_from_zip(z, "routes.txt")
    trips_rows = _read_csv_from_zip(z, "trips.txt")
    stop_times_rows = _read_csv_from_zip(z, "stop_times.txt")
    calendar_rows = _read_csv_from_zip(z, "calendar.txt")
    calendar_dates_rows = _read_csv_from_zip(z, "calendar_dates.txt")
    if not stop_times_rows or not trips_rows:
        log.warning("GTFS: stop_times ou trips vide")
        return None

    g = GtfsData()
    g.calendar = calendar_rows
    g.calendar_dates = calendar_dates_rows
    for r in routes_rows:
        rid = (r.get("route_id") or "").strip()
        if rid:
            g.routes[rid] = r
    all_sid = set()
    for t in trips_rows:
        tid = (t.get("trip_id") or "").strip()
        if not tid:
            continue
        sid = (t.get("service_id") or "").strip()
        if sid:
            all_sid.add(sid)
        g.trips[tid] = t
    g.all_service_ids = all_sid

    for s in stops_rows:
        sid = (s.get("stop_id") or "").strip()
        if sid:
            g.stops[sid] = s
    for sid, s in list(g.stops.items()):
        par = (s.get("parent_station") or "").strip()
        if par:
            g.parent_to_children.setdefault(par, []).append(sid)

    for st in stop_times_rows:
        sid = (st.get("stop_id") or "").strip()
        if not sid:
            continue
        g.stop_times_by_stop.setdefault(sid, []).append(st)

    return g


# IDs logiques (admin / JSON) → quais GTFS STRAN.
# Cité Scolaire (H1/H2, etc.) : 21718 / 21719 — affichage forcé « Cité Scolaire », pas le libellé GTFS « Cité Sanitaire ».
# Tranchée (ligne 7, etc.) : 21581 / 21582.
CONFIG_STOP_ALIASES: dict[str, list[str]] = {
    "CSC01": ["21718"],
    "CSC02": ["21719"],
    "TRA01": ["21581"],
    "TRA02": ["21582"],
}


def expand_config_stop_ids(gtfs: GtfsData, raw_ids: list[str]) -> list[str]:
    """Alias config → stop_id réel ; parent station → quais enfants ; sinon garde l’id."""
    out: list[str] = []
    seen: set[str] = set()
    for rid in raw_ids:
        r = str(rid).strip()
        if not r:
            continue
        if r in CONFIG_STOP_ALIASES:
            for alt in CONFIG_STOP_ALIASES[r]:
                if alt not in seen:
                    seen.add(alt)
                    out.append(alt)
            continue
        if r in gtfs.parent_to_children:
            for c in gtfs.parent_to_children[r]:
                if c not in seen:
                    seen.add(c)
                    out.append(c)
        else:
            if r not in seen:
                seen.add(r)
                out.append(r)
    return out


def get_active_service_ids(gtfs: GtfsData, d: date) -> set[str]:
    """Services actifs pour le jour calendaire d (Europe/Paris)."""
    ds = d.strftime("%Y%m%d")
    weekday_cols = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    dow = d.weekday()  # lundi=0
    col = weekday_cols[dow]
    active: set[str] = set()

    if not gtfs.calendar:
        active = set(gtfs.all_service_ids)
    else:
        for c in gtfs.calendar:
            sid = (c.get("service_id") or "").strip()
            if not sid:
                continue
            try:
                start_d = datetime.strptime((c.get("start_date") or "").strip(), "%Y%m%d").date()
                end_d = datetime.strptime((c.get("end_date") or "").strip(), "%Y%m%d").date()
            except ValueError:
                continue
            if d < start_d or d > end_d:
                continue
            if (c.get(col) or "0") == "1":
                active.add(sid)

    for cd in gtfs.calendar_dates:
        sid = (cd.get("service_id") or "").strip()
        dt_s = (cd.get("date") or "").strip()
        ex = str(cd.get("exception_type") or "1").strip()
        if not sid or not dt_s or dt_s != ds:
            continue
        if ex == "1":
            active.add(sid)
        elif ex == "2":
            active.discard(sid)

    if not active and gtfs.calendar:
        active = set(gtfs.all_service_ids)

    return active


# STRAN-merge : la H2 (route_id 73) a souvent short_name « 2 » sans « H2 » dans routes.txt — on force le libellé panneau.
# Ne pas y mettre H1 sauf si le même problème est vérifié sur routes.txt (sinon risque de masquer H1).
STRAN_MERGE_ROUTE_ID_TO_PANEL: dict[str, str] = {
    "73": "H2",
}


def _canonical_route_label(short: str, long: str, route_id: str) -> str:
    """
    Libellé stable pour le panneau : H1/H2/H3, et 4 / 7 (sans préfixe H pour les numériques).
    Certains GTFS utilisent « 02 », « Ligne H2 », etc. — on déduit H2 depuis le libellé long si besoin.
    """
    rid = (route_id or "").strip()
    if rid in STRAN_MERGE_ROUTE_ID_TO_PANEL:
        return STRAN_MERGE_ROUTE_ID_TO_PANEL[rid]

    s = (short or "").strip()
    ln = (long or "").strip()
    blob = f"{s} {ln}"

    if re.search(r"(?i)hélyce\s*2|helyce\s*2|ligne\s*hélyce\s*2", blob):
        return "H2"

    for src in (s, ln, blob):
        m = re.search(r"\b(H\d{1,2})\b", src, re.I)
        if m:
            hn = m.group(1).upper()
            if hn in ("H4", "H04"):
                return "4"
            if hn in ("H7", "H07"):
                return "7"
            return hn

    s_clean = re.sub(r"(?i)^ligne\s+", "", s).strip()
    if s_clean in ("4", "04"):
        return "4"
    if s_clean in ("7", "07"):
        return "7"
    if s_clean.upper() in ("H4", "H04"):
        return "4"
    if s_clean.upper() in ("H7", "H07"):
        return "7"

    if s_clean.upper() == "H2" or s_clean == "02":
        return "H2"
    if s_clean == "2" and re.search(r"(?i)\bH2\b", blob):
        return "H2"

    # Ne pas confondre la ligne « 2 » (autre parcours) avec H2 : éviter « Saint-Nazaire » seul.
    if re.match(r"^0?2$", s_clean) and re.search(
        r"(?i)(\bH2\b|ligne\s*h?2\b|trignac|pornichet|chanzy|plage\s+des\s+jaunais|les\s+forges)",
        blob,
    ):
        return "H2"

    return s_clean or (route_id or "?").strip() or "?"


# La ligne « 2 » Herbignac… est une autre route_id (pas le 73).


def _route_short(gtfs: GtfsData, route_id: str) -> str:
    r = gtfs.routes.get(route_id) or {}
    short = (r.get("route_short_name") or "").strip()
    long = (r.get("route_long_name") or "").strip()
    if not short and not long:
        return _canonical_route_label("", "", route_id)
    return _canonical_route_label(short, long, route_id)


def _normalize_stop_display_name(name: str) -> str:
    if not name or not isinstance(name, str):
        return str(name or "")
    return name.strip()


def _cite_scolaire_display_label_for_config(config_stop_id: str) -> str | None:
    """Quais Cité Scolaire (codes admin CSC* ou stop_id GTFS 21718 / 21719)."""
    c = (config_stop_id or "").strip().upper()
    if c in ("CSC01", "21718"):
        return "Cité Scolaire (sens 1)"
    if c in ("CSC02", "21719"):
        return "Cité Scolaire (sens 2)"
    return None


def _fix_stop_name_h1_h2(route_name: str, config_stop_id: str, raw_stop_name: str) -> str:
    """
    H1 / H2 : l’arrêt à valoriser est la Cité Scolaire, pas le libellé GTFS « Cité Sanitaire »
    (même quai physique selon le réseau).
    """
    rn = (route_name or "").strip()
    if rn not in ("H1", "H2"):
        return _normalize_stop_display_name(raw_stop_name)
    cn = _cite_scolaire_display_label_for_config(config_stop_id)
    if cn is not None:
        return cn
    sn = _normalize_stop_display_name(raw_stop_name)
    if re.search(r"(?i)cité\s*sanitaire", sn):
        sn = re.sub(r"(?i)cité\s*sanitaire", "Cité Scolaire", sn)
    return sn.strip()


def _format_delay_label(delay_min: int, dep_dt: datetime | None, imminent_max: int) -> str:
    """Libellé affichage : IMMINENT, X min, +1h, +1h20, +3h05 (horizon large possible)."""
    if delay_min < 0:
        return "—"
    if delay_min <= imminent_max:
        return "IMMINENT"
    if delay_min < 60:
        return f"{delay_min} min"
    hours = delay_min // 60
    rem = delay_min % 60
    if rem == 0:
        return f"+{hours}h"
    if hours == 1:
        return f"+1h{rem}"
    return f"+{hours}h{rem:02d}"


def _urgency_from_delay(
    delay_min: int,
    imminent_max: int,
    soon_max: int,
    near_max: int,
) -> str:
    """imminent / soon / near / normal / far — premier passage."""
    if delay_min <= imminent_max:
        return "imminent"
    if delay_min <= soon_max:
        return "soon"
    if delay_min <= near_max:
        return "near"
    if delay_min >= 60:
        return "far"
    return "normal"


def _aggregate_pipeline_debug(
    raw: list[dict],
    deduped: list[dict],
    ordered_req: list[str],
    gtfs: GtfsData,
    active_today: set,
    horizon_minutes: int,
) -> dict:
    """Résumé diagnostic (H2, ligne 7, etc.) — basé sur l’index stop_times + trips."""
    expanded = expand_config_stop_ids(gtfs, ordered_req)
    stops_with_times = [s for s in expanded if s in gtfs.stop_times_by_stop]
    lines_out: dict[str, Any] = {}
    for line in ("H2", "7", "H1", "H3"):
        raws = [x for x in raw if (x.get("_route") or "").strip() == line]
        by_did: dict[str, int] = defaultdict(int)
        for x in raws:
            did = x.get("direction_id")
            if did is not None:
                by_did[f"direction_id={did}"] += 1
            else:
                by_did["headsign_only"] += 1
        lines_out[line] = {
            "raw_rows_in_horizon": len(raws),
            "direction_id_counts": dict(by_did),
        }
    return {
        "horizon_minutes": horizon_minutes,
        "active_services_today_count": len(active_today),
        "deduped_rows": len(deduped),
        "expanded_stop_ids": expanded,
        "stop_ids_with_stop_times": stops_with_times,
        "lines": lines_out,
    }


def compute_next_departures(
    gtfs: GtfsData,
    stop_ids: list[str],
    now_dt: datetime,
    horizon_minutes: int = 45,
    eta_tiers: dict[str, int] | None = None,
    pipeline_debug: bool = False,
) -> tuple[list[dict], bool, dict | None]:
    """
    Retourne (departures, no_service_today, debug_optionnel).
    Source des horaires : **stop_times** GTFS (departure_time / arrival_time) pour chaque arrêt demandé,
    filtré par **calendar** + **service_id** du trip — pas d’invention hors de cet index.

    Chaque élément regroupe **ligne + direction + arrêt configuré** avec jusqu’à **2** prochains passages.
    no_service_today=True si aucun service actif ce jour (calendrier vide après filtre).

    eta_tiers: imminent_max, soon_max, near_max (minutes, seuils inclus pour l’état visuel).
    pipeline_debug=True → 3ᵉ élément = dict diagnostic (pour /api/debug/bus).
    """
    tiers = eta_tiers or {"imminent_max": 1, "soon_max": 3, "near_max": 7}
    imminent_max = int(tiers.get("imminent_max", 1))
    soon_max = int(tiers.get("soon_max", 3))
    near_max = int(tiers.get("near_max", 7))

    if now_dt.tzinfo is None:
        tz = _paris_tz()
        now_dt = now_dt.replace(tzinfo=tz)
    else:
        now_dt = now_dt.astimezone(_paris_tz())

    today = now_dt.date()
    active_today = get_active_service_ids(gtfs, today)

    if not gtfs.calendar and not active_today:
        active_today = set(gtfs.all_service_ids)

    if not active_today:
        # Aucun service actif aujourd’hui (calendrier + exceptions) alors qu’il existe des services dans le réseau
        return ([], bool(gtfs.all_service_ids), None)

    seen_req: set[str] = set()
    ordered_req: list[str] = []
    for rid in stop_ids:
        r = str(rid).strip()
        if not r or r in seen_req:
            continue
        seen_req.add(r)
        ordered_req.append(r)

    for sid in expand_config_stop_ids(gtfs, ordered_req):
        if sid not in gtfs.stops and sid not in gtfs.stop_times_by_stop:
            if sid not in _warned_missing_stops:
                _warned_missing_stops.add(sid)
                log.warning("GTFS: stop_id inconnu ignoré: %s", sid)

    raw: list[dict] = []
    tomorrow = today + timedelta(days=1)
    active_tomorrow = get_active_service_ids(gtfs, tomorrow)

    # Un passage par (ligne, direction, arrêt demandé dans la config) — pas une fusion globale par ligne.
    for config_stop_id in ordered_req:
        expanded = expand_config_stop_ids(gtfs, [config_stop_id])
        for cfg_sid in expanded:
            if cfg_sid not in gtfs.stop_times_by_stop:
                continue
            for st in gtfs.stop_times_by_stop[cfg_sid]:
                trip_id = (st.get("trip_id") or "").strip()
                dep_s = (st.get("departure_time") or st.get("arrival_time") or "").strip()
                if not trip_id or not dep_s:
                    continue
                trip = gtfs.trips.get(trip_id)
                if not trip:
                    continue
                service_id = (trip.get("service_id") or "").strip()
                route_id = (trip.get("route_id") or "").strip()
                headsign = (trip.get("trip_headsign") or "").strip() or "—"
                route_name = _route_short(gtfs, route_id)
                did_raw = trip.get("direction_id")
                direction_id = None
                if did_raw is not None and str(did_raw).strip() != "":
                    try:
                        direction_id = int(did_raw)
                    except (ValueError, TypeError):
                        direction_id = None
                if direction_id is not None:
                    dir_key = f"did:{direction_id}"
                else:
                    dir_key = (headsign or "").strip() or "—"

                for svc_day, act in ((today, active_today), (tomorrow, active_tomorrow)):
                    if service_id not in act:
                        continue
                    dep_dt = service_date_to_departure_dt(svc_day, dep_s)
                    if dep_dt < now_dt:
                        continue
                    delay_min = int((dep_dt - now_dt).total_seconds() // 60)
                    if delay_min > horizon_minutes:
                        continue
                    if delay_min < 0:
                        continue
                    stop_name = (gtfs.stops.get(cfg_sid) or {}).get("stop_name") or cfg_sid
                    stop_name = stop_name.strip() if isinstance(stop_name, str) else str(stop_name)
                    stop_name = _fix_stop_name_h1_h2(route_name, config_stop_id, stop_name)
                    raw.append(
                        {
                            "route_name": route_name,
                            "direction": headsign,
                            "direction_id": direction_id,
                            "stop_name": stop_name,
                            "config_stop_id": config_stop_id,
                            "delay_minutes": delay_min,
                            "dep_dt": dep_dt,
                            "_sort": delay_min,
                            "_route": route_name,
                            "_dir": headsign,
                            "_dir_key": dir_key,
                        }
                    )

    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for item in raw:
        key = (item["_route"], item["_dir_key"], item["config_stop_id"])
        groups[key].append(item)

    # Trier les groupes par le prochain passage le plus tôt (évite de couper H2 / lignes tardives
    # à cause d’un ordre d’insertion arbitraire — ancien plafond 256 supprimé).
    group_items: list[tuple[tuple[str, str, str], list[dict]]] = []
    for key, items in groups.items():
        if not items:
            continue
        items.sort(key=lambda x: x["_sort"])
        group_items.append((key, items))
    group_items.sort(key=lambda kv: min(x["_sort"] for x in kv[1]))

    deduped: list[dict] = []
    for _key, items in group_items:
        take = items[:2]
        if not take:
            continue
        a = take[0]
        b = take[1] if len(take) > 1 else None
        dm1 = int(a["_sort"])
        dm2 = int(b["_sort"]) if b else None
        dep_dt1 = a.get("dep_dt")
        dep_dt2 = b.get("dep_dt") if b else None
        labels = [_format_delay_label(dm1, dep_dt1, imminent_max)]
        if dm2 is not None:
            labels.append(_format_delay_label(dm2, dep_dt2, imminent_max))
        times_minutes = [dm1, dm2] if dm2 is not None else [dm1]
        urgency = _urgency_from_delay(dm1, imminent_max, soon_max, near_max)
        urgency2 = (
            _urgency_from_delay(dm2, imminent_max, soon_max, near_max) if dm2 is not None else None
        )
        is_imminent = urgency == "imminent"
        lab1 = labels[0]
        lab2 = labels[1] if len(labels) > 1 else None
        row = {
            "route_name": a["route_name"],
            "direction": a["direction"],
            "direction_id": a.get("direction_id"),
            "stop_name": a["stop_name"],
            "config_stop_id": a["config_stop_id"],
            "delay_minutes": dm1,
            "delay_minutes_2": dm2,
            "times_minutes": times_minutes,
            "labels": labels,
            "label": " · ".join(labels),
            "is_imminent": is_imminent,
            "urgency": urgency,
            "primary": {
                "minutes": dm1,
                "label": lab1,
                "urgency": urgency,
                "is_imminent": is_imminent,
            },
            "secondary": (
                {
                    "minutes": dm2,
                    "label": lab2,
                    "urgency": urgency2,
                    "is_imminent": urgency2 == "imminent",
                }
                if dm2 is not None and lab2 is not None
                else None
            ),
        }
        deduped.append(row)

    dbg: dict | None = None
    if pipeline_debug or os.environ.get("BUS_GTFS_PIPELINE_DEBUG") == "1":
        dbg = _aggregate_pipeline_debug(
            raw, deduped, ordered_req, gtfs, active_today, horizon_minutes
        )
        if os.environ.get("BUS_GTFS_PIPELINE_DEBUG") == "1":
            log.info("[BUS DEBUG] %s", json.dumps(dbg, ensure_ascii=False, default=str))

    return (deduped, False, dbg if pipeline_debug else None)


def load_gtfs(gtfs_url_or_path: str, cache_dir: str, max_age_days: int = 7) -> GtfsData | None:
    """
    Charge le GTFS depuis une URL (cache disque, max_age_days) ou un chemin local (zip ou répertoire).
    """
    global _gtfs_data
    with _gtfs_lock:
        path = gtfs_url_or_path.strip()
        if not path:
            return None
        data: bytes | None = None
        if os.path.isdir(path):
            # dossier décompressé — lire via zip pas supporté; exiger zip
            log.warning("GTFS: dossier local non supporté, utilisez un fichier .zip")
            return None
        if os.path.isfile(path):
            with open(path, "rb") as f:
                data = f.read()
        else:
            os.makedirs(cache_dir, mode=0o755, exist_ok=True)
            cache_zip = os.path.join(cache_dir, "gtfs_cache.zip")
            meta_path = os.path.join(cache_dir, "gtfs_meta.json")
            fetch = True
            if os.path.isfile(cache_zip):
                age_days = (time.time() - os.path.getmtime(cache_zip)) / 86400.0
                if age_days < max_age_days:
                    fetch = False
            if fetch:
                try:
                    req = Request(path, headers={"Accept": "application/zip, application/octet-stream, */*"})
                    with urlopen(req, timeout=60) as resp:
                        data = resp.read()
                    if data and len(data) > 100:
                        with open(cache_zip, "wb") as f:
                            f.write(data)
                        with open(meta_path, "w", encoding="utf-8") as f:
                            json.dump({"url": path, "fetched_at": time.time()}, f)
                except Exception as e:
                    log.warning("GTFS: téléchargement échoué: %s", e)
                    if os.path.isfile(cache_zip):
                        with open(cache_zip, "rb") as f:
                            data = f.read()
            elif os.path.isfile(cache_zip):
                with open(cache_zip, "rb") as f:
                    data = f.read()

        if not data:
            return None
        g = _parse_gtfs_zip_bytes(data)
        if g:
            g.source_path = path[:120]
            _gtfs_data = g
            log.info("GTFS chargé: %s arrêts, %s trips", len(g.stops), len(g.trips))
        return g


def reset_gtfs_data() -> None:
    global _gtfs_data
    _gtfs_data = None


def ensure_gtfs_loaded(
    url: str,
    cache_dir: str,
    refresh_days: int,
) -> GtfsData | None:
    if _gtfs_data is not None:
        return _gtfs_data
    return load_gtfs(url, cache_dir, max_age_days=refresh_days)
