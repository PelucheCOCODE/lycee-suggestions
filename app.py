import os
import re
import csv
import io
import json
import zipfile
import hmac
import time
import uuid
import threading
from datetime import datetime, timedelta, timezone, date
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore
from functools import wraps

from sqlalchemy.exc import IntegrityError

from urllib.parse import unquote, urlparse

from flask import (
    Flask, render_template, request, jsonify, session, redirect, url_for,
    send_from_directory, send_file, Response, has_request_context, abort,
)

from sqlalchemy import func

from models import (
    db, Suggestion, Vote, SuggestionArgument, Location, Placement, CalibrationExample, CalibrationDebat, CalibrationDetails, CalibrationRapport, CalibrationVerification, Announcement,
    OfficialProposal, ProposalVote, ProposalArgument, CvlOfficialInfo, SchoolContext, SiteSettings, Presentation, Slide, DisplayPage, MediaFile, ScrapedNews,
    ActivityLog, TraceFeedback, Backup, SuggestionArchive,
    SuggestionImportance, DailySessionActivity, DailyPresence, EngagementCardDone, EngagementGuess, CommunityMessage, DailyMood,
    Dilemma, DilemmaVote,
    MusicPoll, MusicTrack, MusicVote,
)
import music_utils
from ai_engine import AIEngine
from content_filter import filter_content, filter_content_quick
import bus_gtfs

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "lycee-suggestions-secret-key-2026")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///suggestions.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# Cookie de session (élève / visitor_id)
# CRITIQUE HTTP : Secure=True → le navigateur n’envoie PAS le cookie sur http:// (IP publique sans TLS).
# Ne pas dériver Secure depuis HTTPS=1 (souvent posé par nginx vers WSGI alors que le client est en HTTP).
# Défaut : Secure désactivé. En HTTPS réel (domaine + certificat) : SESSION_COOKIE_SECURE=true
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() in (
    "1",
    "true",
    "yes",
)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=365)
app.config["SESSION_COOKIE_NAME"] = "lycee_session"
# Session anonyme persistante (cookie) — aligné avec get_session_id() qui pose session.permanent = True
app.config["SESSION_PERMANENT"] = True
app.config["MINIFY"] = os.environ.get("MINIFY", "true").lower() in ("1", "true", "yes")
app.config["SPOTIFY_CLIENT_ID"] = os.environ.get("SPOTIFY_CLIENT_ID", "")
app.config["SPOTIFY_CLIENT_SECRET"] = os.environ.get("SPOTIFY_CLIENT_SECRET", "")


@app.before_request
def _ensure_anonymous_session_permanent():
    """Toute requête : cookie de session prolongé (même avant le premier accès à visitor_id)."""
    session.permanent = True

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "cvl2026")
# « true » = accès admin sans mot de passe (réactiver : ADMIN_PASSWORD_DISABLED=false)
ADMIN_PASSWORD_DISABLED = os.environ.get("ADMIN_PASSWORD_DISABLED", "true").lower() in ("1", "true", "yes")

# Rate limiting login (IP -> list of timestamps)
_login_attempts: dict[str, list[float]] = {}
_LOGIN_RATE_WINDOW = 300  # 5 min
_LOGIN_MAX_ATTEMPTS = 5

# Rate limiting par IP (anti-spam / flood)
_ip_rate_buckets: dict[str, list[float]] = {}
_vote_burst_buckets: dict[str, list[float]] = {}
_vote_sid_locks: dict[int, threading.Lock] = {}
_vote_locks_guard = threading.Lock()
_IP_SUBMIT_PER_HOUR = 24
_IP_UNDERSTAND_PER_HOUR = 48
_IP_VOTE_PER_MINUTE = 72
# Rafales sur la même suggestion + même session (anti double-envoi / spam gestuel)
_VOTE_BURST_PER_SID_WINDOW_SEC = 15
_VOTE_BURST_PER_SID_MAX = 4
_IP_ARG_PER_MINUTE = 36
_IP_SESSION_RESTORE_PER_MINUTE = 12

# Troll detection : blocage temporaire si trop de rejets
_troll_rejections: dict[str, list[float]] = {}  # ip ou visitor_id -> timestamps
_TROLL_WINDOW = 300  # 5 min
_TROLL_MAX_REJECTIONS = 8
_troll_blocked_until: dict[str, float] = {}  # ip/visitor -> unblock timestamp
_TROLL_BLOCK_DURATION = 900  # 15 min

# Suggestion « Terminée » : visible élèves / display TV pendant 2 h après completed_at, puis calibration IA.
TERMINATED_DISPLAY_HOURS = 2
TERMINATED_CALIBRATION_HOURS = 2


def _utc_dt(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _terminée_still_visible(s: Suggestion) -> bool:
    """False si Terminée et fenêtre d'affichage 2 h écoulée."""
    if s.status != "Terminée" or not s.completed_at:
        return True
    ca = _utc_dt(s.completed_at)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=TERMINATED_DISPLAY_HOURS)
    return ca > cutoff


def _strip_proposal_html(html: str) -> str:
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup
        return (BeautifulSoup(html, "html.parser").get_text() or "").strip()[:8000]
    except Exception:
        return html[:8000]


def _sync_suggestion_archive(s: Suggestion):
    """Upsert archive pour historique / suggestions supprimées."""
    row = SuggestionArchive.query.filter_by(suggestion_id=s.id).first()
    if not row:
        row = SuggestionArchive(suggestion_id=s.id)
        db.session.add(row)
    row.title = s.title or ""
    row.original_text = s.original_text or ""
    row.category = s.category or ""
    row.status = s.status or ""
    row.reject_reason = getattr(s, "reject_reason", None) or ""
    row.vote_count = s.vote_count or 0
    row.needs_debate = bool(getattr(s, "needs_debate", False))
    row.created_at = s.created_at
    row.completed_at = s.completed_at


def _troll_check(identifier: str) -> bool:
    """True si l'appareil est bloqué (troll)."""
    now = time.time()
    if identifier in _troll_blocked_until and now < _troll_blocked_until[identifier]:
        return True
    if identifier in _troll_blocked_until and now >= _troll_blocked_until[identifier]:
        del _troll_blocked_until[identifier]
    return False


def _troll_record_rejection(identifier: str):
    """Enregistre un rejet. Bloque si trop de rejets."""
    now = time.time()
    if identifier not in _troll_rejections:
        _troll_rejections[identifier] = []
    _troll_rejections[identifier].append(now)
    _troll_rejections[identifier] = [t for t in _troll_rejections[identifier] if now - t < _TROLL_WINDOW]
    if len(_troll_rejections[identifier]) >= _TROLL_MAX_REJECTIONS:
        _troll_blocked_until[identifier] = now + _TROLL_BLOCK_DURATION
        _log_activity("troll_blocked", f"Appareil bloqué temporairement (trop de rejets)", detail=identifier)


def _log_activity(event_type: str, message: str, detail: str = "", ip: str = "", visitor_id: str = ""):
    """Enregistre un événement dans les logs d'activité."""
    try:
        if not ip and has_request_context():
            ip = request.remote_addr or ""
        if not visitor_id and has_request_context():
            visitor_id = session.get("visitor_id", "")
        log = ActivityLog(event_type=event_type, message=message[:500], detail=detail[:2000], ip=ip, visitor_id=visitor_id)
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()


def _login_rate_limit() -> bool:
    """Return True if rate limit exceeded."""
    ip = request.remote_addr or "unknown"
    now = time.time()
    if ip not in _login_attempts:
        _login_attempts[ip] = []
    attempts = _login_attempts[ip]
    attempts[:] = [t for t in attempts if now - t < _LOGIN_RATE_WINDOW]
    if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
        return True
    attempts.append(now)
    return False


UPLOAD_FOLDER = os.path.join(app.static_folder, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "svg", "mp4", "webm", "ogg"}
MAX_UPLOAD_SIZE = 50 * 1024 * 1024


def _allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


db.init_app(app)
ai = AIEngine()


@app.context_processor
def _minify_assets():
    def _asset(path: str) -> str:
        if app.config.get("MINIFY") and path:
            if path.endswith(".js"):
                p = path.replace(".js", ".min.js")
                if os.path.exists(os.path.join(app.static_folder, p)):
                    return p
            if path.endswith(".css"):
                p = path.replace(".css", ".min.css")
                if os.path.exists(os.path.join(app.static_folder, p)):
                    return p
        return path
    return {"asset": _asset}


@app.after_request
def _security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if app.config.get("MINIFY") and response.content_type and "text/html" in response.content_type:
        try:
            import re as _re
            data = response.get_data(as_text=True)
            data = _re.sub(r">\s+<", "><", data)
            data = _re.sub(r"\s{2,}", " ", data)
            response.set_data(data)
        except Exception:
            pass
    return response


def _client_ip() -> str:
    """IP client (compatible proxy : X-Forwarded-For, X-Real-IP)."""
    h = request.headers.get("X-Forwarded-For") or request.headers.get("X-Real-IP")
    if h:
        return h.split(",")[0].strip()[:120]
    return (request.remote_addr or "unknown")[:120]


def _ip_rate_exceeded(ip: str, action: str, max_n: int, window_sec: float) -> bool:
    """True si la limite est dépassée pour cette IP + action."""
    key = f"{ip}\t{action}"
    now = time.time()
    if key not in _ip_rate_buckets:
        _ip_rate_buckets[key] = []
    arr = _ip_rate_buckets[key]
    arr[:] = [t for t in arr if now - t < window_sec]
    if len(arr) >= max_n:
        return True
    arr.append(now)
    return False


def _ip_rate_response():
    return jsonify({
        "error": "Trop de requêtes depuis cette adresse ou ce réseau. Patientez un peu avant de réessayer.",
    }), 429


def _lock_for_sid(sid: int) -> threading.Lock:
    """Verrou process-local par suggestion : sérialise les POST /vote (SQLite + workers multi-threads)."""
    with _vote_locks_guard:
        if sid not in _vote_sid_locks:
            _vote_sid_locks[sid] = threading.Lock()
        return _vote_sid_locks[sid]


def _server_ts_ms() -> int:
    return int(time.time() * 1000)


def _vote_burst_exceeded(session_id: str, sid: int) -> bool:
    """True si trop de requêtes vote sur cette suggestion pour cette session (fenêtre courte)."""
    key = f"{session_id}\tvote_sid\t{sid}"
    now = time.time()
    if key not in _vote_burst_buckets:
        _vote_burst_buckets[key] = []
    arr = _vote_burst_buckets[key]
    arr[:] = [t for t in arr if now - t < _VOTE_BURST_PER_SID_WINDOW_SEC]
    if len(arr) >= _VOTE_BURST_PER_SID_MAX:
        return True
    arr.append(now)
    return False


def _vote_burst_response(sid: int, session_id: str, needs_debate: bool):
    """429 avec état complet exploitable par le client (réconciliation)."""
    s = Suggestion.query.get(sid)
    if not s:
        return jsonify({"error": "Trop de requêtes. Réessaie plus tard.", "server_ts": _server_ts_ms()}), 429
    my = Vote.query.filter_by(suggestion_id=sid, session_id=session_id).first()
    if needs_debate:
        vf = Vote.query.filter_by(suggestion_id=sid, vote_type="for").count()
        va = Vote.query.filter_by(suggestion_id=sid, vote_type="against").count()
        vc = vf + va
    else:
        total = Vote.query.filter_by(suggestion_id=sid).count()
        vc, vf, va = total, total, 0
    d = {
        "error": "Trop de requêtes sur cette action. Réessaie dans quelques secondes.",
        "server_ts": _server_ts_ms(),
        "vote_count": vc,
        "vote_for": vf,
        "vote_against": va,
        "has_voted": my is not None,
        "my_vote": my.vote_type if my else None,
    }
    if needs_debate:
        d["arguments_for"] = [a.to_dict() for a in s.arguments if a.side == "for" and a.status == "approved"]
        d["arguments_against"] = [a.to_dict() for a in s.arguments if a.side == "against" and a.status == "approved"]
    return jsonify(d), 429


def get_session_id():
    if "visitor_id" not in session:
        session["visitor_id"] = str(uuid.uuid4())
        session.modified = True
    session.permanent = True
    return session["visitor_id"]


try:
    PARIS_TZ = ZoneInfo("Europe/Paris") if ZoneInfo else None
except Exception:
    PARIS_TZ = None
_HOT_IMPORTANCE_THRESHOLD = 70.0


def _paris_today_str() -> str:
    """Jour calendaire (Paris si tzdata dispo, sinon date locale du serveur)."""
    if PARIS_TZ is not None:
        try:
            return datetime.now(PARIS_TZ).date().isoformat()
        except Exception:
            pass
    return datetime.now().date().isoformat()


def _engagement_activity_points(row: DailySessionActivity) -> float:
    return float((row.like_count or 0) * 2 + (row.swipe_count or 0))


def _ensure_daily_presence(session_id: str) -> None:
    day = _paris_today_str()
    if not DailyPresence.query.filter_by(session_id=session_id, day=day).first():
        db.session.add(DailyPresence(session_id=session_id, day=day))
        db.session.commit()


def _get_or_create_activity(session_id: str, day: str) -> DailySessionActivity:
    row = DailySessionActivity.query.filter_by(session_id=session_id, day=day).first()
    if not row:
        row = DailySessionActivity(session_id=session_id, day=day)
        db.session.add(row)
        db.session.commit()
    return row


def _increment_daily_activity(session_id: str, kind: str) -> None:
    """kind: swipe | like"""
    day = _paris_today_str()
    _ensure_daily_presence(session_id)
    row = _get_or_create_activity(session_id, day)
    if kind == "swipe":
        row.swipe_count = (row.swipe_count or 0) + 1
    elif kind == "like":
        row.like_count = (row.like_count or 0) + 1
    db.session.commit()


def _recompute_suggestion_importance(suggestion_id: int) -> None:
    s = Suggestion.query.get(suggestion_id)
    if not s:
        return
    rows = SuggestionImportance.query.filter_by(suggestion_id=suggestion_id).all()
    if not rows:
        s.importance_score = 0.0
    else:
        avg = sum(r.level for r in rows) / len(rows)
        s.importance_score = (avg - 1.0) / 3.0 * 100.0
    db.session.commit()


def _suggestion_popularity_pct(sug: Suggestion) -> float:
    """Pourcentage réel pour le jeu « devine » : simple = likes / électeurs uniques ; débat = % pour."""
    if getattr(sug, "needs_debate", False):
        vf = sug.vote_for or 0
        va = sug.vote_against or 0
        tot = vf + va
        if tot <= 0:
            return 0.0
        return round(100.0 * vf / tot, 1)
    total_voters = db.session.query(func.count(func.distinct(Vote.session_id))).scalar() or 1
    vc = sug.vote_count or 0
    return round(min(100.0, 100.0 * vc / max(total_voters, 1)), 1)


def _popularity_bucket(pct: float) -> str:
    if pct < 30:
        return "lt30"
    if pct <= 60:
        return "mid"
    return "gt60"


def _dilemma_payload_for_session(session_id: str, day: str) -> dict | None:
    d = Dilemma.query.filter_by(scheduled_day=day).first()
    if not d:
        return None
    my = DilemmaVote.query.filter_by(dilemma_id=d.id, session_id=session_id).first()
    total_a = DilemmaVote.query.filter_by(dilemma_id=d.id, side="a").count()
    total_b = DilemmaVote.query.filter_by(dilemma_id=d.id, side="b").count()
    tot = total_a + total_b
    pct_a = round(100.0 * total_a / tot, 1) if tot else 0.0
    pct_b = round(100.0 * total_b / tot, 1) if tot else 0.0
    return {
        "id": d.id,
        "title": d.title,
        "option_a": d.option_a,
        "option_b": d.option_b,
        "my_side": my.side if my else None,
        "pct_a": pct_a,
        "pct_b": pct_b,
        "votes_total": tot,
    }


def _percentile_rank_today(session_id: str) -> tuple[int, int, float]:
    """(percentile 0-100, connected_today, my_score) — score = 2*likes + swipes."""
    day = _paris_today_str()
    connected = DailyPresence.query.filter_by(day=day).count()
    rows = DailySessionActivity.query.filter_by(day=day).all()
    if not rows:
        return 50, connected, 0.0
    row_me = DailySessionActivity.query.filter_by(session_id=session_id, day=day).first()
    my = _engagement_activity_points(row_me) if row_me else 0.0
    scores = sorted(_engagement_activity_points(r) for r in rows)
    n = len(scores)
    below = sum(1 for sc in scores if sc < my)
    pct = int(round(100.0 * below / max(n, 1)))
    return pct, connected, my


def _check_csrf_safe() -> bool:
    """Vérifie que la requête provient de notre origine (anti-CSRF)."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return True
    origin = request.headers.get("Origin")
    if origin is None:
        return True
    our_origin = request.url_root.rstrip("/")
    if origin == our_origin:
        return True
    try:
        from urllib.parse import urlparse

        o = urlparse(origin)
        u = urlparse(our_origin)

        def _host_norm(h):
            if not h:
                return ""
            hl = (h or "").lower()
            if hl in ("127.0.0.1", "localhost", "::1"):
                return "localhost"
            return hl

        port_o = o.port if o.port is not None else (443 if o.scheme == "https" else 80)
        port_u = u.port if u.port is not None else (443 if u.scheme == "https" else 80)
        if (
            o.scheme == u.scheme
            and _host_norm(o.hostname) == _host_norm(u.hostname)
            and port_o == port_u
        ):
            return True
    except Exception:
        pass
    return False


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if ADMIN_PASSWORD_DISABLED:
            session["is_admin"] = True
        if not session.get("is_admin"):
            return jsonify({"error": "Non autorisé"}), 401
        if not _check_csrf_safe():
            return jsonify({"error": "Requête non autorisée"}), 403
        return f(*args, **kwargs)
    return decorated


def get_setting(key: str, default: str | None = None) -> str:
    row = SiteSettings.query.get(key)
    if row:
        return row.value
    if default is not None:
        return default
    return SiteSettings.DEFAULTS.get(key, "")


def set_setting(key: str, value: str):
    row = SiteSettings.query.get(key)
    if row:
        row.value = value
    else:
        row = SiteSettings(key=key, value=value)
        db.session.add(row)
    db.session.commit()


def _split_argument_text(text: str) -> list[str]:
    """Split argument text into multiple parts (1. X 2. Y, or double newlines, or ' - ')."""
    import re
    text = text.strip()
    if not text or len(text) < 5:
        return []
    parts = []
    split = re.split(r"\n\s*\n", text)
    if len(split) > 1:
        for s in split:
            s = s.strip()
            if len(s) >= 5:
                parts.append(s)
        return parts if parts else [text]
    split = re.split(r"\s+-\s+", text)
    if len(split) > 1:
        for s in split:
            s = s.strip()
            if len(s) >= 5:
                parts.append(s)
        return parts if parts else [text]
    split = re.split(r"(?m)^\d+[\.\)]\s*", text)
    if len(split) > 1:
        for s in split:
            s = s.strip()
            if len(s) >= 5:
                parts.append(s)
        return parts if parts else [text]
    return [text]


def _location_extra_aliases(loc: Location) -> list[str]:
    """Synonymes connus (ex. CDI → lieu « Au cœur » si le nom contient cœur)."""
    out: list[str] = []
    n = (loc.name or "").lower()
    if "coeur" in n or "cœur" in n:
        out.extend(
            [
                "CDI",
                "cdi",
                "centre de documentation et d'information",
                "centre documentation",
                "documentation",
            ]
        )
    return out


def _get_locations_list():
    """Return locations with placements for AI detection."""
    result = []
    for loc in Location.query.all():
        names = [loc.name]
        for p in Placement.query.filter_by(location_id=loc.id).all():
            if p.name:
                names.append(p.name)
        for a in _location_extra_aliases(loc):
            if a and a not in names:
                names.append(a)
        result.append({"id": loc.id, "name": loc.name, "names": names})
    return result


def _get_existing_suggestions():
    result = []
    for s in Suggestion.query.all():
        result.append({
            "id": s.id,
            "title": s.title,
            "original_text": s.original_text,
            "keywords": s.keywords.split(",") if s.keywords else [],
            "category": s.category,
        })
    return result


# --------------- Pages ---------------

@app.route("/")
def student_page():
    return render_template("student.html")


@app.route("/display")
def display_page():
    return render_template("display.html")


@app.route("/displaybus")
def displaybus_page():
    """Écran dédié : uniquement les prochains bus (horloge + pagination si besoin)."""
    return render_template("displaybus.html")


@app.route("/admin")
def admin_page():
    if ADMIN_PASSWORD_DISABLED:
        session["is_admin"] = True
    if not session.get("is_admin"):
        return render_template("admin_login.html")
    return render_template("admin.html")


@app.route("/admin/login", methods=["POST"])
def admin_login():
    if ADMIN_PASSWORD_DISABLED:
        session["is_admin"] = True
        return redirect(url_for("admin_page"))
    if _login_rate_limit():
        return render_template("admin_login.html", error="Trop de tentatives. Réessayez dans 5 minutes."), 429
    if request.is_json or request.content_type and "application/json" in (request.content_type or ""):
        return render_template("admin_login.html", error="Utilisez le formulaire de connexion."), 400
    password = request.form.get("password", "")
    if not password:
        return render_template("admin_login.html", error="Mot de passe requis.")
    if not hmac.compare_digest(password, ADMIN_PASSWORD):
        return render_template("admin_login.html", error="Mot de passe incorrect")
    session.clear()
    session["visitor_id"] = str(uuid.uuid4())
    session["is_admin"] = True
    return redirect(url_for("admin_page"))


@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("student_page"))


# --------------- API Élèves ---------------

@app.route("/api/suggestions", methods=["GET"])
def list_suggestions():
    category = request.args.get("category")
    sort = request.args.get("sort", "votes")
    debate_only = request.args.get("debate") == "1"

    query = Suggestion.query.filter(
        Suggestion.status != "En attente",
        Suggestion.status != "Refusée",
    )
    if category and category != "Toutes":
        query = query.filter_by(category=category)
    if debate_only:
        query = query.filter(Suggestion.needs_debate == True)

    if sort == "recent":
        query = query.order_by(Suggestion.created_at.desc())
    else:
        query = query.order_by(Suggestion.vote_count.desc())

    suggestions = [s for s in query.all() if _terminée_still_visible(s)]
    session_id = get_session_id()

    ids = [s.id for s in suggestions]
    vote_totals = {}
    for_counts = {}
    against_counts = {}
    if ids:
        vote_totals = dict(
            db.session.query(Vote.suggestion_id, func.count(Vote.id))
            .filter(Vote.suggestion_id.in_(ids))
            .group_by(Vote.suggestion_id)
            .all()
        )
        for_counts = dict(
            db.session.query(Vote.suggestion_id, func.count(Vote.id))
            .filter(Vote.suggestion_id.in_(ids), Vote.vote_type == "for")
            .group_by(Vote.suggestion_id)
            .all()
        )
        against_counts = dict(
            db.session.query(Vote.suggestion_id, func.count(Vote.id))
            .filter(Vote.suggestion_id.in_(ids), Vote.vote_type == "against")
            .group_by(Vote.suggestion_id)
            .all()
        )

    result = []
    for s in suggestions:
        d = s.to_dict()
        if getattr(s, "needs_debate", False):
            vf = int(for_counts.get(s.id, 0))
            va = int(against_counts.get(s.id, 0))
            d["vote_for"] = vf
            d["vote_against"] = va
            d["vote_count"] = vf + va
        else:
            total = int(vote_totals.get(s.id, 0))
            d["vote_count"] = total
            d["vote_for"] = total
            d["vote_against"] = 0
        my_vote = Vote.query.filter_by(suggestion_id=s.id, session_id=session_id).first()
        d["has_voted"] = my_vote is not None
        d["my_vote"] = my_vote.vote_type if my_vote else None
        imp = float(d.get("importance_score") or 0)
        d["hot"] = imp >= _HOT_IMPORTANCE_THRESHOLD
        d["server_ts"] = int(s.updated_at.timestamp() * 1000) if s.updated_at else 0
        result.append(d)

    return jsonify(result)


@app.route("/api/engagement/bootstrap", methods=["GET"])
def engagement_bootstrap():
    session_id = get_session_id()
    day = _paris_today_str()
    _ensure_daily_presence(session_id)
    pct, connected, my_score = _percentile_rank_today(session_id)
    row = DailySessionActivity.query.filter_by(session_id=session_id, day=day).first()
    done = [x.card_type for x in EngagementCardDone.query.filter_by(session_id=session_id, day=day).all()]
    guessed_sids = {g.suggestion_id for g in EngagementGuess.query.filter_by(session_id=session_id).all()}
    query = Suggestion.query.filter(
        Suggestion.status != "En attente",
        Suggestion.status != "Refusée",
    )
    guess_eligible_ids = [s.id for s in query.all() if _terminée_still_visible(s) and s.id not in guessed_sids]
    mp = _music_poll_active_payload()
    return jsonify({
        "day": day,
        "connected_today": connected,
        "percentile_most_active": pct,
        "my_activity_score": my_score,
        "swipes_today": row.swipe_count if row else 0,
        "likes_today": row.like_count if row else 0,
        "cards_done_today": done,
        "guess_eligible_ids": guess_eligible_ids,
        "dilemma": _dilemma_payload_for_session(session_id, day),
        "music_polls": [mp] if mp else [],
    })


@app.route("/api/engagement/dilemma-vote", methods=["POST"])
def engagement_dilemma_vote():
    if not _check_csrf_safe():
        return jsonify({"error": "Requête non autorisée"}), 403
    ip = _client_ip()
    if _ip_rate_exceeded(ip, "dilemma_vote", 40, 60):
        return _ip_rate_response()
    data = request.get_json() or {}
    did = int(data.get("dilemma_id") or 0)
    side = (data.get("side") or "").strip().lower()
    if did <= 0 or side not in ("a", "b"):
        return jsonify({"error": "Paramètres invalides"}), 400
    day = _paris_today_str()
    d = Dilemma.query.get(did)
    if not d or d.scheduled_day != day:
        return jsonify({"error": "Dilemme introuvable pour aujourd'hui"}), 404
    session_id = get_session_id()
    if DilemmaVote.query.filter_by(dilemma_id=did, session_id=session_id).first():
        return jsonify({"error": "Tu as déjà voté"}), 409
    db.session.add(DilemmaVote(dilemma_id=did, session_id=session_id, side=side))
    db.session.commit()
    if not EngagementCardDone.query.filter_by(session_id=session_id, day=day, card_type="dilemma").first():
        db.session.add(EngagementCardDone(session_id=session_id, day=day, card_type="dilemma"))
        db.session.commit()
    return jsonify(_dilemma_payload_for_session(session_id, day))


@app.route("/api/engagement/dilemma-skip", methods=["POST"])
def engagement_dilemma_skip():
    if not _check_csrf_safe():
        return jsonify({"error": "Requête non autorisée"}), 403
    session_id = get_session_id()
    day = _paris_today_str()
    if not Dilemma.query.filter_by(scheduled_day=day).first():
        return jsonify({"error": "Pas de dilemme aujourd'hui"}), 404
    if not EngagementCardDone.query.filter_by(session_id=session_id, day=day, card_type="dilemma").first():
        db.session.add(EngagementCardDone(session_id=session_id, day=day, card_type="dilemma"))
        db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/engagement/ttt-dismiss", methods=["POST"])
def engagement_ttt_dismiss():
    if not _check_csrf_safe():
        return jsonify({"error": "Requête non autorisée"}), 403
    session_id = get_session_id()
    day = _paris_today_str()
    if not EngagementCardDone.query.filter_by(session_id=session_id, day=day, card_type="ttt").first():
        db.session.add(EngagementCardDone(session_id=session_id, day=day, card_type="ttt"))
        db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/engagement/ping", methods=["POST"])
def engagement_ping():
    if not _check_csrf_safe():
        return jsonify({"error": "Requête non autorisée"}), 403
    ip = _client_ip()
    if _ip_rate_exceeded(ip, "engagement_ping", 120, 60):
        return _ip_rate_response()
    data = request.get_json() or {}
    kind = (data.get("type") or "").strip()
    session_id = get_session_id()
    if kind == "presence":
        _ensure_daily_presence(session_id)
    elif kind == "swipe":
        _increment_daily_activity(session_id, "swipe")
    elif kind == "like":
        _increment_daily_activity(session_id, "like")
    else:
        return jsonify({"error": "type invalide"}), 400
    pct, connected, my_score = _percentile_rank_today(session_id)
    return jsonify({"ok": True, "connected_today": connected, "percentile_most_active": pct, "my_activity_score": my_score})


@app.route("/api/engagement/importance", methods=["POST"])
def engagement_importance():
    if not _check_csrf_safe():
        return jsonify({"error": "Requête non autorisée"}), 403
    ip = _client_ip()
    if _ip_rate_exceeded(ip, "engagement_importance", 40, 60):
        return _ip_rate_response()
    data = request.get_json() or {}
    sid = int(data.get("suggestion_id") or 0)
    level = int(data.get("level") or 0)
    if sid <= 0 or level < 1 or level > 4:
        return jsonify({"error": "Paramètres invalides"}), 400
    s = Suggestion.query.get(sid)
    if not s:
        return jsonify({"error": "Suggestion introuvable"}), 404
    session_id = get_session_id()
    ex = SuggestionImportance.query.filter_by(suggestion_id=sid, session_id=session_id).first()
    if ex:
        ex.level = level
    else:
        db.session.add(SuggestionImportance(suggestion_id=sid, session_id=session_id, level=level))
    db.session.commit()
    _recompute_suggestion_importance(sid)
    day = _paris_today_str()
    if not EngagementCardDone.query.filter_by(session_id=session_id, day=day, card_type="importance").first():
        db.session.add(EngagementCardDone(session_id=session_id, day=day, card_type="importance"))
        db.session.commit()
    s2 = Suggestion.query.get(sid)
    return jsonify({"ok": True, "importance_score": float(s2.importance_score or 0)})


@app.route("/api/engagement/guess", methods=["POST"])
def engagement_guess():
    if not _check_csrf_safe():
        return jsonify({"error": "Requête non autorisée"}), 403
    ip = _client_ip()
    if _ip_rate_exceeded(ip, "engagement_guess", 30, 60):
        return _ip_rate_response()
    data = request.get_json() or {}
    sid = int(data.get("suggestion_id") or 0)
    bucket = (data.get("bucket") or "").strip()
    if sid <= 0 or bucket not in ("lt30", "mid", "gt60"):
        return jsonify({"error": "Paramètres invalides"}), 400
    s = Suggestion.query.get(sid)
    if not s:
        return jsonify({"error": "Suggestion introuvable"}), 404
    session_id = get_session_id()
    if EngagementGuess.query.filter_by(suggestion_id=sid, session_id=session_id).first():
        return jsonify({"error": "Tu as déjà répondu pour cette idée"}), 409
    _update_suggestion_vote_counts(sid)
    s = Suggestion.query.get(sid)
    actual_pct = _suggestion_popularity_pct(s)
    actual_bucket = _popularity_bucket(actual_pct)
    correct = actual_bucket == bucket
    g = EngagementGuess(suggestion_id=sid, session_id=session_id, bucket=bucket)
    db.session.add(g)
    db.session.commit()
    day = _paris_today_str()
    if not EngagementCardDone.query.filter_by(session_id=session_id, day=day, card_type="guess").first():
        db.session.add(EngagementCardDone(session_id=session_id, day=day, card_type="guess"))
        db.session.commit()
    return jsonify({
        "actual_pct": actual_pct,
        "actual_bucket": actual_bucket,
        "correct": correct,
        "your_bucket": bucket,
    })


@app.route("/api/engagement/mood", methods=["POST"])
def engagement_mood():
    if not _check_csrf_safe():
        return jsonify({"error": "Requête non autorisée"}), 403
    data = request.get_json() or {}
    mood = (data.get("mood") or "").strip()
    if mood not in ("bien", "bof", "fatigue", "stresse"):
        return jsonify({"error": "Humeur invalide"}), 400
    session_id = get_session_id()
    day = _paris_today_str()
    ex = DailyMood.query.filter_by(session_id=session_id, day=day).first()
    if ex:
        ex.mood = mood
    else:
        db.session.add(DailyMood(session_id=session_id, day=day, mood=mood))
    db.session.commit()
    if not EngagementCardDone.query.filter_by(session_id=session_id, day=day, card_type="mood").first():
        db.session.add(EngagementCardDone(session_id=session_id, day=day, card_type="mood"))
        db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/engagement/message", methods=["POST"])
def engagement_message():
    if not _check_csrf_safe():
        return jsonify({"error": "Requête non autorisée"}), 403
    ip = _client_ip()
    if _ip_rate_exceeded(ip, "engagement_msg", 12, 60):
        return _ip_rate_response()
    data = request.get_json() or {}
    display_name = (data.get("display_name") or "").strip()[:80]
    body = (data.get("message") or "").strip()[:500]
    if len(display_name) < 1 or len(body) < 3:
        return jsonify({"error": "Pseudo et message requis (3 caractères min. pour le message)."}), 400
    from content_filter import filter_community_message_quick
    ok, msg = filter_community_message_quick(body)
    if not ok:
        return jsonify({"error": msg}), 400
    ok2, msg2 = filter_community_message_quick(display_name)
    if not ok2:
        return jsonify({"error": "Pseudo inapproprié."}), 400
    try:
        from llm_engine import moderate_community_message_llm
        ok_llm, reason = moderate_community_message_llm(body)
        if not ok_llm:
            return jsonify({"error": reason or "Message refusé par la modération."}), 400
    except Exception:
        pass
    session_id = get_session_id()
    db.session.add(CommunityMessage(session_id=session_id, display_name=display_name, body=body, status="approved"))
    db.session.commit()
    day = _paris_today_str()
    if not EngagementCardDone.query.filter_by(session_id=session_id, day=day, card_type="message").first():
        db.session.add(EngagementCardDone(session_id=session_id, day=day, card_type="message"))
        db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/engagement/activity-card-dismiss", methods=["POST"])
def engagement_activity_card_dismiss():
    if not _check_csrf_safe():
        return jsonify({"error": "Requête non autorisée"}), 403
    session_id = get_session_id()
    day = _paris_today_str()
    if not EngagementCardDone.query.filter_by(session_id=session_id, day=day, card_type="activity").first():
        db.session.add(EngagementCardDone(session_id=session_id, day=day, card_type="activity"))
        db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/engagement/music-poll-dismiss", methods=["POST"])
def engagement_music_poll_dismiss():
    """Marque la carte swipe « sondage musique » (par poll_id) comme vue pour la journée."""
    if not _check_csrf_safe():
        return jsonify({"error": "Requête non autorisée"}), 403
    data = request.get_json() or {}
    try:
        pid = int(data.get("poll_id") or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid <= 0:
        return jsonify({"error": "poll_id invalide"}), 400
    session_id = get_session_id()
    day = _paris_today_str()
    ct = f"music_{pid}"
    if len(ct) > 32:
        return jsonify({"error": "poll_id trop long"}), 400
    if not EngagementCardDone.query.filter_by(session_id=session_id, day=day, card_type=ct).first():
        db.session.add(EngagementCardDone(session_id=session_id, day=day, card_type=ct))
        db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/session/me", methods=["GET"])
def session_me():
    """Identifiant visiteur (lié au cookie de session prolongé)."""
    vid = get_session_id()
    return jsonify({"visitor_id": vid})


@app.route("/api/session/restore", methods=["POST"])
def session_restore():
    """Réassocie la session serveur à un visitor_id déjà enregistré sur l'appareil (localStorage)."""
    if not _check_csrf_safe():
        return jsonify({"error": "Requête non autorisée"}), 403
    ip = _client_ip()
    if _ip_rate_exceeded(ip, "session_restore", _IP_SESSION_RESTORE_PER_MINUTE, 60):
        return _ip_rate_response()
    data = request.get_json() or {}
    raw = (data.get("visitor_id") or "").strip()
    if not raw:
        return jsonify({"error": "visitor_id requis"}), 400
    try:
        u = uuid.UUID(raw)
        session["visitor_id"] = str(u)
        session.permanent = True
    except (ValueError, TypeError, AttributeError):
        return jsonify({"error": "Identifiant invalide"}), 400
    return jsonify({"visitor_id": session["visitor_id"], "restored": True})


def _get_troll_id() -> str:
    """Identifiant pour la détection troll (visitor_id prioritaire, sinon IP)."""
    vid = session.get("visitor_id", "") if session else ""
    if vid:
        return vid
    return request.remote_addr or "unknown"


@app.route("/api/suggestions/understand", methods=["POST"])
def understand_suggestion():
    """Prévisualise le traitement IA sans enregistrer. Désactive les suggestions pendant le traitement."""
    ip = _client_ip()
    if _ip_rate_exceeded(ip, "understand", _IP_UNDERSTAND_PER_HOUR, 3600):
        return _ip_rate_response()
    data = request.get_json() or {}
    text = (data.get("text") or "").strip()
    if not text or len(text) < 5:
        return jsonify({"error": "Message trop court"}), 400

    locations = _get_locations_list()
    try:
        result = ai.process(text, locations)
        loc_id = result.get("location_id")
        loc_name = ""
        if loc_id and locations:
            for loc in locations:
                if loc.get("id") == loc_id:
                    loc_name = loc.get("name", "")
                    break
        return jsonify({
            "title": result.get("title", ""),
            "category": result.get("category", ""),
            "keywords": result.get("keywords", []),
            "needs_debate": result.get("needs_debate", False),
            "location_name": loc_name,
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/api/suggestions/submit", methods=["POST"])
def submit_suggestion():
    """
    Instant submission: quick rule-based filter -> save immediately -> AI processes in background.
    The student gets instant feedback; AI validation happens asynchronously.
    """
    if get_setting("submissions_open", "true") != "true":
        return jsonify({"error": "Les suggestions sont temporairement fermées."}), 403

    troll_id = _get_troll_id()
    if _troll_check(troll_id):
        return jsonify({"error": "Trop de tentatives refusées. Réessayez dans 15 minutes."}), 429

    ip = _client_ip()
    if _ip_rate_exceeded(ip, "submit", _IP_SUBMIT_PER_HOUR, 3600):
        return _ip_rate_response()

    data = request.get_json() or {}
    text = data.get("text", "").strip()
    force_new = data.get("force_new") is True

    if not text:
        return jsonify({"error": "Le message est vide."}), 400

    if len(text) < 5:
        return jsonify({"error": "Message trop court."}), 400

    is_ok, error_msg = filter_content_quick(text)
    if not is_ok:
        _troll_record_rejection(troll_id)
        _log_activity(
            "filter_blocked",
            f"Refus immédiat (filtre) — {error_msg}",
            detail=f"Texte soumis :\n{text[:2000]}",
        )
        return jsonify({"error": error_msg}), 400

    session_id = get_session_id()

    existing = _get_existing_suggestions()
    match = None if force_new else ai.quick_duplicate_check(text, existing)

    if match:
        suggestion = Suggestion.query.get(match["id"])
        if suggestion:
            existing_vote = Vote.query.filter_by(
                suggestion_id=suggestion.id, session_id=session_id
            ).first()
            if not existing_vote:
                existing_orig = suggestion.original_text or ""
                adds_precision = False
                proceed_with_match = True
                calib_rapport = [e.to_dict() for e in CalibrationRapport.query.order_by(CalibrationRapport.created_at.desc()).limit(12).all()]
                if calib_rapport:
                    try:
                        import llm_engine
                        res = llm_engine.check_rapport_precision(existing_orig, text, calibration=calib_rapport)
                        if res:
                            has_rapport, is_precision = res
                            adds_precision = has_rapport and is_precision
                            if not has_rapport:
                                proceed_with_match = False
                                match = None
                    except Exception:
                        adds_precision = len(text) > len(existing_orig) + 15
                else:
                    adds_precision = len(text) > len(existing_orig) + 15
                if proceed_with_match and adds_precision and data.get("confirm_precision") is True and data.get("existing_id") == suggestion.id:
                    vote = Vote(suggestion_id=suggestion.id, session_id=session_id, original_text=text)
                    db.session.add(vote)
                    suggestion.vote_count = Vote.query.filter_by(suggestion_id=suggestion.id).count() + 1
                    db.session.commit()
                    suggestion.vote_count = Vote.query.filter_by(suggestion_id=suggestion.id).count()
                    db.session.commit()
                    _maybe_generate_subtitle(suggestion.id)
                    return jsonify({
                        "status": "duplicate_voted",
                        "message": "Vos précisions ont été ajoutées à la suggestion !",
                        "suggestion": suggestion.to_dict(),
                    })
                if proceed_with_match and adds_precision:
                    return jsonify({
                        "status": "ask_precision",
                        "message": "Une suggestion similaire existe.",
                        "existing": suggestion.to_dict(),
                        "existing_title": suggestion.title,
                    }), 200
                if proceed_with_match:
                    vote = Vote(suggestion_id=suggestion.id, session_id=session_id, original_text=text)
                    db.session.add(vote)
                    suggestion.vote_count = Vote.query.filter_by(suggestion_id=suggestion.id).count() + 1
                    db.session.commit()
                    suggestion.vote_count = Vote.query.filter_by(suggestion_id=suggestion.id).count()
                    db.session.commit()
                    _maybe_generate_subtitle(suggestion.id)
                    return jsonify({
                        "status": "duplicate_voted",
                        "message": "Cette suggestion existe déjà, votre soutien a été ajouté !",
                        "suggestion": suggestion.to_dict(),
                    })
            return jsonify({
                "status": "duplicate_already_voted",
                "message": "Vous avez déjà soutenu cette suggestion.",
                "suggestion": suggestion.to_dict(),
            })

    suggestion = Suggestion(
        original_text=text,
        title=text[:200],
        status="En attente",
        vote_count=1,
    )
    db.session.add(suggestion)
    db.session.flush()

    vote = Vote(suggestion_id=suggestion.id, session_id=session_id)
    db.session.add(vote)
    db.session.commit()

    _sync_suggestion_archive(suggestion)
    db.session.commit()
    _log_activity("suggestion_submitted", f"Suggestion #{suggestion.id} en attente : « {text[:80]} »")
    _process_suggestion_background(suggestion.id)

    detail_hint = None
    try:
        import llm_engine
        calib_details = [e.to_dict() for e in CalibrationDetails.query.order_by(CalibrationDetails.created_at.desc()).limit(15).all()]
        detail_hint = llm_engine.suggest_detail_hint(text, calibration_details=calib_details)
    except Exception:
        pass

    resp = {"status": "submitted", "message": "Votre suggestion a été envoyée ! Elle sera examinée sous peu."}
    if detail_hint:
        resp["detail_hint"] = detail_hint
    return jsonify(resp), 201


def _process_suggestion_background(suggestion_id: int):
    """Launch background thread to validate & process a suggestion with AI."""
    def _work():
        with app.app_context():
            suggestion = Suggestion.query.get(suggestion_id)
            if not suggestion or suggestion.status != "En attente":
                return

            is_ok, err_msg = filter_content(suggestion.original_text)
            if not is_ok:
                suggestion.status = "Refusée"
                suggestion.reject_reason = (err_msg or "")[:2000]
                db.session.commit()
                _sync_suggestion_archive(suggestion)
                db.session.commit()
                vote = Vote.query.filter_by(suggestion_id=suggestion_id).first()
                if vote:
                    _troll_record_rejection(vote.session_id)
                _log_activity(
                    "suggestion_rejected",
                    f"Suggestion #{suggestion_id} refusée — {err_msg or 'voir détail'}",
                    detail=f"Motif :\n{err_msg or '—'}\n\nTexte original :\n{(suggestion.original_text or '')[:2000]}",
                )
                return

            existing = _get_existing_suggestions()
            match = ai.check_duplicate(suggestion.original_text, [e for e in existing if e["id"] != suggestion_id])
            if match:
                target = Suggestion.query.get(match["id"])
                if target:
                    vote = Vote.query.filter_by(suggestion_id=suggestion.id).first()
                    if vote and not Vote.query.filter_by(suggestion_id=target.id, session_id=vote.session_id).first():
                        Vote.query.filter_by(suggestion_id=suggestion.id).delete()
                        db.session.add(Vote(suggestion_id=target.id, session_id=vote.session_id, original_text=suggestion.original_text))
                        target.vote_count = Vote.query.filter_by(suggestion_id=target.id).count()
                    Suggestion.query.filter_by(id=suggestion_id).delete()
                    db.session.commit()
                    _maybe_generate_subtitle(target.id)
                return

            locations = _get_locations_list()
            result = ai.process(suggestion.original_text, locations)

            suggestion.title = result["title"]
            suggestion.keywords = ",".join(result["keywords"])
            suggestion.category = result["category"]
            if result.get("location_id"):
                suggestion.location_id = result["location_id"]
            suggestion.needs_debate = result.get("needs_debate", False)
            suggestion.ai_needs_debate = result.get("needs_debate", False)
            suggestion.ai_proportion = result.get("ai_proportion")
            suggestion.ai_feasibility = result.get("ai_feasibility")
            suggestion.ai_cost = result.get("ai_cost")
            suggestion.status = "En étude"
            db.session.commit()
            _sync_suggestion_archive(suggestion)
            db.session.commit()
            _log_activity("suggestion_accepted", f"Suggestion #{suggestion_id} validée : « {suggestion.title[:50]} »")

    threading.Thread(target=_work, daemon=True).start()


def _subtitle_like_threshold() -> int:
    try:
        return max(2, int(get_setting("subtitle_like_threshold", "5") or "5"))
    except (TypeError, ValueError):
        return 5


def _effective_support_count(suggestion: Suggestion) -> int:
    """Soutiens : votes « classiques » ou, en mode débat, votes Pour uniquement."""
    if getattr(suggestion, "needs_debate", False):
        return Vote.query.filter_by(suggestion_id=suggestion.id, vote_type="for").count()
    return Vote.query.filter_by(suggestion_id=suggestion.id).count()


def _maybe_generate_subtitle(suggestion_id: int):
    """Résumé IA agrégé (champ subtitle) dès que le seuil de soutiens est atteint ; régénéré à chaque nouveau soutien."""
    def _work():
        with app.app_context():
            suggestion = Suggestion.query.get(suggestion_id)
            if not suggestion:
                return
            thr = _subtitle_like_threshold()
            eff = _effective_support_count(suggestion)
            if eff < thr:
                return
            last_gen = int(getattr(suggestion, "subtitle_generated_at_support_count", None) or 0)
            if suggestion.subtitle and (suggestion.subtitle or "").strip() and eff <= last_gen:
                return

            import llm_engine

            if not llm_engine.is_available():
                return

            original_texts = [(suggestion.original_text or "").strip()]
            votes = Vote.query.filter_by(suggestion_id=suggestion.id).all()
            for v in votes:
                t = (v.original_text or "").strip()
                if t and t not in original_texts:
                    original_texts.append(t)
            if getattr(suggestion, "needs_debate", False):
                args = SuggestionArgument.query.filter_by(suggestion_id=suggestion.id, status="approved").all()
                for a in args:
                    piece = ((a.summary or a.original_text or "").strip())
                    if not piece:
                        continue
                    label = f"[{a.side}] {piece}"
                    if label not in original_texts:
                        original_texts.append(label)

            subtitle = llm_engine.generate_subtitle(suggestion.title, original_texts)
            if subtitle:
                suggestion.subtitle = subtitle[:8000]
                suggestion.subtitle_generated_at_support_count = eff
                db.session.commit()

    threading.Thread(target=_work, daemon=True).start()


def _process_suggestion_argument_background(arg_id: int):
    """Background: LLM processes suggestion argument (analyse, réduit, anti-troll, anti-doublon, vérifie que c'est bien un argument pour/contre)."""
    def _work():
        with app.app_context():
            try:
                arg = db.session.get(SuggestionArgument, arg_id)
                if not arg or arg.status != "pending":
                    return
                suggestion = db.session.get(Suggestion, arg.suggestion_id)
                if not suggestion:
                    return
                is_ok, filter_msg = filter_content_quick(arg.original_text)
                if not is_ok:
                    arg.status = "rejected"
                    db.session.commit()
                    _log_activity(
                        "argument_rejected",
                        f"Argument refusé (filtre) : « {arg.original_text[:80]} »",
                        detail=filter_msg or "",
                    )
                    return
                existing = [
                    (a.summary or a.original_text)
                    for a in SuggestionArgument.query.filter(
                        SuggestionArgument.suggestion_id == suggestion.id,
                        SuggestionArgument.side == arg.side,
                        SuggestionArgument.status == "approved",
                        SuggestionArgument.id != arg.id,
                    ).all()
                ]
                import llm_engine
                valid, summary = llm_engine.process_argument(
                    suggestion.title, arg.original_text, arg.side, existing_arguments=existing
                )
                if valid and summary:
                    arg.summary = summary
                    arg.status = "approved"
                    db.session.commit()
                    _maybe_generate_subtitle(suggestion.id)
                    _log_activity(
                        "suggestion_argument_accepted",
                        f"Argument accepté ({arg.side}) — résumé : {summary[:140]}",
                        detail=summary[:2000],
                    )
                else:
                    reject_reason = (summary or "").strip() or "Argument non pertinent"
                    arg.status = "rejected"
                    db.session.commit()
                    _log_activity(
                        "argument_rejected",
                        f"Argument refusé (IA) : « {arg.original_text[:80]} »",
                        detail=reject_reason[:2000],
                    )
            except Exception as ex:
                db.session.rollback()
                try:
                    arg2 = db.session.get(SuggestionArgument, arg_id)
                    if arg2 and arg2.status == "pending":
                        arg2.status = "rejected"
                        db.session.commit()
                    _log_activity(
                        "argument_rejected",
                        f"Argument refusé (erreur technique) — id {arg_id}",
                        detail=str(ex)[:2000],
                    )
                except Exception:
                    db.session.rollback()

    threading.Thread(target=_work, daemon=True).start()


def _update_suggestion_vote_counts(sid: int):
    """Recalcule les compteurs depuis la table `votes` uniquement (pas d’incrément in-place)."""
    s = Suggestion.query.get(sid)
    if not s:
        return
    if getattr(s, "needs_debate", False):
        s.vote_for = Vote.query.filter_by(suggestion_id=sid, vote_type="for").count()
        s.vote_against = Vote.query.filter_by(suggestion_id=sid, vote_type="against").count()
        s.vote_count = (s.vote_for or 0) + (s.vote_against or 0)
    else:
        total = Vote.query.filter_by(suggestion_id=sid).count()
        s.vote_count = total
        # Mode classique : tous les votes sont « for » ; vote_for reflète le total affiché
        s.vote_for = total
        s.vote_against = 0
    s.updated_at = datetime.now(timezone.utc)
    db.session.commit()


def _vote_simple_suggestion_locked(sid: int, suggestion: Suggestion, session_id: str, data: dict):
    """
    Suggestion sans débat : intention explicite remove_vote true/false (idempotent).
    - remove_vote True  → supprimer le vote s'il existe, sinon no-op
    - remove_vote False → ajouter un vote « for » s'il n'existe pas, sinon no-op (déjà soutenu)
    """
    vote_type = data.get("vote_type") or "for"
    if vote_type not in ("for", "against"):
        vote_type = "for"
    remove_vote = bool(data.get("remove_vote"))

    existing_vote = Vote.query.filter_by(suggestion_id=sid, session_id=session_id).first()

    if remove_vote:
        if existing_vote:
            db.session.delete(existing_vote)
            db.session.commit()
        _update_suggestion_vote_counts(sid)
        suggestion = Suggestion.query.get(sid)
        return jsonify(_pack_vote_json(suggestion, session_id, False, has_voted=False, my_vote=None))

    if existing_vote:
        _update_suggestion_vote_counts(sid)
        suggestion = Suggestion.query.get(sid)
        return jsonify(_pack_vote_json(suggestion, session_id, False, my_vote=existing_vote.vote_type, has_voted=True))

    vote = Vote(suggestion_id=sid, session_id=session_id, vote_type=vote_type)
    db.session.add(vote)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        _update_suggestion_vote_counts(sid)
        suggestion = Suggestion.query.get(sid)
        if not suggestion:
            return jsonify({"error": "Synchronisation impossible.", "server_ts": _server_ts_ms()}), 500
        ev = Vote.query.filter_by(suggestion_id=sid, session_id=session_id).first()
        return jsonify(
            _pack_vote_json(
                suggestion,
                session_id,
                False,
                has_voted=ev is not None,
                my_vote=ev.vote_type if ev else None,
            )
        )

    _update_suggestion_vote_counts(sid)
    suggestion = Suggestion.query.get(sid)
    if vote_type == "for":
        try:
            _increment_daily_activity(session_id, "like")
        except Exception:
            pass
    _maybe_generate_subtitle(sid)
    return jsonify(_pack_vote_json(suggestion, session_id, False, my_vote=vote_type, has_voted=True))


def _pack_vote_json(suggestion: Suggestion, session_id: str, needs_debate: bool, **overrides) -> dict:
    """État vote complet + server_ts (réconciliation client / désordre des réponses)."""
    my = Vote.query.filter_by(suggestion_id=suggestion.id, session_id=session_id).first()
    if needs_debate:
        vf = Vote.query.filter_by(suggestion_id=suggestion.id, vote_type="for").count()
        va = Vote.query.filter_by(suggestion_id=suggestion.id, vote_type="against").count()
        vc = vf + va
    else:
        total = Vote.query.filter_by(suggestion_id=suggestion.id).count()
        vc, vf, va = total, total, 0
    d = {
        "server_ts": _server_ts_ms(),
        "vote_count": vc,
        "vote_for": vf,
        "vote_against": va,
        "has_voted": my is not None,
        "my_vote": my.vote_type if my else None,
    }
    d.update(overrides)
    if needs_debate:
        d["arguments_for"] = [a.to_dict() for a in suggestion.arguments if a.side == "for" and a.status == "approved"]
        d["arguments_against"] = [a.to_dict() for a in suggestion.arguments if a.side == "against" and a.status == "approved"]
    return d


@app.route("/api/suggestions/<int:sid>/vote", methods=["POST"])
def vote_suggestion(sid):
    ip = _client_ip()
    if _ip_rate_exceeded(ip, "vote", _IP_VOTE_PER_MINUTE, 60):
        return _ip_rate_response()
    with _lock_for_sid(sid):
        return _vote_suggestion_locked(sid)


def _vote_suggestion_locked(sid: int):
    suggestion = Suggestion.query.get_or_404(sid)
    session_id = get_session_id()
    needs_debate = getattr(suggestion, "needs_debate", False)
    if _vote_burst_exceeded(session_id, sid):
        return _vote_burst_response(sid, session_id, needs_debate)

    data = request.get_json() or {}
    if not needs_debate:
        return _vote_simple_suggestion_locked(sid, suggestion, session_id, data)

    vote_type = data.get("vote_type", "for")
    argument_text = (data.get("argument") or "").strip()
    remove_vote = bool(data.get("remove_vote"))

    if vote_type not in ("for", "against"):
        vote_type = "for"

    existing_vote = Vote.query.filter_by(suggestion_id=sid, session_id=session_id).first()

    if not existing_vote and remove_vote:
        _update_suggestion_vote_counts(sid)
        suggestion = Suggestion.query.get(sid)
        return jsonify(_pack_vote_json(suggestion, session_id, needs_debate, has_voted=False, my_vote=None))

    if existing_vote:
        if existing_vote.vote_type == vote_type and not argument_text:
            return jsonify(_pack_vote_json(suggestion, session_id, True, my_vote=vote_type, has_voted=True))
        if existing_vote.vote_type == vote_type and argument_text:
            ok, msg = filter_content_quick(argument_text)
            if not ok:
                return jsonify({"error": msg}), 400
            pending_arg_ids = []
            for part in _split_argument_text(argument_text):
                arg = SuggestionArgument(suggestion_id=sid, session_id=session_id, side=vote_type, original_text=part, status="pending")
                db.session.add(arg)
                db.session.flush()
                pending_arg_ids.append(arg.id)
            db.session.commit()
            for aid in pending_arg_ids:
                _process_suggestion_argument_background(aid)
            suggestion = Suggestion.query.get(sid)
            return jsonify(_pack_vote_json(suggestion, session_id, True, my_vote=vote_type, has_voted=True))
        old_type = existing_vote.vote_type
        existing_vote.vote_type = vote_type
        if old_type == "for":
            suggestion.vote_for = max(0, getattr(suggestion, "vote_for", 0) - 1)
        else:
            suggestion.vote_against = max(0, getattr(suggestion, "vote_against", 0) - 1)
    else:
        vote = Vote(suggestion_id=sid, session_id=session_id, vote_type=vote_type)
        db.session.add(vote)

    if vote_type == "for":
        suggestion.vote_for = (getattr(suggestion, "vote_for", 0) or 0) + 1
    else:
        suggestion.vote_against = (getattr(suggestion, "vote_against", 0) or 0) + 1

    pending_arg_ids = []
    if argument_text:
        ok, msg = filter_content_quick(argument_text)
        if not ok:
            return jsonify({"error": msg}), 400
        for part in _split_argument_text(argument_text):
            arg = SuggestionArgument(
                suggestion_id=sid, session_id=session_id,
                side=vote_type, original_text=part, status="pending"
            )
            db.session.add(arg)
            db.session.flush()
            pending_arg_ids.append(arg.id)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        _update_suggestion_vote_counts(sid)
        suggestion = Suggestion.query.get(sid)
        if not suggestion:
            return jsonify({"error": "Synchronisation impossible.", "server_ts": _server_ts_ms()}), 500
        ev = Vote.query.filter_by(suggestion_id=sid, session_id=session_id).first()
        resp = _pack_vote_json(
            suggestion,
            session_id,
            needs_debate,
            has_voted=True,
            my_vote=ev.vote_type if ev else vote_type,
        )
        return jsonify(resp)

    _update_suggestion_vote_counts(sid)
    suggestion = Suggestion.query.get(sid)
    for aid in pending_arg_ids:
        _process_suggestion_argument_background(aid)

    _maybe_generate_subtitle(sid)
    resp = _pack_vote_json(suggestion, session_id, needs_debate, my_vote=vote_type, has_voted=True)
    return jsonify(resp)


@app.route("/api/suggestions/<int:sid>/argument", methods=["POST"])
def add_suggestion_argument(sid):
    """Ajouter un argument (pour ou contre) sans changer le vote. Permet plusieurs arguments par personne."""
    ip = _client_ip()
    if _ip_rate_exceeded(ip, "arg", _IP_ARG_PER_MINUTE, 60):
        return _ip_rate_response()
    suggestion = Suggestion.query.get_or_404(sid)
    session_id = get_session_id()
    data = request.get_json() or {}
    side = data.get("side", "for")
    argument_text = (data.get("argument") or "").strip()
    needs_debate = getattr(suggestion, "needs_debate", False)

    if not needs_debate:
        return jsonify({"error": "Cette suggestion n'est pas en mode débat"}), 400
    if side not in ("for", "against"):
        return jsonify({"error": "side doit être 'for' ou 'against'"}), 400
    if not argument_text or len(argument_text) < 5:
        return jsonify({"error": "Argument trop court"}), 400

    ok, msg = filter_content_quick(argument_text)
    if not ok:
        return jsonify({"error": msg}), 400

    parts = _split_argument_text(argument_text)
    if not parts:
        return jsonify({"error": "Argument invalide"}), 400

    my_vote = Vote.query.filter_by(suggestion_id=sid, session_id=session_id).first()
    if not my_vote:
        return jsonify({"error": "Vous devez d'abord voter pour ajouter un argument"}), 400
    if my_vote.vote_type != side:
        return jsonify({"error": "Vous ne pouvez ajouter que des arguments pour votre camp"}), 400

    pending_arg_ids: list[int] = []
    for part in parts:
        arg = SuggestionArgument(
            suggestion_id=sid, session_id=session_id,
            side=side, original_text=part, status="pending"
        )
        db.session.add(arg)
        db.session.flush()
        pending_arg_ids.append(arg.id)
    db.session.commit()
    for aid in pending_arg_ids:
        _process_suggestion_argument_background(aid)

    db.session.refresh(suggestion)
    return jsonify({
        "vote_for": getattr(suggestion, "vote_for", 0),
        "vote_against": getattr(suggestion, "vote_against", 0),
        "arguments_for": [a.to_dict() for a in suggestion.arguments if a.side == "for" and a.status == "approved"],
        "arguments_against": [a.to_dict() for a in suggestion.arguments if a.side == "against" and a.status == "approved"],
    })


@app.route("/api/categories", methods=["GET"])
def list_categories():
    return jsonify(Suggestion.VALID_CATEGORIES)


@app.route("/api/locations", methods=["GET"])
def public_list_locations():
    locations = Location.query.order_by(Location.name).all()
    return jsonify([{"id": l.id, "name": l.name} for l in locations])


@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({"llm_available": ai.llm_available()})


def _parse_iso_datetime_optional(val):
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return _utc_dt(val)
    s = str(val).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _recalc_music_track_vote_count(track_id: int) -> int:
    c = db.session.query(func.count(MusicVote.id)).filter_by(track_id=track_id).scalar() or 0
    t = MusicTrack.query.get(track_id)
    if t:
        t.vote_count = c
    return int(c)


def _music_poll_deactivate_others(keep_id: int | None):
    q = MusicPoll.query.filter_by(is_active=True)
    if keep_id is not None:
        q = q.filter(MusicPoll.id != keep_id)
    for p in q.all():
        p.is_active = False


def _music_track_to_public_dict(t: MusicTrack, visitor_id: str, vc: int | None = None) -> dict:
    if vc is None:
        vc = db.session.query(func.count(MusicVote.id)).filter_by(track_id=t.id).scalar() or 0
    has_voted = (
        MusicVote.query.filter_by(track_id=t.id, session_id=visitor_id).first() is not None
    )
    return {
        "id": t.id,
        "spotify_track_id": t.spotify_track_id,
        "title": t.title,
        "artist": t.artist,
        "album": t.album or "",
        "thumbnail_url": t.thumbnail_url,
        "preview_url": t.preview_url,
        "preview_available": bool(t.preview_url),
        "spotify_url": t.spotify_url or f"https://open.spotify.com/track/{t.spotify_track_id}",
        "vote_count": int(vc),
        "has_voted": has_voted,
    }


def _music_track_to_preview_dict(t: MusicTrack) -> dict:
    """Aperçu admin : mêmes champs que l’API publique, votes affichés mais sans interaction."""
    vc = db.session.query(func.count(MusicVote.id)).filter_by(track_id=t.id).scalar() or 0
    return {
        "id": t.id,
        "spotify_track_id": t.spotify_track_id,
        "title": t.title,
        "artist": t.artist,
        "album": t.album or "",
        "thumbnail_url": t.thumbnail_url,
        "preview_url": t.preview_url,
        "preview_available": bool(t.preview_url),
        "spotify_url": t.spotify_url or f"https://open.spotify.com/track/{t.spotify_track_id}",
        "vote_count": int(vc),
        "has_voted": False,
    }


def _music_poll_serialize_admin(p: MusicPoll, include_tracks: bool = True) -> dict:
    d = {
        "id": p.id,
        "title": p.title,
        "is_active": p.is_active,
        "max_votes": p.max_votes or 1,
        "end_date": p.end_date.isoformat() if p.end_date else None,
        "spotify_playlist_url": p.spotify_playlist_url,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }
    if include_tracks:
        tracks = MusicTrack.query.filter_by(poll_id=p.id).order_by(MusicTrack.position, MusicTrack.id).all()
        d["tracks"] = [_music_track_to_admin_dict(x) for x in tracks]
    return d


def _music_track_to_admin_dict(t: MusicTrack) -> dict:
    vc = db.session.query(func.count(MusicVote.id)).filter_by(track_id=t.id).scalar() or 0
    return {
        "id": t.id,
        "poll_id": t.poll_id,
        "spotify_url": t.spotify_url,
        "spotify_track_id": t.spotify_track_id,
        "title": t.title,
        "artist": t.artist,
        "album": t.album or "",
        "thumbnail_url": t.thumbnail_url,
        "preview_url": t.preview_url,
        "preview_available": bool(t.preview_url),
        "vote_count": int(vc),
        "position": t.position or 0,
    }


_music_poll_preview_enrich_last: dict[int, float] = {}


def _maybe_enrich_track_preview(t: MusicTrack) -> None:
    """Si pas d’URL d’extrait en base, tente Spotify+Deezer ou Deezer seul (recherche)."""
    if t.preview_url:
        return
    tid = int(t.id)
    now = time.time()
    if now - _music_poll_preview_enrich_last.get(tid, 0.0) < 300:
        return
    _music_poll_preview_enrich_last[tid] = now
    try:
        if music_utils.spotify_credentials_configured():
            meta = music_utils.fetch_spotify_track_metadata(t.spotify_url)
            pu = meta.get("preview_url")
            if pu:
                t.preview_url = pu[:500]
        else:
            pu = music_utils.try_deezer_preview_for_track(t.artist or "", t.title or "", None)
            if pu:
                t.preview_url = pu[:500]
    except Exception:
        pass


def _music_poll_active_payload() -> dict | None:
    """Un seul sondage musique actif (non expiré, avec morceaux), format mur élève."""
    if get_setting("feature_music_poll_enabled", "true") != "true":
        return None
    now = datetime.now(timezone.utc)
    visitor_id = get_session_id()
    rows = MusicPoll.query.filter_by(is_active=True).order_by(MusicPoll.id.desc()).all()
    if len(rows) > 1:
        for p in rows[1:]:
            p.is_active = False
        db.session.commit()
        rows = rows[:1]
    poll = rows[0] if rows else None
    if not poll:
        return None
    if poll.end_date:
        ed = _utc_dt(poll.end_date)
        if ed and now > ed:
            poll.is_active = False
            db.session.commit()
            return None
    tracks = MusicTrack.query.filter_by(poll_id=poll.id).order_by(MusicTrack.position, MusicTrack.id).all()
    if not tracks:
        return None
    session_votes_count = (
        db.session.query(func.count(MusicVote.id))
        .filter_by(poll_id=poll.id, session_id=visitor_id)
        .scalar()
        or 0
    )
    out_tracks = []
    for t in tracks:
        _maybe_enrich_track_preview(t)
        vc = db.session.query(func.count(MusicVote.id)).filter_by(track_id=t.id).scalar() or 0
        if t.vote_count != vc:
            t.vote_count = vc
        out_tracks.append(_music_track_to_public_dict(t, visitor_id, vc))
    db.session.commit()
    return {
        "id": poll.id,
        "title": poll.title,
        "max_votes": max(1, min(2, poll.max_votes or 1)),
        "spotify_playlist_url": poll.spotify_playlist_url,
        "session_votes_count": int(session_votes_count),
        "tracks": out_tracks,
    }


@app.route("/api/music-poll/active", methods=["GET"])
def music_poll_active():
    payload = _music_poll_active_payload()
    if not payload:
        return jsonify({"active": False})
    return jsonify({"active": True, "poll": payload})


@app.route("/api/music-poll/<int:poll_id>/vote", methods=["POST"])
def music_poll_vote(poll_id):
    if get_setting("feature_music_poll_enabled", "true") != "true":
        return jsonify({"message": "Sondage musique désactivé"}), 400
    poll = MusicPoll.query.get_or_404(poll_id)
    now = datetime.now(timezone.utc)
    if poll.end_date and _utc_dt(poll.end_date) and now > _utc_dt(poll.end_date):
        if poll.is_active:
            poll.is_active = False
            db.session.commit()
        return jsonify({"message": "Sondage terminé"}), 400
    if not poll.is_active:
        return jsonify({"message": "Sondage inactif"}), 400

    data = request.get_json() or {}
    track_id = data.get("track_id")
    remove_vote = bool(data.get("remove_vote"))
    try:
        track_id = int(track_id)
    except (TypeError, ValueError):
        return jsonify({"message": "track_id invalide"}), 400

    track = MusicTrack.query.filter_by(id=track_id, poll_id=poll_id).first()
    if not track:
        return jsonify({"message": "Morceau introuvable"}), 400

    session_id = get_session_id()
    max_v = max(1, min(2, poll.max_votes or 1))
    existing = MusicVote.query.filter_by(track_id=track_id, session_id=session_id).first()

    if remove_vote:
        if existing:
            db.session.delete(existing)
            db.session.commit()
        vc = _recalc_music_track_vote_count(track_id)
        session_votes_count = (
            db.session.query(func.count(MusicVote.id))
            .filter_by(poll_id=poll_id, session_id=session_id)
            .scalar()
            or 0
        )
        return jsonify(
            {
                "track_id": track_id,
                "has_voted": False,
                "vote_count": vc,
                "session_votes_count": int(session_votes_count),
            }
        )

    if existing:
        vc = _recalc_music_track_vote_count(track_id)
        session_votes_count = (
            db.session.query(func.count(MusicVote.id))
            .filter_by(poll_id=poll_id, session_id=session_id)
            .scalar()
            or 0
        )
        return jsonify(
            {
                "track_id": track_id,
                "has_voted": True,
                "vote_count": vc,
                "session_votes_count": int(session_votes_count),
            }
        )

    session_votes_count = (
        db.session.query(func.count(MusicVote.id))
        .filter_by(poll_id=poll_id, session_id=session_id)
        .scalar()
        or 0
    )
    if session_votes_count >= max_v:
        return jsonify({"message": "Nombre de votes max atteint"}), 400

    db.session.add(MusicVote(poll_id=poll_id, track_id=track_id, session_id=session_id))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
    vc = _recalc_music_track_vote_count(track_id)
    session_votes_count = (
        db.session.query(func.count(MusicVote.id))
        .filter_by(poll_id=poll_id, session_id=session_id)
        .scalar()
        or 0
    )
    has_v = MusicVote.query.filter_by(track_id=track_id, session_id=session_id).first() is not None
    return jsonify(
        {
            "track_id": track_id,
            "has_voted": has_v,
            "vote_count": vc,
            "session_votes_count": int(session_votes_count),
        }
    )


def _spotify_secret_hint(val: str | None) -> str | None:
    if not val:
        return None
    v = val.strip()
    if len(v) <= 4:
        return "••••"
    return "•" * min(10, len(v) - 4) + v[-4:]


@app.route("/api/admin/spotify-settings", methods=["GET", "PUT"])
@admin_required
def admin_spotify_settings():
    """Client ID / Secret : stockés en base (site_settings), repli sur les variables d'environnement."""
    if request.method == "GET":
        row_id = SiteSettings.query.get("spotify_client_id")
        row_sec = SiteSettings.query.get("spotify_client_secret")
        dbcid = ((row_id.value if row_id else "") or "").strip()
        dbsec = ((row_sec.value if row_sec else "") or "").strip()
        env_cid = (app.config.get("SPOTIFY_CLIENT_ID") or "").strip()
        env_sec = (app.config.get("SPOTIFY_CLIENT_SECRET") or "").strip()
        resolved_cid, resolved_sec = music_utils.get_resolved_spotify_credentials()
        return jsonify(
            {
                "client_id": resolved_cid,
                "client_id_stored_in_db": bool(dbcid),
                "client_secret_configured": bool(resolved_sec),
                "client_secret_hint": _spotify_secret_hint(resolved_sec) if resolved_sec else None,
                "configured": music_utils.spotify_credentials_configured(),
                "env_fallback_active": {
                    "client_id": bool(env_cid) and not dbcid,
                    "client_secret": bool(env_sec) and not dbsec,
                },
            }
        )

    data = request.get_json() or {}
    if "client_id" in data:
        v = data.get("client_id")
        set_setting("spotify_client_id", (v or "").strip() if isinstance(v, str) else "")
    if data.get("clear_client_secret"):
        set_setting("spotify_client_secret", "")
    elif "client_secret" in data:
        sec = data.get("client_secret")
        if isinstance(sec, str) and sec.strip():
            set_setting("spotify_client_secret", sec.strip())
    music_utils.clear_spotify_token_cache()
    test_ok = False
    test_message = ""
    try:
        tok = music_utils.get_spotify_token()
        if tok:
            test_ok = True
            test_message = "Test API Spotify : jeton d’accès obtenu — la connexion fonctionne."
        else:
            test_message = (
                "Identifiants enregistrés, mais aucun jeton (vérifiez Client ID et Secret sur le dashboard)."
            )
    except Exception as e:
        test_message = f"Test API Spotify : erreur — {str(e)[:280]}"

    return jsonify(
        {
            "ok": True,
            "configured": music_utils.spotify_credentials_configured(),
            "test_ok": test_ok,
            "test_message": test_message,
        }
    )


@app.route("/api/admin/music-polls", methods=["GET"])
@admin_required
def admin_music_polls_list():
    rows = MusicPoll.query.order_by(MusicPoll.id.desc()).all()
    out = []
    for p in rows:
        n = MusicTrack.query.filter_by(poll_id=p.id).count()
        out.append(
            {
                "id": p.id,
                "title": p.title,
                "is_active": p.is_active,
                "track_count": n,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
        )
    return jsonify(
        {
            "polls": out,
            "spotify_configured": music_utils.spotify_credentials_configured(),
        }
    )


@app.route("/api/admin/music-poll/<int:poll_id>", methods=["GET"])
@admin_required
def admin_music_poll_get(poll_id):
    p = MusicPoll.query.get_or_404(poll_id)
    return jsonify({"poll": _music_poll_serialize_admin(p, True)})


@app.route("/api/admin/music-poll", methods=["POST"])
@admin_required
def admin_music_poll_create():
    data = request.get_json() or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Titre requis"}), 400
    max_votes = int(data.get("max_votes", 1))
    if max_votes not in (1, 2):
        max_votes = 1
    end_date = _parse_iso_datetime_optional(data.get("end_date"))
    spl = (data.get("spotify_playlist_url") or "").strip() or None
    is_active = bool(data.get("is_active"))
    p = MusicPoll(
        title=title[:200],
        max_votes=max_votes,
        end_date=end_date,
        spotify_playlist_url=(spl[:500] if spl else None),
        is_active=False,
    )
    db.session.add(p)
    db.session.flush()
    if is_active:
        _music_poll_deactivate_others(p.id)
        p.is_active = True
    db.session.commit()
    return jsonify({"poll": _music_poll_serialize_admin(p, True)})


@app.route("/api/admin/music-poll/<int:poll_id>", methods=["PUT"])
@admin_required
def admin_music_poll_update(poll_id):
    p = MusicPoll.query.get_or_404(poll_id)
    data = request.get_json() or {}
    if "title" in data:
        t = (data.get("title") or "").strip()
        if not t:
            return jsonify({"error": "Titre requis"}), 400
        p.title = t[:200]
    if "max_votes" in data:
        mv = int(data.get("max_votes", 1))
        p.max_votes = mv if mv in (1, 2) else 1
    if "end_date" in data:
        p.end_date = _parse_iso_datetime_optional(data.get("end_date"))
    if "spotify_playlist_url" in data:
        spl = (data.get("spotify_playlist_url") or "").strip() or None
        p.spotify_playlist_url = spl[:500] if spl else None
    if "is_active" in data:
        is_active = bool(data.get("is_active"))
        if is_active:
            _music_poll_deactivate_others(p.id)
            p.is_active = True
        else:
            p.is_active = False
    db.session.commit()
    return jsonify({"poll": _music_poll_serialize_admin(p, True)})


@app.route("/api/admin/music-poll/<int:poll_id>", methods=["DELETE"])
@admin_required
def admin_music_poll_delete(poll_id):
    p = MusicPoll.query.get_or_404(poll_id)
    db.session.delete(p)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/music-poll/<int:poll_id>/tracks", methods=["POST"])
@admin_required
def admin_music_poll_add_track(poll_id):
    if not music_utils.spotify_credentials_configured():
        return jsonify(
            {
                "error": "Spotify non configuré : renseignez Client ID et Secret dans Administration → Sondage musique, ou les variables SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET.",
            }
        ), 503
    p = MusicPoll.query.get_or_404(poll_id)
    data = request.get_json() or {}
    spotify_url = (data.get("spotify_url") or "").strip()
    if not spotify_url:
        return jsonify({"error": "spotify_url requis"}), 400
    n_existing = MusicTrack.query.filter_by(poll_id=poll_id).count()
    if n_existing >= 5:
        return jsonify({"error": "Maximum 5 morceaux par sondage"}), 400
    try:
        meta = music_utils.fetch_spotify_track_metadata(spotify_url)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    tid = meta["spotify_track_id"]
    dup = MusicTrack.query.filter_by(poll_id=poll_id, spotify_track_id=tid).first()
    if dup:
        return jsonify({"error": "Ce morceau est déjà dans le sondage"}), 400
    pos = (
        db.session.query(func.max(MusicTrack.position))
        .filter_by(poll_id=poll_id)
        .scalar()
    )
    pos = int(pos or 0) + 1
    t = MusicTrack(
        poll_id=poll_id,
        spotify_url=meta["spotify_url"][:500],
        spotify_track_id=tid[:30],
        title=(meta.get("title") or "Sans titre")[:300],
        artist=(meta.get("artist") or "")[:200],
        album=(meta.get("album") or "")[:200],
        thumbnail_url=(meta["thumbnail_url"][:500] if meta.get("thumbnail_url") else None),
        preview_url=(meta["preview_url"][:500] if meta.get("preview_url") else None),
        position=pos,
        vote_count=0,
    )
    db.session.add(t)
    db.session.commit()
    return jsonify({"track": _music_track_to_admin_dict(t)})


@app.route("/api/admin/music-poll/<int:poll_id>/tracks/<int:track_id>", methods=["DELETE"])
@admin_required
def admin_music_poll_delete_track(poll_id, track_id):
    MusicPoll.query.get_or_404(poll_id)
    t = MusicTrack.query.filter_by(id=track_id, poll_id=poll_id).first()
    if not t:
        abort(404)
    db.session.delete(t)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/music-poll/<int:poll_id>/results", methods=["GET"])
@admin_required
def admin_music_poll_results(poll_id):
    p = MusicPoll.query.get_or_404(poll_id)
    tracks = MusicTrack.query.filter_by(poll_id=poll_id).all()
    rows = []
    total = 0
    for t in tracks:
        vc = db.session.query(func.count(MusicVote.id)).filter_by(track_id=t.id).scalar() or 0
        total += int(vc)
        rows.append(
            {
                "id": t.id,
                "title": t.title,
                "artist": t.artist,
                "vote_count": int(vc),
            }
        )
    rows.sort(key=lambda x: (-x["vote_count"], x["id"]))
    for r in rows:
        r["percent"] = round(100.0 * r["vote_count"] / total, 1) if total else 0.0
    return jsonify(
        {
            "poll_id": p.id,
            "title": p.title,
            "total_votes": total,
            "tracks": rows,
        }
    )


def _ringtone_active_poll_bundle():
    """Sondage musique actif + résultats (votes), pour l’onglet Résultats / sonnerie."""
    p = MusicPoll.query.filter_by(is_active=True).order_by(MusicPoll.id.desc()).first()
    if not p:
        return None
    tracks = MusicTrack.query.filter_by(poll_id=p.id).all()
    rows = []
    total = 0
    for t in tracks:
        vc = db.session.query(func.count(MusicVote.id)).filter_by(track_id=t.id).scalar() or 0
        vc = int(vc)
        total += vc
        rows.append(
            {
                "id": t.id,
                "title": t.title,
                "artist": t.artist,
                "vote_count": vc,
                "thumbnail_url": t.thumbnail_url,
            }
        )
    rows.sort(key=lambda x: (-x["vote_count"], x["id"]))
    for r in rows:
        r["percent"] = round(100.0 * r["vote_count"] / total, 1) if total else 0.0
    return {
        "id": p.id,
        "title": p.title,
        "total_votes": total,
        "tracks": rows,
    }


@app.route("/api/admin/ringtone", methods=["GET", "PUT"])
@admin_required
def admin_ringtone():
    """Sélection affichée sur la page élève (musique de sonnerie) — métadonnées uniquement, pas d’audio."""
    if request.method == "GET":
        enabled = get_setting("feature_ringtone_banner_enabled", "false") == "true"
        raw = (get_setting("ringtone_selection_json", "") or "").strip()
        selection = None
        if raw:
            try:
                selection = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                selection = None
        return jsonify(
            {
                "enabled": enabled,
                "selection": selection,
                "active_poll": _ringtone_active_poll_bundle(),
                "spotify_configured": music_utils.spotify_credentials_configured(),
            }
        )

    data = request.get_json() or {}
    if data.get("clear_selection"):
        set_setting("ringtone_selection_json", "")
        return jsonify({"ok": True})

    sel = data.get("selection")
    if sel is None:
        return jsonify({"ok": True})

    source = (sel.get("source") or "").strip()
    if source == "manual":
        if not music_utils.spotify_credentials_configured():
            return jsonify(
                {"error": "Spotify non configuré : renseignez Client ID et Secret (Sondage musique)."},
            ), 503
        url = (sel.get("spotify_url") or "").strip()
        if not url:
            return jsonify({"error": "URL Spotify requise"}), 400
        try:
            meta = music_utils.fetch_spotify_track_metadata(url)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        payload = {
            "source": "manual",
            "spotify_track_id": meta["spotify_track_id"],
            "title": meta.get("title") or "Sans titre",
            "artist": meta.get("artist") or "",
            "thumbnail_url": meta.get("thumbnail_url"),
        }
        set_setting("ringtone_selection_json", json.dumps(payload, ensure_ascii=False))
    elif source == "poll":
        try:
            poll_id = int(sel.get("poll_id"))
            track_id = int(sel.get("track_id"))
        except (TypeError, ValueError):
            return jsonify({"error": "poll_id et track_id requis"}), 400
        t = MusicTrack.query.filter_by(id=track_id, poll_id=poll_id).first()
        if not t:
            return jsonify({"error": "Morceau introuvable"}), 400
        payload = {
            "source": "poll",
            "poll_id": poll_id,
            "track_id": track_id,
            "spotify_track_id": t.spotify_track_id,
            "title": t.title,
            "artist": t.artist,
            "thumbnail_url": t.thumbnail_url,
        }
        set_setting("ringtone_selection_json", json.dumps(payload, ensure_ascii=False))
    else:
        return jsonify({"error": "source invalide"}), 400

    return jsonify({"ok": True})


@app.route("/api/ringtone/current", methods=["GET"])
def public_ringtone_current():
    """Bandeau élève : métadonnées du morceau choisi (pas de lecture audio)."""
    if get_setting("feature_ringtone_banner_enabled", "false") != "true":
        return jsonify({"enabled": False, "track": None})
    raw = (get_setting("ringtone_selection_json", "") or "").strip()
    if not raw:
        return jsonify({"enabled": True, "track": None})
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return jsonify({"enabled": True, "track": None})
    track = {
        "title": d.get("title"),
        "artist": d.get("artist"),
        "thumbnail_url": d.get("thumbnail_url"),
    }
    return jsonify({"enabled": True, "track": track})


@app.route("/api/admin/music-poll/<int:poll_id>/preview", methods=["GET"])
@admin_required
def admin_music_poll_preview(poll_id):
    """JSON au même format que /api/music-poll/active pour prévisualiser le rendu élève (sans vote)."""
    if get_setting("feature_music_poll_enabled", "true") != "true":
        return jsonify({"active": False, "message": "Sondage musique désactivé."})
    p = MusicPoll.query.get_or_404(poll_id)
    tracks = MusicTrack.query.filter_by(poll_id=p.id).order_by(MusicTrack.position, MusicTrack.id).all()
    if not tracks:
        return jsonify(
            {
                "active": False,
                "message": "Ajoute au moins un morceau pour voir l’aperçu.",
            }
        )
    for t in tracks:
        _maybe_enrich_track_preview(t)
    db.session.commit()
    out_tracks = [_music_track_to_preview_dict(t) for t in tracks]
    return jsonify(
        {
            "active": True,
            "preview": True,
            "poll": {
                "id": p.id,
                "title": p.title,
                "max_votes": max(1, min(2, p.max_votes or 1)),
                "spotify_playlist_url": p.spotify_playlist_url,
                "session_votes_count": 0,
                "tracks": out_tracks,
            },
        }
    )


@app.route("/api/admin/spotify/verify-playlist", methods=["POST"])
@admin_required
def admin_spotify_verify_playlist():
    """Vérifie qu’une URL playlist est reconnue et interroge l’API Spotify (nom, pochette)."""
    if not music_utils.spotify_credentials_configured():
        return jsonify({"ok": False, "error": "Spotify non configuré (Client ID + Secret)."}), 503
    data = request.get_json() or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "Indiquez une URL de playlist."}), 400
    try:
        music_utils.extract_spotify_playlist_id(url)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    meta = music_utils.fetch_spotify_playlist_metadata(url)
    if meta.get("error"):
        return jsonify(
            {
                "ok": False,
                "error": "L’URL est reconnue mais l’API Spotify a échoué.",
                "detail": meta.get("error"),
            }
        )
    name = meta.get("name") or "Playlist"
    return jsonify(
        {
            "ok": True,
            "playlist_id": meta["playlist_id"],
            "name": name,
            "external_url": meta.get("external_url"),
            "thumbnail_url": meta.get("thumbnail_url"),
            "message": f"Playlist « {name} » détectée et vérifiée sur Spotify.",
        }
    )


def _music_preview_url_allowed(url: str) -> bool:
    """Autorise uniquement les extraits Spotify (p.scdn.co) et Deezer (*.dzcdn.net)."""
    pr = urlparse(url)
    if pr.scheme != "https" or not pr.netloc:
        return False
    netloc = pr.netloc.lower()
    path = pr.path.lower()
    if netloc == "p.scdn.co":
        return "mp3-preview" in pr.path or "/mp3/" in pr.path
    if netloc.endswith(".dzcdn.net"):
        return ".mp3" in path or "/api/" in path or "/stream/preview" in path
    return False


def _spotify_preview_proxy_response():
    """Proxy sécurisé pour les MP3 (Spotify p.scdn.co + extraits Deezer *.dzcdn.net)."""
    import requests as req_lib

    raw = (request.args.get("url") or "").strip()
    url = unquote(raw)
    if not url.startswith("https://"):
        return jsonify({"error": "URL invalide"}), 400
    if not _music_preview_url_allowed(url):
        return jsonify({"error": "Hôte ou chemin non autorisé pour la préécoute"}), 400
    try:
        r = req_lib.get(
            url,
            stream=True,
            timeout=25,
            headers={"User-Agent": "Mozilla/5.0 (compatible; LyceeSuggestions/1.0)"},
        )
        r.raise_for_status()

        def generate():
            try:
                for chunk in r.iter_content(chunk_size=16384):
                    if chunk:
                        yield chunk
            finally:
                r.close()

        ct = r.headers.get("content-type") or "audio/mpeg"
        return Response(generate(), content_type=ct, headers={"Cache-Control": "private, max-age=300"})
    except Exception as e:
        app.logger.warning("preview-audio proxy: %s", e)
        return jsonify({"error": "Lecture impossible"}), 502


@app.route("/api/music-poll/preview-audio", methods=["GET"])
def music_poll_preview_audio():
    """Même proxy que l’admin pour la page élève (évite blocages referrer / CORP sur p.scdn.co)."""
    return _spotify_preview_proxy_response()


@app.route("/api/admin/spotify/preview-audio", methods=["GET"])
@admin_required
def admin_spotify_preview_audio():
    """Proxy lecture des extraits MP3 (Spotify CDN) pour l’aperçu admin."""
    return _spotify_preview_proxy_response()


@app.route("/api/admin/llm-credits", methods=["GET"])
@admin_required
def get_llm_credits():
    import llm_engine
    info = llm_engine.get_credits_info()
    info["model"] = llm_engine.OLLAMA_MODEL
    info["available"] = llm_engine.is_available()
    return jsonify(info)


@app.route("/api/admin/llm-credits", methods=["PUT"])
@admin_required
def update_llm_credits():
    import llm_engine
    data = request.get_json()
    max_credits = int(data.get("max_credits", 100))
    period_hours = int(data.get("period_hours", 24))
    set_setting("llm_max_credits", str(max_credits))
    set_setting("llm_credits_period_hours", str(period_hours))
    llm_engine.configure_credits(max_credits, period_hours)
    return jsonify(llm_engine.get_credits_info())


@app.route("/api/admin/llm-credits/reset", methods=["POST"])
@admin_required
def reset_llm_credits():
    import llm_engine
    llm_engine._reset_credits()
    return jsonify(llm_engine.get_credits_info())


@app.route("/api/admin/trace/simulate", methods=["POST"])
@admin_required
def trace_simulate():
    """Simule le traitement d'une suggestion et retourne les étapes (IA principale → vérification)."""
    import json
    import llm_engine

    data = request.get_json() or {}
    text = (data.get("text") or "").strip()
    if not text or len(text) < 5:
        return jsonify({"error": "Texte trop court"}), 400

    locations = _get_locations_list()
    steps = []

    # Étape 1 : IA principale
    main_result = llm_engine.process_suggestion(text)
    if not main_result:
        return jsonify({"error": "IA principale indisponible", "steps": steps}), 503

    steps.append({"step": "main", "label": "IA principale", "result": main_result})

    # Étape 2 : IA vérification (avec exemples de calibration)
    calib_verify = [e.to_dict() for e in CalibrationVerification.query.order_by(CalibrationVerification.created_at.desc()).limit(15).all()]
    verify_result = llm_engine.verify_and_refine(text, main_result, calibration_verify=calib_verify or None) or main_result
    steps.append({"step": "verify", "label": "IA vérification", "result": verify_result})

    # Débat (proportion)
    calib_debat = [e.to_dict() for e in CalibrationDebat.query.order_by(CalibrationDebat.created_at.desc()).limit(20).all()]
    prop_result = llm_engine.analyze_proportion(verify_result.get("title", ""), calibration_debat=calib_debat or None)
    needs_debate = prop_result.get("needs_debate", False) if prop_result else False
    location_id = None
    if locations and verify_result.get("location_name"):
        for loc in locations:
            if loc.get("name") == verify_result["location_name"]:
                location_id = loc.get("id")
                break

    final = {
        "title": verify_result.get("title", ""),
        "category": verify_result.get("category", "Autre"),
        "keywords": verify_result.get("keywords", []),
        "location_id": location_id,
        "location_name": verify_result.get("location_name", ""),
        "needs_debate": needs_debate,
        "ai_proportion": prop_result.get("proportion", 0) if prop_result else 0,
        "ai_feasibility": prop_result.get("feasibility", 0.5) if prop_result else 0.5,
        "ai_cost": prop_result.get("cost", 0.5) if prop_result else 0.5,
    }

    return jsonify({
        "steps": steps,
        "final": final,
        "original": text,
    })


@app.route("/api/admin/trace/feedback", methods=["POST"])
@admin_required
def trace_feedback():
    """Enregistre le feedback (validation ou correction) pour améliorer l'IA."""
    import json

    data = request.get_json() or {}
    original = (data.get("original_text") or "").strip()
    main_result = data.get("main_result") or {}
    verify_result = data.get("verify_result") or {}
    validated = data.get("validated")  # True, False, or None (modifié)
    correction = data.get("correction") or {}

    if not original:
        return jsonify({"error": "original_text requis"}), 400

    fb = TraceFeedback(
        original_text=original,
        main_result=json.dumps(main_result),
        verify_result=json.dumps(verify_result),
        user_validated=validated,
        user_correction=json.dumps(correction) if correction else "",
    )
    db.session.add(fb)
    db.session.commit()

    # Si correction fournie, ajouter à la calibration de l'IA de vérification
    if correction and validated is None:
        title = correction.get("title", "").strip()
        if title and len(title) > 3:
            cv = CalibrationVerification(
                original_text=original,
                main_result=json.dumps(main_result),
                verify_result=json.dumps(verify_result),
                correction=json.dumps(correction),
            )
            db.session.add(cv)
            db.session.commit()

    return jsonify({"success": True, "id": fb.id})


@app.route("/api/admin/calibration-verification", methods=["GET"])
@admin_required
def list_calibration_verification():
    """Liste les exemples de calibration pour l'IA de vérification."""
    examples = CalibrationVerification.query.order_by(CalibrationVerification.created_at.desc()).all()
    return jsonify([e.to_dict() for e in examples])


@app.route("/api/admin/calibration-verification/<int:eid>", methods=["PUT"])
@admin_required
def update_calibration_verification(eid):
    """Met à jour un exemple de calibration."""
    ex = CalibrationVerification.query.get_or_404(eid)
    data = request.get_json() or {}
    correction = data.get("correction") or {}
    if correction:
        ex.correction = json.dumps(correction)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/admin/calibration-verification/<int:eid>", methods=["DELETE"])
@admin_required
def delete_calibration_verification(eid):
    """Supprime un exemple de calibration."""
    ex = CalibrationVerification.query.get_or_404(eid)
    db.session.delete(ex)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/admin/calibration-verification/import", methods=["POST"])
@admin_required
def import_calibration_verification():
    """Importe des exemples depuis un JSON (ChatGPT, etc.)."""
    data = request.get_json() or {}
    examples = data.get("examples") or []
    if not isinstance(examples, list):
        return jsonify({"error": "examples doit être un tableau"}), 400
    imported = 0
    for item in examples:
        orig = (item.get("original_text") or item.get("message_original") or "").strip()
        cor = item.get("correction") or item.get("correction_attendue") or {}
        if not orig or not cor:
            continue
        title = cor.get("title") or cor.get("titre_reformule") or ""
        if not title or len(title) < 3:
            continue
        main = item.get("main_result") or {}
        verify = item.get("verify_result") or {}
        cv = CalibrationVerification(
            original_text=orig,
            main_result=json.dumps(main),
            verify_result=json.dumps(verify),
            correction=json.dumps({
                "title": title,
                "category": cor.get("category") or cor.get("categorie") or "Autre",
                "keywords": cor.get("keywords") or cor.get("mots_cles") or [],
                "location": cor.get("location") or cor.get("lieu") or "",
            }),
        )
        db.session.add(cv)
        imported += 1
    db.session.commit()
    return jsonify({"imported": imported})


@app.route("/api/admin/calibration-verification/prompt", methods=["GET"])
@admin_required
def get_calibration_verification_prompt():
    """Génère un prompt pour ChatGPT afin de créer des exemples de calibration."""
    examples = CalibrationVerification.query.order_by(CalibrationVerification.created_at.desc()).limit(20).all()
    ex_list = []
    for e in examples:
        cor = json.loads(e.correction) if e.correction else {}
        ex_list.append({
            "original_text": e.original_text,
            "correction_attendue": cor,
        })
    ex_ref = json.dumps(ex_list[:5], ensure_ascii=False, indent=2) if ex_list else "[]"
    prompt = f"""Tu es un expert en reformulation de suggestions d'élèves pour une boîte à idées de lycée.

Une IA principale traite les suggestions et produit : titre, catégorie, mots-clés, lieu.
Une IA de VÉRIFICATION doit contrôler ces résultats et les corriger si nécessaire (cohérence, syntaxe, français).

Génère entre 20 et 50 exemples au format JSON suivant. Chaque exemple contient :
- original_text : le message brut de l'élève
- main_result : le résultat (potentiellement incorrect) de l'IA principale (title, category, keywords, location_name)
- correction_attendue : la correction que l'IA de vérification devrait produire (title, category, keywords, location)

Exemples de référence :
{ex_ref}

Réponds UNIQUEMENT avec un JSON valide de ce format :
{{
  "examples": [
    {{
      "original_text": "...",
      "main_result": {{ "title": "...", "category": "...", "keywords": [...], "location_name": "..." }},
      "correction_attendue": {{ "title": "...", "category": "...", "keywords": [...], "location": "..." }}
    }}
  ]
}}"""
    return jsonify({"prompt": prompt})


# --------------- API Display ---------------

def _recalc_suggestion_vote_count(s):
    """Recalcule vote_count (ou vote_for/vote_against) depuis la table Vote."""
    if getattr(s, "needs_debate", False):
        s.vote_for = Vote.query.filter_by(suggestion_id=s.id, vote_type="for").count()
        s.vote_against = Vote.query.filter_by(suggestion_id=s.id, vote_type="against").count()
        s.vote_count = s.vote_for + s.vote_against
    else:
        total = Vote.query.filter_by(suggestion_id=s.id).count()
        s.vote_count = total
        s.vote_for = total
        s.vote_against = 0


@app.route("/api/display/suggestions", methods=["GET"])
def display_suggestions():
    """Suggestions formatted for the TV display (excludes pending)."""
    suggestions = (
        Suggestion.query
        .filter(Suggestion.status != "En attente", Suggestion.status != "Refusée")
        .order_by(Suggestion.vote_count.desc())
        .limit(20)
        .all()
    )
    suggestions = [s for s in suggestions if _terminée_still_visible(s)]
    if suggestions:
        from sqlalchemy import func
        ids = [s.id for s in suggestions]
        for_counts = dict(db.session.query(Vote.suggestion_id, func.count(Vote.id))
            .filter(Vote.suggestion_id.in_(ids), Vote.vote_type == "for")
            .group_by(Vote.suggestion_id).all())
        against_counts = dict(db.session.query(Vote.suggestion_id, func.count(Vote.id))
            .filter(Vote.suggestion_id.in_(ids), Vote.vote_type == "against")
            .group_by(Vote.suggestion_id).all())
        total_counts = dict(db.session.query(Vote.suggestion_id, func.count(Vote.id))
            .filter(Vote.suggestion_id.in_(ids))
            .group_by(Vote.suggestion_id).all())
        for s in suggestions:
            if getattr(s, "needs_debate", False):
                s.vote_for = for_counts.get(s.id, 0)
                s.vote_against = against_counts.get(s.id, 0)
                s.vote_count = s.vote_for + s.vote_against
            else:
                s.vote_count = total_counts.get(s.id, 0)
        db.session.commit()

    max_votes = max((s.vote_count for s in suggestions), default=1)

    result = []
    for s in suggestions:
        d = s.to_dict()
        ratio = s.vote_count / max_votes if max_votes > 0 else 0
        if ratio >= 0.7:
            d["heat"] = "hot"
        elif ratio >= 0.35:
            d["heat"] = "warm"
        else:
            d["heat"] = "cool"
        result.append(d)

    return jsonify(result)


# --------------- API Proposition Officielle CVL ---------------

def _get_active_proposal():
    return OfficialProposal.query.filter_by(active=True).order_by(OfficialProposal.updated_at.desc()).first()


def _recalc_proposal_votes(p):
    """Recalcule vote_for et vote_against depuis la table ProposalVote (robustesse)."""
    for_count = ProposalVote.query.filter_by(proposal_id=p.id, vote_type="for").count()
    against_count = ProposalVote.query.filter_by(proposal_id=p.id, vote_type="against").count()
    p.vote_for = for_count
    p.vote_against = against_count


@app.route("/api/official-proposal", methods=["GET"])
def get_official_proposal():
    """Public: get active official proposal for display/student page."""
    if get_setting("feature_official_proposal_enabled", "true") != "true":
        return jsonify(None)
    p = _get_active_proposal()
    if not p:
        return jsonify(None)
    _recalc_proposal_votes(p)
    db.session.commit()
    d = p.to_dict()
    session_id = get_session_id()
    my_vote = ProposalVote.query.filter_by(proposal_id=p.id, session_id=session_id).first()
    d["my_vote"] = my_vote.vote_type if my_vote else None
    if p.needs_debate:
        args_for = [a.to_dict() for a in p.arguments if a.side == "for" and a.status == "approved"]
        args_against = [a.to_dict() for a in p.arguments if a.side == "against" and a.status == "approved"]
        d["arguments_for"] = args_for
        d["arguments_against"] = args_against
    return jsonify(d)


def _process_argument_background(arg_id: int):
    """Background: LLM processes argument (analyse, réduit, anti-troll, anti-doublon)."""
    def _work():
        with app.app_context():
            try:
                arg = db.session.get(ProposalArgument, arg_id)
                if not arg or arg.status != "pending":
                    return
                proposal = db.session.get(OfficialProposal, arg.proposal_id)
                if not proposal:
                    return

                is_ok, msg = filter_content_quick(arg.original_text)
                if not is_ok:
                    arg.status = "rejected"
                    arg.reject_reason = (msg or "Contenu filtré")[:2000]
                    db.session.commit()
                    _log_activity(
                        "proposal_argument_rejected",
                        f"Débat CVL : argument refusé (filtre) — {arg.side}",
                        detail=arg.reject_reason,
                    )
                    return

                proposal_plain = _strip_proposal_html(proposal.content or "")
                existing = [
                    (a.summary or a.original_text)
                    for a in ProposalArgument.query.filter(
                        ProposalArgument.proposal_id == proposal.id,
                        ProposalArgument.side == arg.side,
                        ProposalArgument.status == "approved",
                        ProposalArgument.id != arg.id,
                    ).all()
                ]
                import llm_engine
                valid, summary = llm_engine.process_argument(
                    proposal_plain, arg.original_text, arg.side, existing_arguments=existing
                )
                if valid and summary:
                    arg.summary = summary
                    arg.status = "approved"
                    db.session.commit()
                    _log_activity(
                        "proposal_argument_accepted",
                        f"Débat CVL : argument accepté ({arg.side}) — {summary[:140]}",
                        detail=summary[:2000],
                    )
                else:
                    arg.status = "rejected"
                    reason = (summary or "").strip() if summary else ""
                    if not reason:
                        reason = "Argument non pertinent, hors-sujet ou redondant"
                    arg.reject_reason = reason[:2000]
                    db.session.commit()
                    _log_activity(
                        "proposal_argument_rejected",
                        f"Débat CVL : argument refusé (IA) — {arg.side}",
                        detail=reason[:2000],
                    )
            except Exception as ex:
                db.session.rollback()
                try:
                    arg2 = db.session.get(ProposalArgument, arg_id)
                    if arg2 and arg2.status == "pending":
                        arg2.status = "rejected"
                        arg2.reject_reason = "Erreur lors de la modération IA."
                        db.session.commit()
                    _log_activity(
                        "proposal_argument_rejected",
                        f"Débat CVL : argument refusé (erreur technique) — id {arg_id}",
                        detail=str(ex)[:2000],
                    )
                except Exception:
                    db.session.rollback()

    threading.Thread(target=_work, daemon=True).start()


@app.route("/api/official-proposal/vote", methods=["POST"])
def vote_official_proposal():
    """Vote for or against the active proposal. Optional argument for debate proposals."""
    if get_setting("feature_official_proposal_enabled", "true") != "true":
        return jsonify({"error": "Proposition officielle désactivée"}), 400
    ip = _client_ip()
    if _ip_rate_exceeded(ip, "vote_cvl", _IP_VOTE_PER_MINUTE, 60):
        return _ip_rate_response()
    p = _get_active_proposal()
    if not p:
        return jsonify({"error": "Aucune proposition active"}), 404

    data = request.get_json() or {}
    vote_type = data.get("vote")
    if vote_type not in ("for", "against"):
        return jsonify({"error": "Vote invalide"}), 400

    argument_text = (data.get("argument") or "").strip()

    if not p.needs_debate and vote_type == "against":
        return jsonify({"error": "Cette proposition ne permet pas de vote contre."}), 400

    session_id = get_session_id()
    existing = ProposalVote.query.filter_by(proposal_id=p.id, session_id=session_id).first()

    if existing:
        if existing.vote_type == vote_type:
            if argument_text and p.needs_debate:
                ok, fmsg = filter_content_quick(argument_text)
                if not ok:
                    return jsonify({"error": fmsg}), 400
                arg = ProposalArgument(
                    proposal_id=p.id, session_id=session_id,
                    side=vote_type, original_text=argument_text, status="pending"
                )
                db.session.add(arg)
                db.session.flush()
                db.session.commit()
                _log_activity(
                    "proposal_argument_submitted",
                    f"Débat CVL : nouvel argument ({vote_type}) — modération IA en cours",
                    detail=argument_text[:2000],
                )
                _process_argument_background(arg.id)
            _recalc_proposal_votes(p)
            db.session.commit()
            return jsonify({
                "vote_for": p.vote_for, "vote_against": p.vote_against,
                "my_vote": vote_type,
                "arguments_for": [a.to_dict() for a in p.arguments if a.side == "for" and a.status == "approved"],
                "arguments_against": [a.to_dict() for a in p.arguments if a.side == "against" and a.status == "approved"],
            })
        existing.vote_type = vote_type
    else:
        db.session.add(ProposalVote(proposal_id=p.id, session_id=session_id, vote_type=vote_type))

    if argument_text and p.needs_debate:
        ok, fmsg = filter_content_quick(argument_text)
        if not ok:
            return jsonify({"error": fmsg}), 400
        arg = ProposalArgument(
            proposal_id=p.id, session_id=session_id,
            side=vote_type, original_text=argument_text, status="pending"
        )
        db.session.add(arg)
        db.session.flush()
        db.session.commit()
        _log_activity(
            "proposal_argument_submitted",
            f"Débat CVL : nouvel argument ({vote_type}) — modération IA en cours",
            detail=argument_text[:2000],
        )
        _process_argument_background(arg.id)

    _recalc_proposal_votes(p)
    db.session.commit()

    resp = {
        "vote_for": p.vote_for, "vote_against": p.vote_against,
        "my_vote": vote_type,
    }
    if p.needs_debate:
        resp["arguments_for"] = [a.to_dict() for a in p.arguments if a.side == "for" and a.status == "approved"]
        resp["arguments_against"] = [a.to_dict() for a in p.arguments if a.side == "against" and a.status == "approved"]
    return jsonify(resp)


@app.route("/api/official-proposal/argument", methods=["POST"])
def add_official_proposal_argument():
    """Ajoute un argument pour/contre sans changer le vote (plusieurs arguments autorisés)."""
    if get_setting("feature_official_proposal_enabled", "true") != "true":
        return jsonify({"error": "Proposition officielle désactivée"}), 400
    ip = _client_ip()
    if _ip_rate_exceeded(ip, "arg_cvl", _IP_ARG_PER_MINUTE, 60):
        return _ip_rate_response()
    p = _get_active_proposal()
    if not p or not p.needs_debate:
        return jsonify({"error": "Aucune proposition en débat active"}), 400
    data = request.get_json() or {}
    side = data.get("side", "for")
    argument_text = (data.get("argument") or "").strip()
    if side not in ("for", "against"):
        return jsonify({"error": "Camp invalide"}), 400
    if len(argument_text) < 5:
        return jsonify({"error": "Argument trop court (5 caractères min.)"}), 400
    ok, msg = filter_content_quick(argument_text)
    if not ok:
        return jsonify({"error": msg}), 400
    session_id = get_session_id()
    my_vote = ProposalVote.query.filter_by(proposal_id=p.id, session_id=session_id).first()
    if not my_vote or my_vote.vote_type != side:
        return jsonify({"error": "Votez d'abord pour ce camp pour ajouter un argument."}), 400
    arg = ProposalArgument(
        proposal_id=p.id, session_id=session_id,
        side=side, original_text=argument_text, status="pending",
    )
    db.session.add(arg)
    db.session.flush()
    db.session.commit()
    _log_activity(
        "proposal_argument_submitted",
        f"Débat CVL : nouvel argument ({side}) — modération IA en cours",
        detail=argument_text[:2000],
    )
    _process_argument_background(arg.id)
    _recalc_proposal_votes(p)
    db.session.commit()
    return jsonify({
        "vote_for": p.vote_for, "vote_against": p.vote_against,
        "arguments_for": [a.to_dict() for a in p.arguments if a.side == "for" and a.status == "approved"],
        "arguments_against": [a.to_dict() for a in p.arguments if a.side == "against" and a.status == "approved"],
    })


# --------------- API Moments Critiques ---------------

def _detect_critical_moments():
    """Detect sudden spikes in suggestions by category in last 24h."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    recent = (
        Suggestion.query
        .filter(Suggestion.created_at >= cutoff, Suggestion.status != "Refusée")
        .all()
    )
    by_category = {}
    for s in recent:
        cat = s.category or "Autre"
        by_category[cat] = by_category.get(cat, 0) + 1

    critical = []
    for cat, count in by_category.items():
        if count >= 3:
            keywords = set()
            for s in recent:
                if (s.category or "Autre") == cat and s.keywords:
                    keywords.update(k.strip().lower() for k in s.keywords.split(",") if k.strip())
            critical.append({
                "category": cat,
                "count": count,
                "keywords": list(keywords)[:5],
            })
    return critical


@app.route("/api/display/critical-moments", methods=["GET"])
def get_critical_moments():
    """Critical moments for display panel."""
    return jsonify(_detect_critical_moments())


@app.route("/api/cvl-official-info", methods=["GET"])
def get_cvl_official_info():
    """Public: informations officielles du CVL à afficher en haut de la page Boîte à Idées."""
    if get_setting("feature_cvl_official_info_enabled", "true") != "true":
        return jsonify([])
    info_list = CvlOfficialInfo.query.filter_by(active=True).order_by(CvlOfficialInfo.display_order).all()
    return jsonify([i.to_dict() for i in info_list])


# --------------- API Admin ---------------

@app.route("/api/admin/suggestions", methods=["GET"])
@admin_required
def admin_list_suggestions():
    suggestions = Suggestion.query.order_by(Suggestion.created_at.desc()).all()
    return jsonify([s.to_dict() for s in suggestions])


@app.route("/api/admin/suggestions/<int:sid>/status", methods=["PUT"])
@admin_required
def update_status(sid):
    suggestion = Suggestion.query.get_or_404(sid)
    data = request.get_json() or {}
    new_status = data.get("status")

    if new_status not in Suggestion.VALID_STATUSES:
        return jsonify({"error": "Statut invalide"}), 400

    suggestion.status = new_status
    if new_status in ("En cours de mise en place", "Terminée"):
        suggestion.completed_at = datetime.now(timezone.utc)
    else:
        suggestion.completed_at = None
    if "reject_reason" in data:
        suggestion.reject_reason = (data.get("reject_reason") or "")[:2000]
    _sync_suggestion_archive(suggestion)
    db.session.commit()
    detail_json = json.dumps(
        {
            "suggestion_id": sid,
            "new_status": new_status,
            "title": (suggestion.title or "")[:200],
        },
        ensure_ascii=False,
    )
    _log_activity("status_changed", f"Admin : suggestion #{sid} → {new_status}", detail=detail_json)
    return jsonify(suggestion.to_dict())


@app.route("/api/admin/suggestions/<int:sid>/add-vote", methods=["POST"])
@admin_required
def admin_add_vote(sid):
    """Dev tool: manually add support votes to a suggestion."""
    suggestion = Suggestion.query.get_or_404(sid)
    data = request.get_json(silent=True) or {}
    count = min(int(data.get("count", 1)), 50)
    for i in range(count):
        fake_session = f"admin-dev-{uuid.uuid4().hex[:8]}"
        vote = Vote(suggestion_id=sid, session_id=fake_session)
        db.session.add(vote)
    db.session.commit()
    suggestion.vote_count = Vote.query.filter_by(suggestion_id=sid).count()
    db.session.commit()
    _maybe_generate_subtitle(suggestion.id)
    return jsonify({"vote_count": suggestion.vote_count})


@app.route("/api/admin/suggestions/<int:sid>/process", methods=["POST"])
@admin_required
def process_suggestion(sid):
    """Run AI reformulation on a pending suggestion."""
    suggestion = Suggestion.query.get_or_404(sid)
    locations = _get_locations_list()
    result = ai.process(suggestion.original_text, locations)

    suggestion.title = result["title"]
    suggestion.keywords = ",".join(result["keywords"])
    suggestion.category = result["category"]
    if result.get("location_id"):
        suggestion.location_id = result["location_id"]
    suggestion.needs_debate = result.get("needs_debate", False)
    suggestion.ai_needs_debate = result.get("needs_debate", False)
    suggestion.ai_proportion = result.get("ai_proportion")
    suggestion.ai_feasibility = result.get("ai_feasibility")
    suggestion.ai_cost = result.get("ai_cost")
    suggestion.status = "En étude"
    db.session.commit()
    _sync_suggestion_archive(suggestion)
    db.session.commit()
    return jsonify(suggestion.to_dict())


@app.route("/api/admin/suggestions/process-pending", methods=["POST"])
@admin_required
def process_all_pending():
    """Batch AI processing of all pending suggestions."""
    pending = Suggestion.query.filter_by(status="En attente").all()
    locations = _get_locations_list()
    count = 0

    for s in pending:
        result = ai.process(s.original_text, locations)
        s.title = result["title"]
        s.keywords = ",".join(result["keywords"])
        s.category = result["category"]
        if result.get("location_id"):
            s.location_id = result["location_id"]
        s.needs_debate = result.get("needs_debate", False)
        s.ai_needs_debate = result.get("needs_debate", False)
        s.ai_proportion = result.get("ai_proportion")
        s.ai_feasibility = result.get("ai_feasibility")
        s.ai_cost = result.get("ai_cost")
        s.status = "En étude"
        count += 1
        _sync_suggestion_archive(s)

    db.session.commit()
    return jsonify({"processed": count})


@app.route("/api/admin/suggestions/<int:sid>", methods=["DELETE"])
@admin_required
def delete_suggestion(sid):
    suggestion = Suggestion.query.get_or_404(sid)
    title_snap = (suggestion.title or "")[:200]
    _sync_suggestion_archive(suggestion)
    row = SuggestionArchive.query.filter_by(suggestion_id=sid).first()
    if row:
        row.deleted_at = datetime.now(timezone.utc)
    db.session.commit()
    db.session.delete(suggestion)
    db.session.commit()
    _log_activity("suggestion_deleted", f"Suggestion #{sid} supprimée par l'admin", detail=title_snap)
    return jsonify({"success": True})


@app.route("/api/admin/suggestions/<int:sid>/history", methods=["GET"])
@admin_required
def admin_suggestion_history(sid):
    """Historique complet d'une suggestion pour affichage modal."""
    suggestion = Suggestion.query.get_or_404(sid)
    return jsonify(suggestion.to_dict())


@app.route("/api/admin/suggestions/<int:sid>/stats", methods=["GET"])
@admin_required
def admin_suggestion_stats(sid):
    """Fiche synthèse : suggestion + métriques (votes, arguments, notes d'importance)."""
    s = Suggestion.query.get_or_404(sid)
    total_votes = Vote.query.filter_by(suggestion_id=sid).count()
    vf = Vote.query.filter_by(suggestion_id=sid, vote_type="for").count()
    va = Vote.query.filter_by(suggestion_id=sid, vote_type="against").count()
    args_all = SuggestionArgument.query.filter_by(suggestion_id=sid).all()
    args_pending = sum(1 for a in args_all if (a.status or "") == "pending")
    imp_rows = SuggestionImportance.query.filter_by(suggestion_id=sid).all()
    imp_levels = [r.level for r in imp_rows if r.level is not None]
    imp_avg = (sum(imp_levels) / len(imp_levels)) if imp_levels else None
    d = s.to_dict()
    return jsonify(
        {
            "suggestion": d,
            "stats": {
                "total_votes": total_votes,
                "votes_for": vf,
                "votes_against": va,
                "arguments_total": len(args_all),
                "arguments_pending": args_pending,
                "importance_ratings_count": len(imp_levels),
                "importance_average_level": round(imp_avg, 2) if imp_avg is not None else None,
            },
        }
    )


@app.route("/api/admin/suggestions/<int:sid>/pdf", methods=["GET"])
@admin_required
def admin_suggestion_pdf(sid):
    """Télécharge un PDF avec l'historique complet de la suggestion."""
    from io import BytesIO
    from pdf_export import build_suggestion_pdf
    suggestion = Suggestion.query.get_or_404(sid)
    pdf_bytes = build_suggestion_pdf(suggestion)
    safe_title = "".join(c for c in (suggestion.title or "")[:30] if c.isalnum() or c in " -_") or "suggestion"
    filename = f"suggestion-{sid}-{safe_title}.pdf"
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/api/admin/stats", methods=["GET"])
@admin_required
def admin_stats():
    total = Suggestion.query.count()

    by_category = {}
    for cat in Suggestion.VALID_CATEGORIES:
        by_category[cat] = Suggestion.query.filter_by(category=cat).count()

    by_status = {}
    for st in Suggestion.VALID_STATUSES:
        by_status[st] = Suggestion.query.filter_by(status=st).count()

    top_suggestions = (
        Suggestion.query
        .order_by(Suggestion.vote_count.desc())
        .limit(10)
        .all()
    )

    recent = (
        Suggestion.query
        .order_by(Suggestion.created_at.desc())
        .limit(10)
        .all()
    )

    # Suggestions créées par mois (12 derniers mois, fuseau UTC)
    from collections import defaultdict

    sug_by_month: dict[str, int] = defaultdict(int)
    twelve_m_ago = datetime.now(timezone.utc) - timedelta(days=370)
    _ym = func.strftime("%Y-%m", Suggestion.created_at)
    for row in (
        db.session.query(_ym, func.count(Suggestion.id))
        .filter(Suggestion.created_at >= twelve_m_ago)
        .group_by(_ym)
        .all()
    ):
        if row[0]:
            sug_by_month[str(row[0])] = int(row[1])
    month_keys = []
    y, m = datetime.now(timezone.utc).year, datetime.now(timezone.utc).month
    for _ in range(12):
        month_keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m < 1:
            m = 12
            y -= 1
    month_keys.reverse()
    suggestions_per_month = [{"month": mk, "count": sug_by_month.get(mk, 0)} for mk in month_keys]

    return jsonify({
        "total": total,
        "by_category": by_category,
        "by_status": by_status,
        "top_voted": [s.to_dict() for s in top_suggestions],
        "recent": [s.to_dict() for s in recent],
        "suggestions_per_month": suggestions_per_month,
    })


@app.route("/api/admin/dev-notes", methods=["GET", "PUT"])
@admin_required
def admin_dev_notes():
    """Bloc notes développement (HTML riche, auto-sauvegardé côté client)."""
    if request.method == "GET":
        return jsonify({"html": get_setting("admin_dev_notes_html", "") or ""})
    data = request.get_json() or {}
    html = data.get("html", "")
    if not isinstance(html, str):
        return jsonify({"error": "Format invalide"}), 400
    if len(html) > 2_000_000:
        return jsonify({"error": "Contenu trop volumineux"}), 400
    set_setting("admin_dev_notes_html", html)
    return jsonify({"ok": True})


@app.route("/api/admin/suggestions/<int:sid>/detail", methods=["GET"])
@admin_required
def admin_suggestion_detail(sid):
    """Archive + fiche live pour modale (historique / Terminée)."""
    arch = SuggestionArchive.query.filter_by(suggestion_id=sid).first()
    live = Suggestion.query.get(sid)
    return jsonify(
        {
            "archive": arch.to_dict() if arch else None,
            "live": live.to_dict() if live else None,
        }
    )


@app.route("/api/admin/engagement-stats", methods=["GET"])
@admin_required
def admin_engagement_stats():
    """Statistiques engagement (cartes spéciales, humeur, devinettes, importance)."""
    day = _paris_today_str()
    mood_rows = db.session.query(DailyMood.mood, func.count(DailyMood.id)).filter(DailyMood.day == day).group_by(DailyMood.mood).all()
    moods_today = {m: c for m, c in mood_rows}

    presence_by_day = []
    base_day = datetime.now(PARIS_TZ).date() if PARIS_TZ else date.today()
    for i in range(14):
        d = (base_day - timedelta(days=i)).isoformat()
        presence_by_day.append({"day": d, "count": DailyPresence.query.filter_by(day=d).count()})

    imp_total = SuggestionImportance.query.count()
    guess_total = EngagementGuess.query.count()
    guess_correct = 0
    for g in EngagementGuess.query.all():
        s = Suggestion.query.get(g.suggestion_id)
        if not s:
            continue
        actual = _popularity_bucket(_suggestion_popularity_pct(s))
        if actual == g.bucket:
            guess_correct += 1

    msg_7d = CommunityMessage.query.filter(
        CommunityMessage.created_at >= datetime.now(timezone.utc) - timedelta(days=7)
    ).count()

    cards_done_today = db.session.query(EngagementCardDone.card_type, func.count(EngagementCardDone.id)).filter(
        EngagementCardDone.day == day
    ).group_by(EngagementCardDone.card_type).all()
    cards_done_by_type = {t: c for t, c in cards_done_today}

    top_hot = (
        Suggestion.query.filter(Suggestion.importance_score > 0)
        .order_by(Suggestion.importance_score.desc())
        .limit(12)
        .all()
    )

    avg_swipes = db.session.query(func.avg(DailySessionActivity.swipe_count)).filter(
        DailySessionActivity.day == day
    ).scalar()
    avg_likes = db.session.query(func.avg(DailySessionActivity.like_count)).filter(
        DailySessionActivity.day == day
    ).scalar()

    from collections import defaultdict

    since_400d = datetime.now(timezone.utc) - timedelta(days=400)
    mood_by_month: dict[str, dict[str, int]] = {}
    mood_by_hour: dict[int, dict[str, int]] = {h: {} for h in range(24)}
    for dm in DailyMood.query.filter(DailyMood.created_at >= since_400d).all():
        ca = dm.created_at
        if ca is None:
            continue
        if ca.tzinfo is None:
            ca = ca.replace(tzinfo=timezone.utc)
        local = ca.astimezone(PARIS_TZ) if PARIS_TZ else ca
        mk = local.strftime("%Y-%m")
        hr = local.hour
        mood_by_month.setdefault(mk, {})
        mood_by_month[mk][dm.mood] = mood_by_month[mk].get(dm.mood, 0) + 1
        mood_by_hour[hr][dm.mood] = mood_by_hour[hr].get(dm.mood, 0) + 1

    _ym_imp = func.strftime("%Y-%m", SuggestionImportance.created_at)
    imp_votes_by_month: dict[str, int] = defaultdict(int)
    for row in (
        db.session.query(_ym_imp, func.count(SuggestionImportance.id))
        .filter(SuggestionImportance.created_at >= since_400d)
        .group_by(_ym_imp)
        .all()
    ):
        if row[0]:
            imp_votes_by_month[str(row[0])] = int(row[1])

    return jsonify({
        "reference": {
            "timezone": "Europe/Paris",
            "day_today": day,
            "hot_threshold": _HOT_IMPORTANCE_THRESHOLD,
            "activity_score_formula": "2 * likes_jour + swipes_jour",
            "popularity_pct_simple": "100 * vote_count / max(distinct_voters_total, 1), plafonné à 100",
            "popularity_pct_debate": "100 * vote_for / (vote_for + vote_against)",
            "percentile": "% d'élèves avec un score d'activité strictement inférieur au tien (jour calendaire Paris si tzdata, sinon serveur).",
        },
        "moods_today": moods_today,
        "presence_by_day": list(reversed(presence_by_day)),
        "importance_votes_total": imp_total,
        "guess_total": guess_total,
        "guess_correct": guess_correct,
        "guess_accuracy_pct": round(100.0 * guess_correct / guess_total, 1) if guess_total else None,
        "community_messages_last_7d": msg_7d,
        "cards_done_today_by_type": cards_done_by_type,
        "avg_swipes_today": round(float(avg_swipes or 0), 2),
        "avg_likes_today": round(float(avg_likes or 0), 2),
        "top_by_importance": [
            {"id": s.id, "title": s.title, "importance_score": float(s.importance_score or 0), "vote_count": s.vote_count}
            for s in top_hot
        ],
        "mood_by_month": mood_by_month,
        "mood_by_hour": {str(h): mood_by_hour[h] for h in range(24)},
        "importance_votes_by_month": dict(imp_votes_by_month),
    })


@app.route("/api/admin/dilemmas", methods=["GET"])
@admin_required
def admin_list_dilemmas():
    rows = Dilemma.query.order_by(Dilemma.scheduled_day.desc()).all()
    return jsonify([d.to_dict() for d in rows])


@app.route("/api/admin/dilemmas", methods=["POST"])
@admin_required
def admin_create_dilemma():
    data = request.get_json() or {}
    title = (data.get("title") or "").strip()
    option_a = (data.get("option_a") or "").strip()
    option_b = (data.get("option_b") or "").strip()
    scheduled_day = (data.get("scheduled_day") or "").strip()
    if len(title) < 3:
        return jsonify({"error": "Titre trop court"}), 400
    if len(option_a) < 2 or len(option_b) < 2:
        return jsonify({"error": "Les deux options sont requises"}), 400
    if len(scheduled_day) != 10:
        return jsonify({"error": "Date invalide (AAAA-MM-JJ)"}), 400
    if Dilemma.query.filter_by(scheduled_day=scheduled_day).first():
        return jsonify({"error": "Un dilemme existe déjà pour ce jour"}), 409
    d = Dilemma(title=title[:220], option_a=option_a[:500], option_b=option_b[:500], scheduled_day=scheduled_day)
    db.session.add(d)
    db.session.commit()
    return jsonify(d.to_dict()), 201


@app.route("/api/admin/dilemmas/<int:did>", methods=["PUT"])
@admin_required
def admin_update_dilemma(did):
    d = Dilemma.query.get_or_404(did)
    data = request.get_json() or {}
    if "title" in data:
        t = (data.get("title") or "").strip()
        if len(t) < 3:
            return jsonify({"error": "Titre trop court"}), 400
        d.title = t[:220]
    if "option_a" in data:
        d.option_a = (data.get("option_a") or "").strip()[:500]
    if "option_b" in data:
        d.option_b = (data.get("option_b") or "").strip()[:500]
    if "scheduled_day" in data:
        nd = (data.get("scheduled_day") or "").strip()
        if len(nd) != 10:
            return jsonify({"error": "Date invalide"}), 400
        other = Dilemma.query.filter(Dilemma.scheduled_day == nd, Dilemma.id != did).first()
        if other:
            return jsonify({"error": "Ce jour est déjà pris"}), 409
        d.scheduled_day = nd
    db.session.commit()
    return jsonify(d.to_dict())


@app.route("/api/admin/dilemmas/<int:did>", methods=["DELETE"])
@admin_required
def admin_delete_dilemma(did):
    d = Dilemma.query.get_or_404(did)
    DilemmaVote.query.filter_by(dilemma_id=did).delete()
    db.session.delete(d)
    db.session.commit()
    return jsonify({"ok": True})


# --------------- API Lieux ---------------

@app.route("/api/admin/locations", methods=["GET"])
@admin_required
def list_locations():
    locations = Location.query.order_by(Location.name).all()
    result = [loc.to_dict(include_placement_ids=True) for loc in locations]
    result.sort(key=lambda x: x["suggestion_count"], reverse=True)
    return jsonify(result)


@app.route("/api/admin/locations", methods=["POST"])
@admin_required
def create_location():
    data = request.get_json()
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Nom requis"}), 400

    if Location.query.filter_by(name=name).first():
        return jsonify({"error": "Ce lieu existe déjà"}), 409

    location = Location(name=name)
    db.session.add(location)
    db.session.commit()
    return jsonify(location.to_dict()), 201


@app.route("/api/admin/locations/<int:lid>", methods=["DELETE"])
@admin_required
def delete_location(lid):
    location = Location.query.get_or_404(lid)
    Suggestion.query.filter_by(location_id=lid).update({"location_id": None})
    db.session.delete(location)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/admin/locations/<int:lid>/placements", methods=["POST"])
@admin_required
def add_placement(lid):
    """Add a sub-location (e.g. salle de dance) to a location (e.g. Batiment B)."""
    Location.query.get_or_404(lid)
    data = request.get_json()
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Nom requis"}), 400

    if Placement.query.filter_by(location_id=lid, name=name).first():
        return jsonify({"error": "Cet emplacement existe déjà"}), 409

    placement = Placement(location_id=lid, name=name)
    db.session.add(placement)
    db.session.commit()
    return jsonify({"id": placement.id, "name": placement.name}), 201


@app.route("/api/admin/placements/<int:pid>", methods=["DELETE"])
@admin_required
def delete_placement(pid):
    placement = Placement.query.get_or_404(pid)
    db.session.delete(placement)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/admin/suggestions/<int:sid>/title", methods=["PUT"])
@admin_required
def update_suggestion_title(sid):
    suggestion = Suggestion.query.get_or_404(sid)
    data = request.get_json()
    title = (data.get("title") or "").strip()
    if not title or len(title) < 3:
        return jsonify({"error": "Titre trop court"}), 400
    suggestion.title = title[:200]
    db.session.commit()
    return jsonify(suggestion.to_dict())


@app.route("/api/admin/suggestions/<int:sid>/subtitle", methods=["PUT"])
@admin_required
def update_suggestion_subtitle(sid):
    """Modifier ou supprimer le sous-titre (ex: corriger une erreur de l'IA)."""
    suggestion = Suggestion.query.get_or_404(sid)
    data = request.get_json() or {}
    suggestion.subtitle = (data.get("subtitle") or "").strip()[:300]
    db.session.commit()
    return jsonify(suggestion.to_dict())


@app.route("/api/admin/suggestions/<int:sid>/location", methods=["PUT"])
@admin_required
def update_suggestion_location(sid):
    suggestion = Suggestion.query.get_or_404(sid)
    data = request.get_json()
    location_id = data.get("location_id")

    if location_id is not None:
        Location.query.get_or_404(location_id)

    suggestion.location_id = location_id
    db.session.commit()
    return jsonify(suggestion.to_dict())


@app.route("/api/admin/suggestions/<int:sid>/needs-debate", methods=["PUT"])
@admin_required
def update_suggestion_needs_debate(sid):
    suggestion = Suggestion.query.get_or_404(sid)
    data = request.get_json() or {}
    if "needs_debate" in data:
        suggestion.needs_debate = bool(data["needs_debate"])
    db.session.commit()
    return jsonify(suggestion.to_dict())


@app.route("/api/admin/suggestions/<int:sid>/recalibrate", methods=["POST"])
@admin_required
def recalibrate_suggestion(sid):
    """Add a corrected suggestion to the calibration dataset."""
    suggestion = Suggestion.query.get_or_404(sid)
    data = request.get_json()

    example = CalibrationExample(
        original_text=suggestion.original_text,
        title=data.get("title", suggestion.title),
        keywords=",".join(data.get("keywords", [])) if isinstance(data.get("keywords"), list) else data.get("keywords", suggestion.keywords),
        category=data.get("category", suggestion.category),
        location=data.get("location", ""),
        status="validated",
    )
    db.session.add(example)

    if data.get("title"):
        suggestion.title = data["title"]
    if data.get("category"):
        suggestion.category = data["category"]
    if data.get("keywords"):
        suggestion.keywords = ",".join(data["keywords"]) if isinstance(data["keywords"], list) else data["keywords"]

    db.session.commit()
    ai.reload_training_data()
    return jsonify({"success": True, "calibration_id": example.id})


# --------------- API Calibration IA ---------------

# --------------- API Calibration Débat ---------------

@app.route("/api/admin/calibration-debat", methods=["GET"])
@admin_required
def list_calibration_debat():
    items = CalibrationDebat.query.order_by(CalibrationDebat.created_at.desc()).all()
    return jsonify([e.to_dict() for e in items])


@app.route("/api/admin/calibration-debat", methods=["POST"])
@admin_required
def create_calibration_debat():
    data = request.get_json()
    proposition = (data.get("proposition") or "").strip()
    if not proposition:
        return jsonify({"error": "Proposition requise"}), 400
    needs_debate = bool(data.get("needs_debate", False))
    ex = CalibrationDebat(proposition=proposition, needs_debate=needs_debate)
    db.session.add(ex)
    db.session.commit()
    return jsonify(ex.to_dict())


@app.route("/api/admin/calibration-debat/<int:eid>", methods=["PUT"])
@admin_required
def update_calibration_debat(eid):
    ex = CalibrationDebat.query.get_or_404(eid)
    data = request.get_json()
    if "proposition" in data:
        ex.proposition = (data["proposition"] or "").strip()
    if "needs_debate" in data:
        ex.needs_debate = bool(data["needs_debate"])
    db.session.commit()
    return jsonify(ex.to_dict())


@app.route("/api/admin/calibration-debat/<int:eid>", methods=["DELETE"])
@admin_required
def delete_calibration_debat(eid):
    ex = CalibrationDebat.query.get_or_404(eid)
    db.session.delete(ex)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/admin/calibration-debat/prompt", methods=["GET"])
@admin_required
def get_calibration_debat_prompt():
    """Génère un prompt pour ChatGPT afin de créer des exemples de calibration débat."""
    ctx = SchoolContext.query.filter_by(key="school_info").first()
    school_info = ctx.value if ctx else "Lycée français (pas de contexte défini)"
    count = request.args.get("count", "40")
    return jsonify({
        "prompt": f"""Tu vas générer des exemples de calibration pour une IA qui décide si une proposition de lycée mérite un DÉBAT (arguments pour ET contre) ou seulement des SOUTIENS.

CONTEXTE : {school_info}

DÉBAT = OUI : propositions où on peut légitimement être pour OU contre (coût, priorité, impact, faisabilité).
Exemples : horaires (décaler cours, récré plus longue), rénovation/travaux, panneaux solaires, changements d'organisation, projets coûteux.

DÉBAT = NON : petites améliorations consensuelles sans vrai enjeu.
Exemples : ketchup à la cantine, micro-ondes, peinture décorative, petits aménagements.

IMPORTANT — SOIS RÉALISTE ET CRU :
- Génère des propositions comme des lycéens en feraient VRAIMENT
- Langage direct, parfois cru : "les toilettes du bat C c'est dégueu", "la cantine c'est immangeable", "le self pue", "c'est la galère en permanence"
- Mélange de ton poli et de ton familier
- Propositions concrètes et spécifiques (pas vagues)
- Inclus des sujets sensibles mais réalistes : horaires, cantine, propreté, bruit, équipements

Génère exactement {count} exemples au format JSON strict :

[
  {{"proposition": "Installer des panneaux solaires sur les toits", "needs_debate": true}},
  {{"proposition": "Rajouter du ketchup et de la mayo au self", "needs_debate": false}},
  {{"proposition": "Les chiottes du bat B sont dégueulasses, faut les refaire", "needs_debate": true}}
]

Règles :
- "proposition" : phrase reformulée/claire (comme après traitement IA), pas le message brut
- "needs_debate" : true si débat, false si soutiens uniquement
- Variété : cantine, infra, vie scolaire, pédagogie, numérique, bien-être
- Réaliste et cru quand c'est pertinent (propreté, nourriture, confort)
- Réponds UNIQUEMENT avec le tableau JSON, rien d'autre"""
    })


@app.route("/api/admin/calibration-debat/import", methods=["POST"])
@admin_required
def import_calibration_debat():
    """Importe un fichier JSON généré par ChatGPT ou autre IA."""
    if "file" not in request.files:
        return jsonify({"error": "Aucun fichier envoyé"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Fichier invalide"}), 400
    raw = file.read().decode("utf-8-sig", errors="replace").strip()
    # Strip markdown code blocks if ChatGPT wrapped the JSON
    if raw.startswith("```"):
        for prefix in ("```json", "```"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):].lstrip("\n")
                break
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0].rstrip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return jsonify({"error": f"JSON invalide : {e}"}), 400
    if not isinstance(data, list):
        return jsonify({"error": "Le JSON doit être un tableau"}), 400
    count = 0
    for item in data:
        if isinstance(item, dict):
            prop = (item.get("proposition") or item.get("proposition_text") or "").strip()
            if not prop or len(prop) < 3:
                continue
            nd = item.get("needs_debate", False)
            if isinstance(nd, str):
                nd = nd.lower() in ("true", "oui", "yes", "1")
            ex = CalibrationDebat(proposition=prop, needs_debate=bool(nd))
            db.session.add(ex)
            count += 1
    db.session.commit()
    return jsonify({"imported": count})


# --------------- API Calibration Détails ---------------

@app.route("/api/admin/calibration-details", methods=["GET"])
@admin_required
def list_calibration_details():
    items = CalibrationDetails.query.order_by(CalibrationDetails.created_at.desc()).all()
    return jsonify([e.to_dict() for e in items])


@app.route("/api/admin/calibration-details", methods=["POST"])
@admin_required
def create_calibration_details():
    data = request.get_json()
    suggestion_text = (data.get("suggestion_text") or data.get("suggestion") or "").strip()
    if not suggestion_text:
        return jsonify({"error": "Texte de suggestion requis"}), 400
    hint_raw = data.get("hint")
    hint = None
    if hint_raw is not None and str(hint_raw).strip().lower() not in ("", "non", "non."):
        hint = str(hint_raw).strip()[:120]
    ex = CalibrationDetails(suggestion_text=suggestion_text, hint=hint)
    db.session.add(ex)
    db.session.commit()
    return jsonify(ex.to_dict())


@app.route("/api/admin/calibration-details/<int:eid>", methods=["PUT"])
@admin_required
def update_calibration_details(eid):
    ex = CalibrationDetails.query.get_or_404(eid)
    data = request.get_json()
    if "suggestion_text" in data:
        ex.suggestion_text = (data["suggestion_text"] or "").strip()
    if "hint" in data:
        h = data["hint"]
        ex.hint = None if (h is None or str(h).strip().lower() in ("", "non", "non.")) else str(h).strip()[:120]
    db.session.commit()
    return jsonify(ex.to_dict())


@app.route("/api/admin/calibration-details/<int:eid>", methods=["DELETE"])
@admin_required
def delete_calibration_details(eid):
    ex = CalibrationDetails.query.get_or_404(eid)
    db.session.delete(ex)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/admin/calibration-details/prompt", methods=["GET"])
@admin_required
def get_calibration_details_prompt():
    """Génère un prompt pour ChatGPT/IA externe : exemples pour calibrer l'IA « détails »."""
    ctx = SchoolContext.query.filter_by(key="school_info").first()
    school_info = ctx.value if ctx else "Lycée français (pas de contexte défini)"
    count = request.args.get("count", "30")
    return jsonify({
        "prompt": f"""Tu génères des exemples de CALIBRATION pour entraîner une IA (ChatGPT, Claude, etc.). L'IA locale utilisera ces exemples en few-shot pour apprendre à distinguer : suggestion précise vs vague, et si vague → quelle question poser.

CONTEXTE : {school_info}

FORMAT OBLIGATOIRE :
Chaque bloc contient une SUGGESTION DE BASE (thème/sujet) et plusieurs EXEMPLES en dessous : reformulations, précisions, versions vagues. Cela donne à l'IA le contexte pour comprendre les variations.

STRUCTURE :
- suggestion_base : la suggestion de référence (suffisamment précise)
- exemples : tableau de variations (reformulations, ajouts de détails, versions vagues)
  - texte : ce qu'un élève pourrait écrire
  - is_precision : true si déjà assez précis (reformulation ou précision de la base), false si vague
  - hint : question courte (max 12 mots) si is_precision=false, sinon null

TYPES D'EXEMPLES SOUS UNE BASE :
1. REFORMULATION (is_precision: true) : même idée, autre formulation
2. PRÉCISION (is_precision: true) : ajoute des DÉTAILS concrets (salle, cause, contexte). IMPORTANT : une précision peut être longue et détaillée — salle + problème + cause = déjà précis.
3. VAGUE (is_precision: false) : manque lieu, problème trop général → hint = question ciblée

EXEMPLE CRUCIAL — PRÉCISION DÉTAILLÉE (is_precision: true) :
"Dans la salle 10 du bat C, y'a une fenêtre qui s'ouvre très fort quand y'a du vent, vu que le loquet est cassé" → PRÉCISION. Salle indiquée + cause (loquet) + contexte (vent). Ne pas demander plus de détails.

Génère exactement {count} blocs au format JSON :

[
  {{
    "suggestion_base": "Réparer les fenêtres cassées du bâtiment C",
    "exemples": [
      {{"texte": "Y'a des fenêtres cassée au batiment c", "is_precision": true, "hint": null}},
      {{"texte": "Une fenêtre brisée dans la salle B205 du bâtiment C", "is_precision": true, "hint": null}},
      {{"texte": "Dans la salle 10 du bat C, y'a une fenêtre qui s'ouvre très fort quand y'a du vent, vu que le loquet est cassé", "is_precision": true, "hint": null}},
      {{"texte": "c'est cassé", "is_precision": false, "hint": "Quel endroit ou équipement ?"}}
    ]
  }},
  {{
    "suggestion_base": "Installer des micro-ondes au self du bâtiment B",
    "exemples": [
      {{"texte": "Des micro-ondes au self", "is_precision": false, "hint": "Quel bâtiment ou étage ?"}},
      {{"texte": "micro ondes a la cantine bat B", "is_precision": true, "hint": null}}
    ]
  }},
  {{
    "suggestion_base": "Améliorer la cantine : plats plus variés et meilleure qualité",
    "exemples": [
      {{"texte": "la cantine c'est nul", "is_precision": false, "hint": "Quel problème concret ?"}},
      {{"texte": "c'est immangeable", "is_precision": false, "hint": "Quel plat ou quel souci ?"}},
      {{"texte": "plus de choix végétariens au self", "is_precision": true, "hint": null}}
    ]
  }}
]

Règles :
- suggestion_base : toujours une suggestion déjà précise (référence du bloc)
- PRÉCISION = salle + cause + contexte OU lieu + problème concret. Ne pas confondre avec "vague" : "Dans la salle 10 du bat C, fenêtre qui s'ouvre avec le vent car loquet cassé" = PRÉCISION (is_precision: true)
- exemples : 2 à 5 variations par bloc (reformulations, précisions détaillées, versions vagues)
- is_precision=true ⟺ hint=null
- hint : question ciblée selon le manque (lieu, problème, produit, fréquence...)
- Variété : cantine, infra, propreté, équipements, horaires
- Langage familier OK pour les exemples
- Réponds UNIQUEMENT avec le tableau JSON, rien d'autre"""
    })


@app.route("/api/admin/calibration-details/import", methods=["POST"])
@admin_required
def import_calibration_details():
    """Importe un fichier JSON généré par ChatGPT ou autre IA."""
    if "file" not in request.files:
        return jsonify({"error": "Aucun fichier envoyé"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Fichier invalide"}), 400
    raw = file.read().decode("utf-8-sig", errors="replace").strip()
    if raw.startswith("```"):
        for prefix in ("```json", "```"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):].lstrip("\n")
                break
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0].rstrip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return jsonify({"error": f"JSON invalide : {e}"}), 400
    if not isinstance(data, list):
        return jsonify({"error": "Le JSON doit être un tableau"}), 400
    count = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        # Nouveau format : suggestion_base + exemples
        if "suggestion_base" in item and "exemples" in item:
            base = str(item.get("suggestion_base") or "").strip()[:300]
            for ex_item in item.get("exemples") or []:
                if not isinstance(ex_item, dict):
                    continue
                text = (ex_item.get("texte") or ex_item.get("text") or "").strip()
                if not text or len(text) < 3:
                    continue
                h = ex_item.get("hint")
                hint = None
                if h is not None and str(h).strip().lower() not in ("", "non", "non."):
                    hint = str(h).strip()[:120]
                ex = CalibrationDetails(suggestion_text=text, hint=hint, suggestion_base=base if base else None)
                db.session.add(ex)
                count += 1
        else:
            # Ancien format plat : suggestion_text, hint
            text = (item.get("suggestion_text") or item.get("suggestion") or item.get("texte") or item.get("text") or "").strip()
            if not text or len(text) < 3:
                continue
            h = item.get("hint")
            hint = None
            if h is not None and str(h).strip().lower() not in ("", "non", "non."):
                hint = str(h).strip()[:120]
            ex = CalibrationDetails(suggestion_text=text, hint=hint)
            db.session.add(ex)
            count += 1
    db.session.commit()
    return jsonify({"imported": count})


# --------------- API Calibration Rapport ---------------

@app.route("/api/admin/calibration-rapport", methods=["GET"])
@admin_required
def list_calibration_rapport():
    items = CalibrationRapport.query.order_by(CalibrationRapport.created_at.desc()).all()
    return jsonify([e.to_dict() for e in items])


@app.route("/api/admin/calibration-rapport", methods=["POST"])
@admin_required
def create_calibration_rapport():
    data = request.get_json()
    existing_text = (data.get("existing_text") or data.get("existing") or "").strip()
    new_text = (data.get("new_text") or data.get("new") or "").strip()
    if not existing_text or not new_text:
        return jsonify({"error": "existing_text et new_text requis"}), 400
    has_rapport = bool(data.get("has_rapport", False))
    is_precision = bool(data.get("is_precision", False))
    ex = CalibrationRapport(existing_text=existing_text, new_text=new_text, has_rapport=has_rapport, is_precision=is_precision)
    db.session.add(ex)
    db.session.commit()
    return jsonify(ex.to_dict())


@app.route("/api/admin/calibration-rapport/<int:eid>", methods=["PUT"])
@admin_required
def update_calibration_rapport(eid):
    ex = CalibrationRapport.query.get_or_404(eid)
    data = request.get_json()
    if "existing_text" in data:
        ex.existing_text = (data["existing_text"] or "").strip()
    if "new_text" in data:
        ex.new_text = (data["new_text"] or "").strip()
    if "has_rapport" in data:
        ex.has_rapport = bool(data["has_rapport"])
    if "is_precision" in data:
        ex.is_precision = bool(data["is_precision"])
    db.session.commit()
    return jsonify(ex.to_dict())


@app.route("/api/admin/calibration-rapport/<int:eid>", methods=["DELETE"])
@admin_required
def delete_calibration_rapport(eid):
    ex = CalibrationRapport.query.get_or_404(eid)
    db.session.delete(ex)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/admin/calibration-rapport/prompt", methods=["GET"])
@admin_required
def get_calibration_rapport_prompt():
    """Prompt pour ChatGPT/IA externe : rapport et précision entre deux suggestions."""
    ctx = SchoolContext.query.filter_by(key="school_info").first()
    school_info = ctx.value if ctx else "Lycée français"
    count = request.args.get("count", "25")
    return jsonify({
        "prompt": f"""Tu génères des exemples de calibration pour une IA qui décide, entre deux suggestions d'élèves de lycée :
1) RAPPORT : la nouvelle a-t-elle un lien avec l'existante (même sujet, même problème) ?
2) PRÉCISION : si rapport=oui, la nouvelle apporte-t-elle des DÉTAILS en plus (lieu précis, salle, fréquence) ou est-ce juste la même idée reformulée ?

CONTEXTE : {school_info}

RAPPORT=OUI : même sujet (ex: fenêtres bat C + fenêtres salle C203 = oui)
RAPPORT=NON : sujet différent (ex: fenêtres bat C + micro-ondes au self = non)

PRÉCISION=OUI : la nouvelle ajoute des détails (salle, cause, contexte). Ex: "fenêtres bat C" + "surtout salle C203" = précision. Ex: "fenêtres bat C" + "Dans la salle 10 du bat C, fenêtre qui s'ouvre avec le vent car loquet cassé" = précision (salle + cause + contexte).
PRÉCISION=NON : même idée, pas de détail en plus (ex: "fenêtres bat C" + "les fenêtres du bat C sont cassées" = pas précision, juste reformulé)

Génère exactement {count} exemples au format JSON :

[
  {{"existing_text": "Réparer les fenêtres cassées du bâtiment C", "new_text": "Une fenêtre brisée dans la salle B205 du bâtiment C", "has_rapport": true, "is_precision": true}},
  {{"existing_text": "Réparer les fenêtres cassées du bâtiment C", "new_text": "Dans la salle 10 du bat C, y'a une fenêtre qui s'ouvre très fort quand y'a du vent, vu que le loquet est cassé", "has_rapport": true, "is_precision": true}},
  {{"existing_text": "Les fenêtres du bâtiment C sont cassées", "new_text": "Les fenêtres du bat C sont cassées", "has_rapport": true, "is_precision": false}},
  {{"existing_text": "Fenêtres cassées bat C", "new_text": "Installer des micro-ondes au self", "has_rapport": false, "is_precision": false}}
]

Règles : existing_text = suggestion existante, new_text = nouvelle soumise. has_rapport et is_precision = booléens.
Réponds UNIQUEMENT avec le tableau JSON."""
    })


@app.route("/api/admin/calibration-rapport/import", methods=["POST"])
@admin_required
def import_calibration_rapport():
    if "file" not in request.files:
        return jsonify({"error": "Aucun fichier envoyé"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Fichier invalide"}), 400
    raw = file.read().decode("utf-8-sig", errors="replace").strip()
    if raw.startswith("```"):
        for prefix in ("```json", "```"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):].lstrip("\n")
                break
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0].rstrip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return jsonify({"error": f"JSON invalide : {e}"}), 400
    if not isinstance(data, list):
        return jsonify({"error": "Le JSON doit être un tableau"}), 400
    count = 0
    for item in data:
        if isinstance(item, dict):
            existing = (item.get("existing_text") or item.get("existing") or "").strip()
            new = (item.get("new_text") or item.get("new") or "").strip()
            if not existing or not new:
                continue
            hr = item.get("has_rapport", False)
            ip = item.get("is_precision", False)
            if isinstance(hr, str):
                hr = hr.lower() in ("true", "oui", "yes", "1")
            if isinstance(ip, str):
                ip = ip.lower() in ("true", "oui", "yes", "1")
            ex = CalibrationRapport(existing_text=existing, new_text=new, has_rapport=bool(hr), is_precision=bool(ip))
            db.session.add(ex)
            count += 1
    db.session.commit()
    return jsonify({"imported": count})


# --------------- API Calibration IA ---------------

@app.route("/api/admin/calibration", methods=["GET"])
@admin_required
def list_calibration():
    status_filter = request.args.get("status")
    batch_filter = request.args.get("batch")
    query = CalibrationExample.query.order_by(CalibrationExample.created_at.desc())
    if status_filter:
        query = query.filter_by(status=status_filter)
    if batch_filter:
        query = query.filter_by(batch_id=batch_filter)
    examples = query.all()
    return jsonify([e.to_dict() for e in examples])


@app.route("/api/admin/calibration/batches", methods=["GET"])
@admin_required
def list_calibration_batches():
    rows = db.session.query(
        CalibrationExample.batch_id,
        db.func.count(CalibrationExample.id),
        db.func.min(CalibrationExample.created_at),
    ).filter(CalibrationExample.batch_id != "").group_by(
        CalibrationExample.batch_id
    ).order_by(db.func.min(CalibrationExample.created_at).desc()).all()

    result = []
    for batch_id, count, created in rows:
        result.append({
            "batch_id": batch_id,
            "count": count,
            "created_at": created.isoformat() if created else None,
        })
    return jsonify(result)


@app.route("/api/admin/calibration/stats", methods=["GET"])
@admin_required
def calibration_stats():
    total = CalibrationExample.query.count()
    pending = CalibrationExample.query.filter_by(status="pending").count()
    processed = CalibrationExample.query.filter_by(status="processed").count()
    validated = CalibrationExample.query.filter_by(status="validated").count()
    rejected = CalibrationExample.query.filter_by(status="rejected").count()
    return jsonify({
        "total": total,
        "pending": pending,
        "processed": processed,
        "validated": validated,
        "rejected": rejected,
    })


@app.route("/api/admin/calibration/import", methods=["POST"])
@admin_required
def import_calibration():
    """Import training messages from TXT, CSV or JSON file.
    JSON files with titre_reformule/title are imported as pre-trained."""
    if "file" not in request.files:
        return jsonify({"error": "Aucun fichier envoyé"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Fichier invalide"}), 400

    batch_id = str(uuid.uuid4())[:8]
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    raw = file.read().decode("utf-8-sig", errors="replace")

    count = 0
    pre_trained = 0

    if ext == "json":
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, str):
                        ex = CalibrationExample(original_text=item.strip(), status="pending", batch_id=batch_id)
                        db.session.add(ex)
                        count += 1
                    elif isinstance(item, dict):
                        original = item.get("message_original", item.get("message", item.get("text", item.get("original_text", "")))).strip()
                        if not original or len(original) < 3:
                            continue
                        title = item.get("titre_reformule", item.get("title", ""))
                        is_trained = bool(title)
                        kw = item.get("mots_cles", item.get("keywords", []))
                        kw_str = ",".join(kw) if isinstance(kw, list) else kw
                        fw = item.get("forbidden_words", [])
                        fw_str = ",".join(fw) if isinstance(fw, list) else fw
                        status = item.get("status", "validated" if is_trained else "pending")
                        if status not in CalibrationExample.VALID_STATUSES:
                            status = "validated" if is_trained else "pending"

                        ex = CalibrationExample(
                            original_text=original,
                            title=title or "",
                            keywords=kw_str or "",
                            category=item.get("categorie", item.get("category", "")),
                            location=item.get("lieu", item.get("location", "")),
                            status=status,
                            batch_id=batch_id,
                            forbidden_words=fw_str or "",
                        )
                        db.session.add(ex)
                        count += 1
                        if is_trained:
                            pre_trained += 1
        except json.JSONDecodeError:
            return jsonify({"error": "JSON invalide"}), 400

    elif ext == "csv":
        reader = csv.reader(io.StringIO(raw))
        for row in reader:
            if row and row[0].strip() and len(row[0].strip()) > 3:
                db.session.add(CalibrationExample(original_text=row[0].strip(), status="pending", batch_id=batch_id))
                count += 1
    else:
        for line in raw.splitlines():
            line = line.strip().strip('"').strip("'")
            if line and len(line) > 3:
                db.session.add(CalibrationExample(original_text=line, status="pending", batch_id=batch_id))
                count += 1

    if count == 0:
        return jsonify({"error": "Aucun message trouvé dans le fichier"}), 400

    db.session.commit()
    if pre_trained > 0:
        ai.reload_training_data()
    return jsonify({"imported": count, "pre_trained": pre_trained, "batch_id": batch_id})


@app.route("/api/admin/calibration/process-all", methods=["POST"])
@admin_required
def process_all_calibration():
    """Run AI analysis on pending calibration examples, optionally filtered by batch."""
    data = request.get_json(silent=True) or {}
    batch_filter = data.get("batch_id", "")

    query = CalibrationExample.query.filter_by(status="pending")
    if batch_filter:
        query = query.filter_by(batch_id=batch_filter)
    examples = query.all()

    locations = _get_locations_list()
    processed_count = 0

    for ex in examples:
        result = ai.process(ex.original_text, locations)
        ex.title = result["title"]
        ex.keywords = ",".join(result["keywords"])
        ex.category = result["category"]

        if result.get("location_id"):
            loc = Location.query.get(result["location_id"])
            ex.location = loc.name if loc else ""

        ex.status = "processed"
        processed_count += 1

    db.session.commit()
    return jsonify({"processed": processed_count})


@app.route("/api/admin/calibration/<int:eid>/process", methods=["POST"])
@admin_required
def process_single_calibration(eid):
    example = CalibrationExample.query.get_or_404(eid)
    locations = _get_locations_list()
    result = ai.process(example.original_text, locations)

    example.title = result["title"]
    example.keywords = ",".join(result["keywords"])
    example.category = result["category"]

    if result.get("location_id"):
        loc = Location.query.get(result["location_id"])
        example.location = loc.name if loc else ""

    example.status = "processed"
    db.session.commit()
    return jsonify(example.to_dict())


@app.route("/api/admin/calibration/<int:eid>", methods=["PUT"])
@admin_required
def update_calibration(eid):
    example = CalibrationExample.query.get_or_404(eid)
    data = request.get_json()

    if "title" in data:
        example.title = data["title"]
    if "keywords" in data:
        example.keywords = ",".join(data["keywords"]) if isinstance(data["keywords"], list) else data["keywords"]
    if "category" in data:
        example.category = data["category"]
    if "location" in data:
        example.location = data["location"]

    db.session.commit()
    return jsonify(example.to_dict())


@app.route("/api/admin/calibration/<int:eid>/validate", methods=["POST"])
@admin_required
def validate_calibration(eid):
    example = CalibrationExample.query.get_or_404(eid)
    data = request.get_json(silent=True) or {}
    if data.get("forbidden_words"):
        fw = data["forbidden_words"]
        example.forbidden_words = ",".join(fw) if isinstance(fw, list) else fw
    example.status = "validated"
    db.session.commit()
    ai.reload_training_data()
    return jsonify(example.to_dict())


@app.route("/api/admin/calibration/<int:eid>/reject", methods=["POST"])
@admin_required
def reject_calibration(eid):
    example = CalibrationExample.query.get_or_404(eid)
    data = request.get_json(silent=True) or {}
    example.status = "rejected"
    if data.get("forbidden_words"):
        fw = data["forbidden_words"]
        example.forbidden_words = ",".join(fw) if isinstance(fw, list) else fw
    db.session.commit()
    ai.reload_training_data()
    return jsonify(example.to_dict())


@app.route("/api/admin/calibration/<int:eid>/split", methods=["POST"])
@admin_required
def split_calibration(eid):
    """Split one example into multiple distinct suggestions."""
    example = CalibrationExample.query.get_or_404(eid)
    data = request.get_json()
    parts = data.get("parts", [])

    if len(parts) < 2:
        return jsonify({"error": "Il faut au moins 2 parties"}), 400

    locations = _get_locations_list()
    created = []

    for part_text in parts:
        part_text = part_text.strip()
        if not part_text:
            continue
        result = ai.process(part_text, locations)
        loc_name = ""
        if result.get("location_id"):
            loc = Location.query.get(result["location_id"])
            loc_name = loc.name if loc else ""

        new_ex = CalibrationExample(
            original_text=part_text,
            title=result["title"],
            keywords=",".join(result["keywords"]),
            category=result["category"],
            location=loc_name,
            status="processed",
        )
        db.session.add(new_ex)
        db.session.flush()
        created.append(new_ex.to_dict())

    db.session.delete(example)
    db.session.commit()
    return jsonify({"created": created})


@app.route("/api/admin/calibration/<int:eid>", methods=["DELETE"])
@admin_required
def delete_calibration(eid):
    example = CalibrationExample.query.get_or_404(eid)
    db.session.delete(example)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/admin/calibration/export", methods=["GET"])
@admin_required
def export_calibration():
    examples = CalibrationExample.query.filter_by(status="validated").all()
    data = [e.to_training_dict() for e in examples]
    return jsonify(data)


@app.route("/api/admin/calibration/validate-all-processed", methods=["POST"])
@admin_required
def validate_all_processed():
    data = request.get_json(silent=True) or {}
    batch_filter = data.get("batch_id", "")
    query = CalibrationExample.query.filter_by(status="processed")
    if batch_filter:
        query = query.filter_by(batch_id=batch_filter)
    examples = query.all()
    count = 0
    for ex in examples:
        if ex.title and ex.category:
            ex.status = "validated"
            count += 1
    db.session.commit()
    ai.reload_training_data()
    return jsonify({"validated": count})


# --------------- API Contexte IA ---------------

@app.route("/api/admin/context", methods=["GET"])
@admin_required
def get_context():
    ctx = SchoolContext.query.filter_by(key="school_info").first()
    return jsonify({"context": ctx.value if ctx else ""})


@app.route("/api/admin/context", methods=["PUT"])
@admin_required
def update_context():
    data = request.get_json()
    value = data.get("context", "")
    ctx = SchoolContext.query.filter_by(key="school_info").first()
    if ctx:
        ctx.value = value
    else:
        ctx = SchoolContext(key="school_info", value=value)
        db.session.add(ctx)
    db.session.commit()
    ai.reload_context()
    return jsonify({"success": True})


@app.route("/api/admin/calibration/prompts", methods=["GET"])
@admin_required
def get_all_prompts():
    """Retourne tous les prompts IA pour la page Contexte (copiables)."""
    import llm_engine
    prompts = {
        "relevance": {"name": "Pertinence (accepter/refuser)", "prompt": llm_engine.RELEVANCE_PROMPT},
        "reformulate": {"name": "Reformulation", "prompt": llm_engine.REFORMULATE_PROMPT},
        "category": {"name": "Catégorisation", "prompt": llm_engine.CATEGORY_PROMPT},
        "keywords": {"name": "Mots-clés", "prompt": llm_engine.KEYWORDS_PROMPT},
        "duplicate": {"name": "Détection doublon", "prompt": llm_engine.DUPLICATE_CHECK_PROMPT},
        "process": {"name": "Traitement complet (all-in-one)", "prompt": llm_engine.PROCESS_PROMPT},
        "proportion": {"name": "Impact / Débat", "prompt": llm_engine.PROPORTION_PROMPT},
        "argument": {"name": "Argument pour/contre", "prompt": llm_engine.ARGUMENT_PROMPT},
        "subtitle": {"name": "Sous-titre (regroupement)", "prompt": llm_engine.SUBTITLE_PROMPT},
        "verify": {"name": "Vérification (cohérence, syntaxe, français)", "prompt": llm_engine.VERIFY_PROMPT},
    }
    return jsonify(prompts)


@app.route("/api/admin/calibration/prompt", methods=["GET"])
@admin_required
def get_calibration_prompt():
    """Generate a prompt to send to ChatGPT for generating training data."""
    ctx = SchoolContext.query.filter_by(key="school_info").first()
    school_info = ctx.value if ctx else "Lycée (pas de contexte défini)"

    locations = [l.name for l in Location.query.all()]
    loc_text = ", ".join(locations) if locations else "Aucun lieu configuré"

    count = request.args.get("count", "50")

    prompt = f"""Tu vas générer des données d'entraînement pour une IA de traitement de suggestions d'élèves de lycée.

CONTEXTE DE L'ÉTABLISSEMENT :
{school_info}

CATÉGORIES DISPONIBLES : Cantine, Infrastructure, Vie scolaire, Pédagogie, Numérique, Bien-être

LIEUX CONNUS : {loc_text}

INSTRUCTIONS :
Génère exactement {count} messages simulés d'élèves. Les messages doivent être TRÈS variés et réalistes :

1. MESSAGES NORMAUX (environ 60%) :
   - Langage familier d'adolescent ("ça serait trop bien si...", "svp mettez...", "c relou que...")
   - Fautes d'orthographe volontaires ("sa serait bien", "y'a pas de...", "ya tro de bruit")
   - Abréviations SMS (stp, mtn, tkt, jsp, bg, mdr)
   - Mélange de niveaux de langue

2. MESSAGES AVEC INSULTES/VULGARITÉS CACHÉES (environ 20%) - C'EST CRUCIAL POUR ENTRAÎNER LE FILTRE :
   - Leetspeak : $@lope, p*tain, m3rd3, c0nnard, f!ls d3 p*t3
   - Espaces dans les mots : s a l o p e, p u t a i n
   - Caractères spéciaux : c.o.n.n.a.r.d, m€rde, b!te
   - Inversions : epolAs, drannoc
   - Mots tronqués : put..., sal..., enc...
   - Mots collés avec le message : "cestdlamerde au self"
   - Variantes phonétiques : "niktamer", "tagel"
   ⚠️ Génère ces exemples car ils sont NÉCESSAIRES pour entraîner le filtre anti-insultes.

3. SPAM (environ 10%) :
   - Répétitions : "aaaaaaaaaaaa", "lol lol lol lol"
   - Majuscules : "JE VEUX DES FRITES"
   - Messages trop courts : "ok", "lol"
   - URLs ou liens

4. MESSAGES HORS-SUJET (environ 10%) :
   - Qui n'ont rien à voir avec le lycée
   - Messages personnels

FORMAT DE SORTIE (JSON STRICT, prêt à importer) :
[
  {{
    "message_original": "le message brut de l'élève",
    "titre_reformule": "Version formelle et claire (vide si rejected)",
    "mots_cles": ["mot1", "mot2"],
    "categorie": "Une des catégories ci-dessus (vide si rejected)",
    "lieu": "Le lieu si mentionné, sinon vide",
    "status": "validated ou rejected",
    "forbidden_words": ["mot_interdit1"] si des mots doivent être bloqués par le filtre, sinon []
  }}
]

RÈGLES :
- Les messages avec "status": "rejected" sont des exemples de ce que l'IA doit REFUSER
- Les messages avec "status": "validated" sont des exemples de ce que l'IA doit ACCEPTER
- Pour les rejected avec insultes, remplis "forbidden_words" avec les mots à bloquer (version NETTOYÉE, ex: "salope" pas "$@lope")
- Pour les validated, "titre_reformule" doit être propre, formel, concis
- Utilise les lieux réels du lycée dans les messages
- Varie les catégories de manière réaliste

Génère EXACTEMENT {count} exemples en JSON VALIDE."""

    return jsonify({"prompt": prompt})


@app.route("/api/admin/calibration/ai-suggest", methods=["POST"])
@admin_required
def ai_suggest_calibration():
    """Use local LLM to generate a few training examples."""
    if not ai.llm_available():
        return jsonify({"error": "LLM local non disponible"}), 503

    import llm_engine

    ctx = SchoolContext.query.filter_by(key="school_info").first()
    school_info = ctx.value if ctx else ""
    locations = [l.name for l in Location.query.all()]

    prompt = f"""Génère 5 messages réalistes d'élèves de lycée qui proposent des améliorations.
{f'Contexte: {school_info}' if school_info else ''}
{f'Lieux: {", ".join(locations)}' if locations else ''}

Chaque message doit être comme un vrai élève l'écrirait (familier, fautes, etc.).
Réponds avec UNIQUEMENT les 5 messages, un par ligne, sans numérotation."""

    result = llm_engine._call_ollama(prompt, temperature=0.8)
    if not result:
        return jsonify({"error": "Pas de réponse du LLM"}), 503

    lines = [l.strip().strip('"').strip("- ").strip("•").strip() for l in result.strip().splitlines()]
    messages = [l for l in lines if l and len(l) > 5 and not l[0].isdigit()][:5]

    if not messages:
        return jsonify({"error": "Aucun message généré"}), 500

    batch_id = "ai-" + str(uuid.uuid4())[:6]
    created = []
    for msg in messages:
        ex = CalibrationExample(original_text=msg, status="pending", batch_id=batch_id)
        db.session.add(ex)
        db.session.flush()
        created.append(ex.to_dict())

    db.session.commit()
    return jsonify({"created": created, "batch_id": batch_id})


@app.route("/api/admin/calibration/generate-json", methods=["POST"])
@admin_required
def calibration_generate_json():
    """Génère du JSON de calibration via l'IA locale et l'importe automatiquement."""
    if not ai.llm_available():
        return jsonify({"error": "LLM local non disponible"}), 503

    import json
    import llm_engine

    data = request.get_json() or {}
    count = min(int(data.get("count", 10)), 30)

    ctx = SchoolContext.query.filter_by(key="school_info").first()
    school_info = ctx.value if ctx else ""
    locations = [l.name for l in Location.query.all()]

    prompt = f"""Génère exactement {count} exemples de calibration au format JSON pour une IA de suggestions lycée.

CONTEXTE : {school_info or "Lycée français"}
LIEUX : {", ".join(locations) if locations else "Aucun"}

Format de sortie (JSON valide, tableau d'objets) :
[
  {{"message_original": "texte brut élève", "titre_reformule": "Titre clair", "mots_cles": ["mot1","mot2"], "categorie": "Cantine|Infrastructure|Vie scolaire|Pédagogie|Numérique|Bien-être|Autre", "lieu": "nom ou vide", "status": "validated ou rejected", "forbidden_words": []}}
]

Règles :
- 70% validated (suggestions acceptables), 30% rejected (spam, insultes, hors-sujet)
- message_original : langage familier, fautes, SMS
- titre_reformule : vide si rejected, sinon phrase claire
- Pour rejected avec insultes : remplir forbidden_words
- Réponds UNIQUEMENT avec le JSON, rien d'autre."""

    result = llm_engine._call_ollama(prompt, temperature=0.7, num_predict=2000, timeout=60)
    if not result:
        return jsonify({"error": "Pas de réponse du LLM"}), 503

    # Extraire le JSON (parfois entouré de markdown)
    raw = result.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        examples = json.loads(raw)
        if not isinstance(examples, list):
            examples = [examples]
    except json.JSONDecodeError as e:
        return jsonify({"error": f"JSON invalide : {str(e)[:100]}", "raw": raw[:500]}), 400

    batch_id = "ai-json-" + str(uuid.uuid4())[:6]
    created = []
    for ex in examples[:count]:
        if not isinstance(ex, dict):
            continue
        orig = ex.get("message_original", "").strip()
        if not orig or len(orig) < 3:
            continue
        title = ex.get("titre_reformule", "").strip()
        status = "validated" if ex.get("status") == "validated" else "rejected"
        cat = ex.get("categorie", "Autre")
        kws = ex.get("mots_cles", [])
        loc = ex.get("lieu", "")
        fw = ex.get("forbidden_words", [])
        fw_str = ",".join(fw) if isinstance(fw, list) else str(fw)

        calib = CalibrationExample(
            original_text=orig,
            title=title or orig[:100],
            keywords=",".join(kws) if kws else "",
            category=cat if cat in Suggestion.VALID_CATEGORIES else "Autre",
            location=loc,
            status=status,
            batch_id=batch_id,
            forbidden_words=fw_str,
        )
        db.session.add(calib)
        db.session.flush()
        created.append(calib.to_dict())

    db.session.commit()
    return jsonify({"created": created, "batch_id": batch_id, "count": len(created)})


# --------------- API Admin Proposition Officielle ---------------

@app.route("/api/admin/official-proposal", methods=["GET"])
@admin_required
def admin_get_official_proposal():
    """Retourne la proposition (par id si fourni, sinon la plus récente)."""
    pid = request.args.get("id", type=int)
    if pid:
        p = OfficialProposal.query.get(pid)
    else:
        p = OfficialProposal.query.order_by(OfficialProposal.id.desc()).first()
    if not p:
        return jsonify(None)
    d = p.to_dict()
    if p.needs_debate:
        d["arguments_for"] = [a.to_dict() for a in p.arguments if a.side == "for" and a.status == "approved"]
        d["arguments_against"] = [a.to_dict() for a in p.arguments if a.side == "against" and a.status == "approved"]
    return jsonify(d)


def _analyze_proportion_background(proposal_id: int):
    """Background: AI analyzes impact, feasibility, cost. Admin peut forcer needs_debate manuellement."""
    def _work():
        with app.app_context():
            p = OfficialProposal.query.get(proposal_id)
            if not p or not p.content:
                return
            import llm_engine
            calib_debat = [e.to_dict() for e in CalibrationDebat.query.order_by(CalibrationDebat.created_at.desc()).limit(20).all()]
            result = llm_engine.analyze_proportion(p.content, calibration_debat=calib_debat or None)
            if result is not None:
                p.proportion = result.get("proportion", 0.0)
                p.feasibility = result.get("feasibility", 0.5)
                p.cost = result.get("cost", 0.5)
                db.session.commit()

    threading.Thread(target=_work, daemon=True).start()


@app.route("/api/admin/official-proposal", methods=["PUT"])
@admin_required
def admin_update_official_proposal():
    data = request.get_json() or {}
    pid = data.get("id")
    pid = int(pid) if pid is not None and str(pid).isdigit() else None
    if pid:
        p = OfficialProposal.query.get(pid)
    else:
        p = OfficialProposal.query.order_by(OfficialProposal.id.desc()).first()
    if not p:
        p = OfficialProposal()
        db.session.add(p)
        db.session.flush()

    if "content" in data:
        p.content = data["content"]
        _analyze_proportion_background(p.id)
    if "status" in data and data["status"] in OfficialProposal.VALID_STATUSES:
        p.status = data["status"]
    if "needs_debate" in data:
        p.needs_debate = bool(data["needs_debate"])
    db.session.commit()
    return jsonify(p.to_dict())


@app.route("/api/admin/official-proposal/publish", methods=["POST"])
@admin_required
def admin_publish_official_proposal():
    data = request.get_json() or {}
    pid = data.get("id")
    pid = int(pid) if pid is not None and str(pid).isdigit() else None
    if pid:
        p = OfficialProposal.query.get(pid)
    else:
        p = OfficialProposal.query.order_by(OfficialProposal.id.desc()).first()
    if not p:
        p = OfficialProposal()
        db.session.add(p)
        db.session.flush()
    p.active = True
    p.published_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(p.to_dict())


@app.route("/api/admin/official-proposals", methods=["GET"])
@admin_required
def admin_list_official_proposals():
    """Liste toutes les propositions (pour le menu sélectif)."""
    proposals = OfficialProposal.query.order_by(OfficialProposal.id.desc()).limit(50).all()
    return jsonify([{
        "id": p.id,
        "content_preview": (p.content or "")[:80] + ("…" if len(p.content or "") > 80 else ""),
        "status": p.status,
        "active": p.active,
        "vote_for": p.vote_for,
        "vote_against": p.vote_against,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    } for p in proposals])


@app.route("/api/admin/official-proposal/<int:pid>", methods=["GET"])
@admin_required
def admin_get_official_proposal_by_id(pid):
    """Récupère une proposition par ID."""
    p = OfficialProposal.query.get_or_404(pid)
    d = p.to_dict()
    if p.needs_debate:
        d["arguments_for"] = [a.to_dict() for a in p.arguments if a.side == "for" and a.status == "approved"]
        d["arguments_against"] = [a.to_dict() for a in p.arguments if a.side == "against" and a.status == "approved"]
    return jsonify(d)


@app.route("/api/admin/official-proposal/<int:pid>/argument", methods=["POST"])
@admin_required
def admin_add_proposal_argument(pid):
    """Ajoute un argument (pour ou contre) à une proposition."""
    p = OfficialProposal.query.get_or_404(pid)
    data = request.get_json() or {}
    side = data.get("side")
    text = (data.get("text") or "").strip()
    if side not in ("for", "against") or not text or len(text) < 5:
        return jsonify({"error": "Argument invalide (side: for/against, text min 5 car.)"}), 400
    arg = ProposalArgument(
        proposal_id=pid, session_id="admin",
        side=side, original_text=text, summary=text[:200], status="approved"
    )
    db.session.add(arg)
    db.session.commit()
    return jsonify(arg.to_dict()), 201


@app.route("/api/admin/official-proposal/argument/<int:arg_id>", methods=["DELETE"])
@admin_required
def admin_remove_proposal_argument(arg_id):
    """Retire un argument d'une proposition."""
    arg = ProposalArgument.query.get_or_404(arg_id)
    db.session.delete(arg)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/admin/official-proposal/new", methods=["POST"])
@admin_required
def admin_new_official_proposal():
    """Crée une nouvelle proposition vierge. Une seule proposition active autorisée."""
    if OfficialProposal.query.filter_by(active=True).first():
        return jsonify({"error": "Une proposition est déjà active. Clôturez-la d'abord."}), 400
    p = OfficialProposal(
        content="",
        status="En cours",
        active=False,
        vote_for=0,
        vote_against=0,
        needs_debate=False,
    )
    db.session.add(p)
    db.session.commit()
    return jsonify(p.to_dict()), 201


@app.route("/api/admin/official-proposal/close", methods=["POST"])
@admin_required
def admin_close_official_proposal():
    """Clôture la proposition active (retrait de l'affichage). Une seule proposition autorisée."""
    p = OfficialProposal.query.filter_by(active=True).first()
    if p:
        p.active = False
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/admin/official-proposal/<int:pid>/history", methods=["GET"])
@admin_required
def admin_proposal_history(pid):
    """Historique complet d'une proposition officielle pour affichage modal."""
    p = OfficialProposal.query.get_or_404(pid)
    d = p.to_dict()
    if p.needs_debate:
        d["arguments_for"] = [a.to_dict() for a in p.arguments if a.side == "for" and a.status == "approved"]
        d["arguments_against"] = [a.to_dict() for a in p.arguments if a.side == "against" and a.status == "approved"]
    return jsonify(d)


@app.route("/api/admin/official-proposal/<int:pid>/pdf", methods=["GET"])
@admin_required
def admin_proposal_pdf(pid):
    """Télécharge un PDF avec l'historique complet de la proposition officielle."""
    from io import BytesIO
    from pdf_export import build_proposal_pdf
    p = OfficialProposal.query.get_or_404(pid)
    pdf_bytes = build_proposal_pdf(p)
    from bs4 import BeautifulSoup
    title = "proposition"
    if p.content:
        try:
            soup = BeautifulSoup(p.content, "html.parser")
            title = (soup.get_text()[:30] or "proposition").replace("/", "-")
        except Exception:
            pass
    filename = f"proposition-cvl-{pid}-{title}.pdf"
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


# --------------- API Annonces ---------------

@app.route("/api/admin/announcements", methods=["GET"])
@admin_required
def list_announcements():
    announcements = Announcement.query.order_by(Announcement.created_at.desc()).all()
    return jsonify([a.to_dict() for a in announcements])


@app.route("/api/admin/announcements", methods=["POST"])
@admin_required
def create_announcement():
    data = request.get_json()
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "Titre requis"}), 400

    duration = data.get("duration_minutes", 60)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=duration)

    ann = Announcement(
        title=title,
        content=data.get("content", ""),
        style=data.get("style", "info"),
        active=True,
        duration_minutes=duration,
        expires_at=expires_at,
    )
    db.session.add(ann)
    db.session.commit()
    return jsonify(ann.to_dict()), 201


@app.route("/api/admin/announcements/<int:aid>", methods=["PUT"])
@admin_required
def update_announcement(aid):
    ann = Announcement.query.get_or_404(aid)
    data = request.get_json()

    if "title" in data:
        ann.title = data["title"]
    if "content" in data:
        ann.content = data["content"]
    if "extra_info" in data:
        ann.extra_info = data["extra_info"] or ""
    if "style" in data:
        ann.style = data["style"]
    if "active" in data:
        ann.active = data["active"]
        if data["active"] and ann.duration_minutes:
            ann.expires_at = datetime.now(timezone.utc) + timedelta(minutes=ann.duration_minutes)

    db.session.commit()
    return jsonify(ann.to_dict())


@app.route("/api/admin/chat-ia", methods=["POST"])
@admin_required
def admin_chat_ia():
    """Chat IA : peut créer des annonces si on le demande."""
    data = request.get_json() or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Message requis"}), 400
    try:
        import llm_engine
        prompt = f"""Tu es un assistant pour le CVL d'un lycée. L'utilisateur peut te demander de créer une annonce pour les écrans d'affichage.

Règles :
- Si l'utilisateur demande de créer une annonce (ex: "annonce pour dire que...", "affiche que...", "dis aux élèves que..."), réponds avec le format exact :
  CREER_ANNONCE:|titre|contenu|style
  (style = info, warning, urgent ou success)
- Sinon, réponds normalement en tant qu'assistant.
- Sois concis.

Message utilisateur : {message[:500]}"""
        result = llm_engine._call_ollama(prompt, temperature=0.3, num_predict=200, timeout=30)
        if not result:
            return jsonify({"reply": "L'IA n'est pas disponible (Ollama).", "announcement": None})
        result = result.strip()
        if result.startswith("CREER_ANNONCE:"):
            parts = result.split("|", 4)
            if len(parts) >= 4:
                title = parts[1].strip()[:200]
                content = (parts[2].strip() if len(parts) > 2 else "")[:1000]
                style = parts[3].strip() if len(parts) > 3 else "info"
                if style not in ("info", "warning", "urgent", "success"):
                    style = "info"
                if title:
                    ann = Announcement(
                        title=title,
                        content=content,
                        style=style,
                        active=True,
                        duration_minutes=60,
                        expires_at=datetime.now(timezone.utc) + timedelta(minutes=60),
                    )
                    db.session.add(ann)
                    db.session.commit()
                    _log_activity("announcement_created", f"Annonce créée via chat IA : « {title[:50]} »")
                    return jsonify({
                        "reply": f"Annonce créée : « {title} »",
                        "announcement": ann.to_dict(),
                    })
        return jsonify({"reply": result[:500], "announcement": None})
    except Exception as e:
        return jsonify({"reply": f"Erreur : {str(e)[:100]}", "announcement": None})


@app.route("/api/admin/announcements/<int:aid>/set-priority", methods=["POST"])
@admin_required
def set_priority_announcement(aid):
    """Active l'annonce prioritaire : tous les displays affichent uniquement cette annonce."""
    ann = Announcement.query.get_or_404(aid)
    if not ann.active or not ann.is_active():
        return jsonify({"error": "L'annonce doit être active"}), 400
    Announcement.query.update({Announcement.is_priority: False})
    ann.is_priority = True
    set_setting("priority_announcement_id", str(ann.id))
    db.session.commit()
    return jsonify({"success": True, "announcement": ann.to_dict()})


@app.route("/api/admin/announcements/clear-priority", methods=["POST"])
@admin_required
def clear_priority_announcement():
    """Désactive l'annonce prioritaire."""
    pid = get_setting("priority_announcement_id", "")
    if pid and pid.isdigit():
        ann = Announcement.query.get(int(pid))
        if ann:
            ann.is_priority = False
    set_setting("priority_announcement_id", "")
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/admin/priority-announcement", methods=["GET"])
@admin_required
def get_priority_announcement():
    """Retourne l'annonce prioritaire active si elle existe."""
    pid = get_setting("priority_announcement_id", "")
    if not pid or not pid.isdigit():
        return jsonify(None)
    ann = Announcement.query.get(int(pid))
    if not ann:
        return jsonify(None)
    return jsonify(ann.to_dict())


@app.route("/api/admin/announcements/<int:aid>", methods=["DELETE"])
@admin_required
def delete_announcement(aid):
    ann = Announcement.query.get_or_404(aid)
    db.session.delete(ann)
    db.session.commit()
    return jsonify({"success": True})


# --------------- API Information Officielle CVL ---------------

@app.route("/api/admin/cvl-official-info", methods=["GET"])
@admin_required
def admin_list_cvl_official_info():
    info_list = CvlOfficialInfo.query.order_by(CvlOfficialInfo.display_order, CvlOfficialInfo.created_at).all()
    return jsonify([i.to_dict() for i in info_list])


@app.route("/api/admin/cvl-official-info", methods=["POST"])
@admin_required
def admin_create_cvl_official_info():
    data = request.get_json() or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Titre requis"}), 400
    info = CvlOfficialInfo(
        title=title,
        content=data.get("content", ""),
        style=data.get("style", "info"),
        display_mode=data.get("display_mode", "banner"),
        active=True,
        display_order=data.get("display_order", 0),
    )
    db.session.add(info)
    db.session.commit()
    return jsonify(info.to_dict()), 201


@app.route("/api/admin/cvl-official-info/<int:iid>", methods=["PUT"])
@admin_required
def admin_update_cvl_official_info(iid):
    info = CvlOfficialInfo.query.get_or_404(iid)
    data = request.get_json() or {}
    if "title" in data:
        info.title = (data["title"] or "").strip() or info.title
    if "content" in data:
        info.content = data["content"]
    if "style" in data:
        info.style = data["style"] if data["style"] in CvlOfficialInfo.VALID_STYLES else info.style
    if "display_mode" in data:
        info.display_mode = data["display_mode"] if data["display_mode"] in CvlOfficialInfo.VALID_MODES else info.display_mode
    if "active" in data:
        info.active = bool(data["active"])
    if "display_order" in data:
        info.display_order = int(data.get("display_order", 0))
    db.session.commit()
    return jsonify(info.to_dict())


@app.route("/api/admin/cvl-official-info/<int:iid>", methods=["DELETE"])
@admin_required
def admin_delete_cvl_official_info(iid):
    info = CvlOfficialInfo.query.get_or_404(iid)
    db.session.delete(info)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/display/announcements", methods=["GET"])
def display_announcements():
    announcements = Announcement.query.filter_by(active=True).all()
    active = [a.to_dict() for a in announcements if a.is_active()]
    return jsonify(active)


@app.route("/api/display/completed-suggestions", methods=["GET"])
def display_completed_suggestions():
    """Suggestions en cours de mise en place ou terminées, pour le bandeau display."""
    completed = Suggestion.query.filter(
        Suggestion.status.in_(("En cours de mise en place", "Terminée")),
        Suggestion.completed_at.isnot(None),
    ).order_by(Suggestion.completed_at.desc()).limit(12).all()
    completed = [
        s for s in completed
        if s.status == "En cours de mise en place" or _terminée_still_visible(s)
    ][:5]
    return jsonify([{
        "id": s.id,
        "title": s.title,
        "status": s.status,
        "reason": "En cours de mise en place" if s.status == "En cours de mise en place" else "Terminée",
        "completed_at": s.completed_at.isoformat() if s.completed_at else None,
    } for s in completed])


@app.route("/api/display/priority-announcement", methods=["GET"])
def display_priority_announcement():
    """Annonce prioritaire active : toutes les pages display l'affichent."""
    pid = get_setting("priority_announcement_id", "")
    if not pid or not pid.isdigit():
        return jsonify(None)
    ann = Announcement.query.get(int(pid))
    if not ann or not ann.active:
        return jsonify(None)
    if not ann.is_active():
        return jsonify(None)
    return jsonify(ann.to_dict())


# --------------- API Settings ---------------

@app.route("/api/admin/settings", methods=["GET"])
@admin_required
def admin_get_settings():
    result = {}
    for key, default in SiteSettings.DEFAULTS.items():
        result[key] = get_setting(key, default)
    return jsonify(result)


@app.route("/api/admin/settings", methods=["PUT"])
@admin_required
def admin_update_settings():
    data = request.get_json() or {}
    if not isinstance(data, dict):
        return jsonify({"error": "Données invalides"}), 400
    for key, value in data.items():
        if key in SiteSettings.DEFAULTS:
            set_setting(key, str(value))
    return jsonify({"success": True})


@app.route("/api/settings/public", methods=["GET"])
def public_settings():
    return jsonify({
        "submissions_open": get_setting("submissions_open", "true") == "true",
        "display_mode": get_setting("display_mode", "normal"),
        "display_waiting_title": get_setting("display_waiting_title", ""),
        "display_waiting_text": get_setting("display_waiting_text", ""),
        "bus_schedule": _parse_bus_schedule_slots(),
        "bus_restrict_to_schedule": get_setting("bus_restrict_to_schedule", "false") == "true",
        "bus_force_display": get_setting("bus_force_display", "false") == "true",
        "bus_force_display_until": get_setting("bus_force_display_until", ""),
        "bus_alternance_enabled": get_setting("bus_alternance_enabled", "false") == "true",
        "bus_alternance_interval_sec": int(get_setting("bus_alternance_interval_sec", "60") or "60"),
        "feature_bus_enabled": get_setting("feature_bus_enabled", "true") == "true",
        "feature_display_dynamic_enabled": get_setting("feature_display_dynamic_enabled", "true") == "true",
        "feature_music_poll_enabled": get_setting("feature_music_poll_enabled", "true") == "true",
        "feature_official_proposal_enabled": get_setting("feature_official_proposal_enabled", "true") == "true",
        "feature_cvl_official_info_enabled": get_setting("feature_cvl_official_info_enabled", "true") == "true",
        "feature_ringtone_banner_enabled": get_setting("feature_ringtone_banner_enabled", "false") == "true",
    })


def _parse_bus_schedule():
    """Parse bus display schedule from settings."""
    raw = get_setting("bus_schedule", "[]")
    try:
        return json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return []


# --- GTFS statique (Ycéo / STRAN) : payload unifié pour /api/display/bus et TV ---
_DEFAULT_BUS_GTFS_URL = (
    "https://app.mecatran.com/utw/ws/gtfsfeed/static/stran-merge"
    "?apiKey=2e6071036d276153761f0c090b4a45420e047612"
)
_BUS_DISPLAY_CACHE: dict = {"ts": 0.0, "payload": None}


def _parse_bus_stop_ids_list() -> list[str]:
    raw = get_setting("bus_stop_ids", "")
    if raw:
        try:
            j = json.loads(raw)
            if isinstance(j, list):
                return [str(x) for x in j]
        except json.JSONDecodeError:
            pass
    # Cité Scolaire : CSC01 + CSC02 (deux sens pour H1/H2 qui passent aux mêmes quais). Tranchée : TRA01/TRA02.
    # Autres ids numériques (ex. lycée). Pas de 10826 (Cité Sanitaire) pour ne pas confondre avec la Cité Scolaire.
    return ["CSC01", "CSC02", "TRA01", "TRA02", "10869", "10954"]


def _parse_bus_route_order():
    """Ordre d’affichage des lignes (stabilité type tableau des départs). JSON ou liste par défaut."""
    raw = get_setting("bus_route_order", "")
    if raw:
        try:
            j = json.loads(raw)
            if isinstance(j, list) and len(j) > 0:
                return [str(x).strip() for x in j if str(x).strip()]
        except json.JSONDecodeError:
            pass
    return [
        "H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8", "H9", "H10", "H11", "H12",
        "4", "5", "6", "7",
        "L1", "L2", "L3", "L4", "L5", "HLP", "L",
    ]


def _parse_bus_eta_tiers():
    """Seuils visuels (minutes, inclus) : imminent ≤ soon ≤ near. Configurable via réglages."""
    return {
        "imminent_max": max(0, int(get_setting("bus_eta_imminent_max", "1") or "1")),
        "soon_max": max(0, int(get_setting("bus_eta_soon_max", "3") or "3")),
        "near_max": max(0, int(get_setting("bus_eta_near_max", "7") or "7")),
    }


def _sort_bus_departures(departures, route_order):
    """Tri : ordre de ligne (config), puis prochain passage, puis direction / arrêt."""

    def rank(route_name: str) -> tuple:
        r = (route_name or "").strip()
        if r in route_order:
            return (0, route_order.index(r))
        return (1, r)

    def key(d: dict) -> tuple:
        rk = rank(d.get("route_name") or "")
        dmin = int(d.get("delay_minutes") or 9999)
        direction = (d.get("direction") or "").strip()
        stop_k = (d.get("config_stop_id") or d.get("stop_name") or "").strip()
        return (rk[0], rk[1], dmin, direction, stop_k)

    return sorted(departures, key=key)


def _route_sort_key_tuple(rn: str, route_order: list) -> tuple:
    r = (rn or "").strip()
    if r in route_order:
        return (0, route_order.index(r))
    return (1, r)


def _matches_priority_slot(slot: str, route_name: str) -> bool:
    """H1/H2/H3 ou 4 / H4 ou 7 / H7."""
    rn = (route_name or "").strip()
    s = slot.strip().upper()
    if s == "4":
        return rn in ("4", "H4")
    if s == "7":
        return rn in ("7", "H7")
    return rn.upper() == s


def _direction_bucket(r: dict) -> str:
    """Discriminant de sens : direction_id GTFS si présent, sinon trip_headsign."""
    did = r.get("direction_id")
    if did is not None:
        try:
            return f"did:{int(did)}"
        except (TypeError, ValueError):
            pass
    return (r.get("direction") or "").strip() or "—"


def _pair_sides_by_direction(rows: list) -> tuple[dict | None, dict | None]:
    """Sens 1 = direction_id 0, Sens 2 = direction_id 1 (GTFS). Sinon deux sens par trip_headsign."""
    if not rows:
        return (None, None)
    by_dir: dict[str, list] = {}
    for r in rows:
        dr = _direction_bucket(r)
        by_dir.setdefault(dr, []).append(r)
    for k in by_dir:
        by_dir[k].sort(key=lambda x: int(x.get("delay_minutes") or 9999))
    # Dès qu’au moins un trip a direction_id 0 ou 1, on fixe Sens1/Sens2 sur ces créneaux
    # (évite le fallback headsign si un seul trip sans direction_id pollue la liste).
    if "did:0" in by_dir or "did:1" in by_dir:
        a = (by_dir.get("did:0") or [None])[0]
        b = (by_dir.get("did:1") or [None])[0]
        return (a, b)
    keys_sorted = sorted(
        by_dir.keys(),
        key=lambda k: min(int(x.get("delay_minutes") or 9999) for x in by_dir[k]),
    )
    if len(keys_sorted) >= 2:
        return (by_dir[keys_sorted[0]][0], by_dir[keys_sorted[1]][0])
    if len(keys_sorted) == 1:
        return (by_dir[keys_sorted[0]][0], None)
    return (None, None)


def _rows_for_line7_tranchee_only(rows: list) -> list:
    """Ligne 7 : ne garder que les départs à l’arrêt Tranchée (config TRA* ou libellé)."""
    out = []
    for r in rows:
        cid = (r.get("config_stop_id") or "").strip().upper()
        sn = (r.get("stop_name") or "").strip()
        if cid.startswith("TRA"):
            out.append(r)
        elif re.search(r"(?i)tranch", sn):
            out.append(r)
    return out


def _combined_stop_label(a: dict | None, b: dict | None, route_name: str = "") -> str:
    rn = (route_name or "").strip()

    def _cite_label(s: str) -> str:
        t = (s or "").strip()
        if rn in ("H1", "H2") and re.search(r"(?i)cité\s*sanitaire", t):
            return re.sub(r"(?i)cité\s*sanitaire", "Cité Scolaire", t)
        return t

    if rn in ("7", "H7", "07"):
        names: list[str] = []
        for side in (a, b):
            if not side:
                continue
            sn = (side.get("stop_name") or "").strip()
            if re.search(r"(?i)tranch", sn):
                names.append(sn)
        if names:
            uniq = list(dict.fromkeys(names))
            return uniq[0] if len(uniq) == 1 else " · ".join(uniq)
        return ""
    if not a and not b:
        return ""
    if not b:
        return _cite_label((a.get("stop_name") or "").strip())
    if not a:
        return _cite_label((b.get("stop_name") or "").strip())
    sa = _cite_label((a.get("stop_name") or "").strip())
    sb = _cite_label((b.get("stop_name") or "").strip())
    if sa == sb or not sb:
        return sa
    if not sa:
        return sb
    return f"{sa} · {sb}"


def _bundle_two_directions_per_route(departures: list, route_order: list) -> list:
    """Une ligne d’écran par ligne de bus : jusqu’à 2 sens côte à côte (départs + attente chacun)."""
    if not departures:
        return []
    by_route: dict[str, list] = {}
    for d in departures:
        rn = (d.get("route_name") or "").strip()
        if rn not in by_route:
            by_route[rn] = []
        by_route[rn].append(d)
    out = []
    for rn in sorted(by_route.keys(), key=lambda r: _route_sort_key_tuple(r, route_order)):
        rows = by_route[rn]
        if (rn or "").strip() in ("7", "H7", "07"):
            rows = _rows_for_line7_tranchee_only(rows)
            if not rows:
                continue
        a, b = _pair_sides_by_direction(rows)
        if a is None and b is None:
            continue
        if a is None:
            a = _void_side_na(rn)
        if b is None:
            b = _void_side_na(rn)
        out.append(
            {
                "route_name": rn,
                "stop_name": _combined_stop_label(a, b, rn),
                "side_a": a,
                "side_b": b,
            }
        )
    return out


def _sort_priority_routes_first(departures: list, route_order: list) -> list:
    """H1 → H2 → H3 → 4 → 7 en tête si présents, puis le reste selon l’ordre réseau."""
    priority_slots = ["H1", "H2", "H3", "4", "7"]
    by_rn = {(d.get("route_name") or "").strip(): d for d in departures}
    out = []
    for slot in priority_slots:
        for rn, d in list(by_rn.items()):
            if _matches_priority_slot(slot, rn):
                out.append(d)
                del by_rn[rn]
                break
    rest = sorted(
        by_rn.values(),
        key=lambda x: _route_sort_key_tuple((x.get("route_name") or "").strip(), route_order),
    )
    return out + rest


BUS_PRIORITY_SLOTS: list[str] = ["H1", "H2", "H3", "4", "7"]


def _get_bus_compute_horizon_minutes() -> int:
    """Horizon GTFS pour l’index stop_times (défaut 10 h = 600 min). Plafond 600."""
    co = (get_setting("bus_compute_horizon_minutes", "") or "").strip()
    if co:
        return max(30, min(600, int(co)))
    ho = (get_setting("bus_horizon_minutes", "") or "").strip()
    if ho:
        return max(30, min(600, int(ho)))
    return 600


def _slot_display_route_name(slot: str) -> str:
    return {"H1": "H1", "H2": "H2", "H3": "H3", "4": "4", "7": "7"}.get(slot, slot)


def _void_side_na(route_display: str) -> dict:
    return {
        "route_name": route_display,
        "direction": "—",
        "stop_name": "",
        "config_stop_id": "",
        "delay_minutes": -1,
        "no_departure": True,
        "labels": ["—"],
        "label": "—",
        "urgency": "normal",
        "is_imminent": False,
        "primary": {"minutes": -1, "label": "—", "urgency": "normal", "is_imminent": False},
        "secondary": None,
    }


def _placeholder_bundled_row(slot: str) -> dict:
    rn = _slot_display_route_name(slot)
    void_a = _void_side_na(rn)
    void_b = _void_side_na(rn)
    return {
        "route_name": rn,
        "stop_name": "Pas de départ dans l’horizon",
        "side_a": void_a,
        "side_b": void_b,
        "is_placeholder": True,
    }


def _earliest_bundle_delay(row: dict) -> int:
    """Plus petit délai (minutes) sur les deux sens ; placeholders en dernier."""

    def min_side(side: dict | None) -> int:
        if not side or not isinstance(side, dict):
            return 99999
        if side.get("no_departure"):
            return 99999
        p = side.get("primary") or {}
        m = p.get("minutes")
        if m is not None and int(m) >= 0:
            return int(m)
        dm = side.get("delay_minutes")
        if dm is not None and int(dm) >= 0:
            return int(dm)
        return 99999

    sa = row.get("side_a") or {}
    sb = row.get("side_b")
    return min(min_side(sa), min_side(sb) if sb else 99999)


def _finalize_bus_departures_for_display(
    bundled: list, max_dep: int, route_order: list
) -> tuple[list, dict]:
    """
    Pass 1 : réserver les créneaux H1, H2, H3, 4, 7 (réel ou placeholder « — » si aucun départ).
    Pass 2 : compléter avec les autres lignes par proximité du prochain passage, sans tronquer
    une priorité par un simple [:N] sur une liste mal ordonnée.
    """
    priority_slots = BUS_PRIORITY_SLOTS
    by_slot: dict[str, dict] = {}
    for row in bundled:
        if not isinstance(row, dict):
            continue
        rn = (row.get("route_name") or "").strip()
        for slot in priority_slots:
            if _matches_priority_slot(slot, rn):
                if slot not in by_slot:
                    by_slot[slot] = row
                break

    used_ids = {id(v) for v in by_slot.values()}
    rest = [r for r in bundled if id(r) not in used_ids]
    rest_sorted = sorted(
        rest,
        key=lambda r: (
            _earliest_bundle_delay(r),
            _route_sort_key_tuple((r.get("route_name") or "").strip(), route_order),
        ),
    )

    out: list = []
    for slot in priority_slots:
        if len(out) >= max_dep:
            break
        row = by_slot.get(slot)
        if row:
            out.append(row)
        else:
            out.append(_placeholder_bundled_row(slot))

    for r in rest_sorted:
        if len(out) >= max_dep:
            break
        out.append(r)

    meta = {
        "priority_routes_found": [s for s in priority_slots if s in by_slot],
        "priority_routes_missing": [s for s in priority_slots if s not in by_slot],
        "placeholders_used": [s for s in priority_slots if s not in by_slot],
    }
    return out, meta


def _dropped_routes_after_bundle(raw_departures: list, bundled: list) -> list[dict]:
    """Routes présentes en lignes plates GTFS mais absentes après bundle (ex. ligne 7 sans quai Tranchée)."""
    raw_routes: set[str] = set()
    for d in raw_departures or []:
        rn = (d.get("route_name") or "").strip()
        if rn:
            raw_routes.add(rn)
    out_routes: set[str] = set()
    for row in bundled or []:
        if isinstance(row, dict):
            rn = (row.get("route_name") or "").strip()
            if rn:
                out_routes.add(rn)
    dropped: list[dict] = []
    for rn in raw_routes - out_routes:
        if rn in ("7", "H7", "07"):
            reason = "filtre ligne 7 → uniquement arrêts Tranchée (TRA*) ; aucun départ retenu"
        else:
            reason = "agrégation par ligne : aucun sens retenu (best per direction)"
        dropped.append({"route": rn, "stage": "bundle", "reason": reason})
    return dropped


def _h2_pipeline_diagnostics(
    raw_flat: list,
    bundled: list,
    bundled_sorted: list | None,
    final: list,
) -> dict:
    """Compteurs explicites H2 à chaque étape du pipeline (debug)."""

    def count_h2_flat(rows: list) -> int:
        return sum(1 for d in (rows or []) if (d.get("route_name") or "").strip() == "H2")

    def count_h2_bundled(rows: list) -> int:
        n = 0
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            if (r.get("route_name") or "").strip() == "H2":
                n += 1
        return n

    h2r = count_h2_flat(raw_flat)
    h2b = count_h2_bundled(bundled)
    h2s = count_h2_bundled(bundled_sorted) if bundled_sorted is not None else None
    h2f = count_h2_bundled(final)
    if h2r == 0:
        stage = "pas_dans_brut_gtfs"
    elif h2b == 0:
        stage = "perdue_au_bundle"
    elif h2f == 0:
        stage = "perdue_finale_ou_troncature"
    else:
        stage = "ok"
    return {
        "h2_before_bundle_count": h2r,
        "h2_after_bundle_count": h2b,
        "h2_after_sort_count": h2s,
        "h2_after_truncate_count": h2f,
        "h2_stage": stage,
    }


def _default_bus_schedule_slots() -> list[dict]:
    return [
        {"start": "07:15", "end": "08:30"},
        {"start": "11:45", "end": "13:15"},
        {"start": "16:15", "end": "18:30"},
    ]


def _parse_bus_schedule_slots() -> list[dict]:
    s = _parse_bus_schedule()
    if s:
        return s
    return _default_bus_schedule_slots()


def _paris_now_dt() -> datetime:
    """Maintenant en Europe/Paris (affichage bus / TV)."""
    if PARIS_TZ is not None:
        try:
            return datetime.now(PARIS_TZ)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def _tv_should_show_bus_fullscreen(bus_enabled: bool, bus_excluded: bool) -> bool:
    """Plein écran bus sur /tv/ : uniquement si réglage activé + dans les créneaux + données GTFS disponibles."""
    if not bus_enabled or bus_excluded:
        return False
    if get_setting("bus_tv_show_only_during_schedule", "false") != "true":
        return False
    slots = _parse_bus_schedule_slots()
    if not _is_now_in_bus_slots(_paris_now_dt(), slots):
        return False
    return _get_bus_display_payload_cached().get("available") is True


def _is_now_in_bus_slots(now_dt: datetime, slots: list[dict]) -> bool:
    if not slots:
        return True
    now_min = now_dt.hour * 60 + now_dt.minute
    for slot in slots:
        try:
            sh, sm = (slot.get("start") or "00:00").split(":")[:2]
            eh, em = (slot.get("end") or "23:59").split(":")[:2]
            a = int(sh) * 60 + int(sm)
            b = int(eh) * 60 + int(em)
            if a <= now_min <= b:
                return True
        except (ValueError, IndexError):
            continue
    return False


def _parse_bus_force_display() -> bool:
    if get_setting("bus_force_display", "false") != "true":
        return False
    until = get_setting("bus_force_display_until", "")
    if not until:
        return True
    try:
        u = datetime.fromisoformat(until.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) < u
    except Exception:
        return True


def _fake_bus_display_payload(test_perturbations: bool = False) -> dict:
    now_ms = int(time.time() * 1000)
    cache_sec = int(get_setting("bus_cache_seconds", "60") or "60")
    tiers = _parse_bus_eta_tiers()
    imax, smax, nmax = tiers["imminent_max"], tiers["soon_max"], tiers["near_max"]

    def urg(dm: int) -> str:
        if dm <= imax:
            return "imminent"
        if dm <= smax:
            return "soon"
        if dm <= nmax:
            return "near"
        return "normal"

    def plab(dm: int) -> str:
        if dm <= imax:
            return "IMMINENT"
        return f"{dm} min"

    deps = [
        {
            "route_name": "H1",
            "direction": "Gavy",
            "stop_name": "Cité Scolaire (sens 1)",
            "config_stop_id": "CSC01",
            "delay_minutes": 2,
            "delay_minutes_2": 14,
            "times_minutes": [2, 14],
            "labels": [plab(2), plab(14)],
            "label": f"{plab(2)} · {plab(14)}",
            "is_imminent": urg(2) == "imminent",
            "urgency": urg(2),
            "primary": {
                "minutes": 2,
                "label": plab(2),
                "urgency": urg(2),
                "is_imminent": urg(2) == "imminent",
            },
            "secondary": {"minutes": 14, "label": plab(14)},
        },
        {
            "route_name": "H1",
            "direction": "Saint-Joseph",
            "stop_name": "Cité Scolaire (sens 2)",
            "config_stop_id": "CSC02",
            "delay_minutes": 7,
            "delay_minutes_2": 19,
            "times_minutes": [7, 19],
            "labels": [plab(7), plab(19)],
            "label": f"{plab(7)} · {plab(19)}",
            "is_imminent": urg(7) == "imminent",
            "urgency": urg(7),
            "primary": {
                "minutes": 7,
                "label": plab(7),
                "urgency": urg(7),
                "is_imminent": urg(7) == "imminent",
            },
            "secondary": {"minutes": 19, "label": plab(19)},
        },
        {
            "route_name": "H2",
            "direction": "Pornichet",
            "stop_name": "Cité Scolaire",
            "config_stop_id": "SAN01",
            "delay_minutes": 8,
            "delay_minutes_2": 21,
            "times_minutes": [8, 21],
            "labels": [plab(8), plab(21)],
            "label": f"{plab(8)} · {plab(21)}",
            "is_imminent": False,
            "urgency": urg(8),
            "primary": {
                "minutes": 8,
                "label": plab(8),
                "urgency": urg(8),
                "is_imminent": False,
            },
            "secondary": {"minutes": 21, "label": plab(21)},
        },
        {
            "route_name": "H2",
            "direction": "Saint-Nazaire",
            "stop_name": "Cité Scolaire",
            "config_stop_id": "CSC02",
            "delay_minutes": 15,
            "delay_minutes_2": 33,
            "times_minutes": [15, 33],
            "labels": [plab(15), plab(33)],
            "label": f"{plab(15)} · {plab(33)}",
            "is_imminent": False,
            "urgency": urg(15),
            "primary": {
                "minutes": 15,
                "label": plab(15),
                "urgency": urg(15),
                "is_imminent": False,
            },
            "secondary": {"minutes": 33, "label": plab(33)},
        },
        {
            "route_name": "H3",
            "direction": "Saint-Nazaire",
            "stop_name": "Cité Scolaire",
            "config_stop_id": "CSC01",
            "delay_minutes": 11,
            "delay_minutes_2": 26,
            "times_minutes": [11, 26],
            "labels": [plab(11), plab(26)],
            "label": f"{plab(11)} · {plab(26)}",
            "is_imminent": False,
            "urgency": urg(11),
            "primary": {
                "minutes": 11,
                "label": plab(11),
                "urgency": urg(11),
                "is_imminent": False,
            },
            "secondary": {"minutes": 26, "label": plab(26)},
        },
        {
            "route_name": "4",
            "direction": "Mendès-France",
            "stop_name": "Tranchée",
            "config_stop_id": "TRA01",
            "delay_minutes": 6,
            "delay_minutes_2": 22,
            "times_minutes": [6, 22],
            "labels": [plab(6), plab(22)],
            "label": f"{plab(6)} · {plab(22)}",
            "is_imminent": urg(6) == "imminent",
            "urgency": urg(6),
            "primary": {
                "minutes": 6,
                "label": plab(6),
                "urgency": urg(6),
                "is_imminent": urg(6) == "imminent",
            },
            "secondary": {"minutes": 22, "label": plab(22)},
        },
        {
            "route_name": "7",
            "direction": "Méan",
            "stop_name": "Tranchée",
            "config_stop_id": "TRA01",
            "delay_minutes": 9,
            "delay_minutes_2": 24,
            "times_minutes": [9, 24],
            "labels": [plab(9), plab(24)],
            "label": f"{plab(9)} · {plab(24)}",
            "is_imminent": False,
            "urgency": urg(9),
            "primary": {
                "minutes": 9,
                "label": plab(9),
                "urgency": urg(9),
                "is_imminent": False,
            },
            "secondary": {"minutes": 24, "label": plab(24)},
        },
        {
            "route_name": "7",
            "direction": "Gare de St Nazaire",
            "stop_name": "Tranchée",
            "config_stop_id": "TRA02",
            "delay_minutes": 11,
            "delay_minutes_2": 26,
            "times_minutes": [11, 26],
            "labels": [plab(11), plab(26)],
            "label": f"{plab(11)} · {plab(26)}",
            "is_imminent": False,
            "urgency": urg(11),
            "primary": {
                "minutes": 11,
                "label": plab(11),
                "urgency": urg(11),
                "is_imminent": False,
            },
            "secondary": {"minutes": 26, "label": plab(26)},
        },
        {
            "route_name": "L1",
            "direction": "Gavy",
            "stop_name": "Lycée Sainte-Anne",
            "config_stop_id": "LAE01",
            "delay_minutes": 12,
            "delay_minutes_2": None,
            "times_minutes": [12],
            "labels": [plab(12)],
            "label": plab(12),
            "is_imminent": False,
            "urgency": urg(12),
            "primary": {
                "minutes": 12,
                "label": plab(12),
                "urgency": urg(12),
                "is_imminent": False,
            },
            "secondary": None,
        },
    ]
    ro = _parse_bus_route_order()
    max_fake = int(get_setting("bus_max_departures", "32") or "32")
    deps = _bundle_two_directions_per_route(deps, ro)
    deps, _ = _finalize_bus_departures_for_display(deps, max_fake, ro)
    out = {
        "available": True,
        "source": "test",
        "computed_at": now_ms,
        "valid_until": now_ms + cache_sec * 1000,
        "departures": deps,
    }
    if test_perturbations:
        out["notice"] = "Perturbations simulées (mode test)"
    return out


def _bus_route_histogram(dep_list: list) -> dict[str, int]:
    h: dict[str, int] = {}
    for d in dep_list or []:
        rn = (d.get("route_name") or "").strip()
        if rn:
            h[rn] = h.get(rn, 0) + 1
    return h


def _build_bus_display_payload(include_diagnostics: bool = False) -> dict:
    diag: dict = {}
    if include_diagnostics:
        diag = {
            "hint": "localStorage.setItem('bus_debug','1') puis recharge, ou ?bus_debug=1 — API: /api/display/bus?debug=1",
            "settings": {
                "feature_bus_enabled": get_setting("feature_bus_enabled", "true") == "true",
                "bus_test_mode": get_setting("bus_test_mode", "false") == "true",
                "bus_restrict_to_schedule": get_setting("bus_restrict_to_schedule", "false") == "true",
                "bus_compute_horizon_minutes": _get_bus_compute_horizon_minutes(),
                "bus_horizon_minutes_legacy": int(get_setting("bus_horizon_minutes", "45") or "45"),
                "bus_relevance_minutes": int(get_setting("bus_relevance_minutes", "30") or "30"),
                "bus_max_departures": int(get_setting("bus_max_departures", "32") or "32"),
                "stop_ids_config": _parse_bus_stop_ids_list(),
                "horizon_note": "calcul GTFS = bus_compute_horizon_minutes (ou bus_horizon_minutes si calcul vide) ; pagination UI après payload",
            },
        }

    if get_setting("bus_test_mode", "false") == "true":
        test_pert = get_setting("bus_test_perturbations", "false") == "true"
        out = _fake_bus_display_payload(test_pert)
        if include_diagnostics:
            out = dict(out)
            diag["mode"] = "test_fake_payload"
            diag["note"] = "Mode test admin — pas le GTFS réel"
            out["diagnostics"] = diag
        return out

    if get_setting("feature_bus_enabled", "true") != "true":
        out = {"available": False, "source": "gtfs_static", "reason": "disabled"}
        if include_diagnostics:
            out["diagnostics"] = {**diag, "failure": "feature_bus_enabled false"}
        return out

    slots = _parse_bus_schedule_slots()
    force = _parse_bus_force_display()
    if ZoneInfo:
        try:
            tz = ZoneInfo("Europe/Paris")
        except Exception:
            tz = timezone.utc
    else:
        tz = timezone.utc
    now_dt = datetime.now(tz)

    if include_diagnostics:
        diag["paris_now"] = now_dt.isoformat()
        diag["schedule_slots"] = slots
        diag["bus_force_display"] = force

    # Par défaut le panneau bus suit le GTFS toute l’année ; horaires « scolaires » optionnels.
    if get_setting("bus_restrict_to_schedule", "false") == "true":
        if not force and not _is_now_in_bus_slots(now_dt, slots):
            out = {"available": False, "source": "gtfs_static", "reason": "outside_schedule"}
            if include_diagnostics:
                out["diagnostics"] = {**diag, "failure": "outside_schedule (bus_restrict_to_schedule actif)"}
            return out

    url = (get_setting("bus_gtfs_url", "") or "").strip() or _DEFAULT_BUS_GTFS_URL
    cache_dir = os.path.join(app.instance_path, "gtfs_cache")
    refresh_days = int(get_setting("bus_gtfs_refresh_days", "7") or "7")

    gtfs = bus_gtfs.ensure_gtfs_loaded(url, cache_dir, refresh_days)
    if gtfs is None:
        out = {"available": False, "source": "gtfs_static", "reason": "gtfs_not_loaded"}
        if include_diagnostics:
            out["diagnostics"] = {**diag, "failure": "gtfs_not_loaded", "gtfs_url": url}
        return out

    stop_ids = _parse_bus_stop_ids_list()
    horizon_compute = _get_bus_compute_horizon_minutes()
    max_dep = int(get_setting("bus_max_departures", "32") or "32")
    eta_tiers = _parse_bus_eta_tiers()
    route_order = _parse_bus_route_order()
    now_ms = int(time.time() * 1000)
    cache_sec = int(get_setting("bus_cache_seconds", "60") or "60")

    departures, no_svc, _ = bus_gtfs.compute_next_departures(
        gtfs, stop_ids, now_dt, horizon_minutes=horizon_compute, eta_tiers=eta_tiers
    )
    if include_diagnostics:
        diag["gtfs_no_service_today"] = bool(no_svc)
        diag["horizon_compute_minutes"] = horizon_compute
        diag["raw_rows_before_bundle"] = len(departures or [])
        diag["routes_histogram_raw"] = _bus_route_histogram(departures)
        diag["h2_rows_raw"] = sum(
            1 for d in (departures or []) if (d.get("route_name") or "").strip() == "H2"
        )

    if no_svc:
        out = {"available": False, "source": "gtfs_static", "reason": "no_service_today"}
        if include_diagnostics:
            out["diagnostics"] = {**diag, "failure": "no_service_today (calendrier GTFS)"}
        return out

    if not departures:
        final_departures, fin_meta = _finalize_bus_departures_for_display([], max_dep, route_order)
        out = {
            "available": True,
            "source": "gtfs_static",
            "computed_at": now_ms,
            "valid_until": now_ms + cache_sec * 1000,
            "departures": final_departures,
            "notice": "Aucun départ dans l’horizon calculé — créneaux prioritaires avec « — »",
        }
        if include_diagnostics:
            diag["bundled_rows"] = 0
            diag["routes_histogram_bundled"] = {}
            diag["priority_routes_found"] = fin_meta.get("priority_routes_found", [])
            diag["priority_routes_missing"] = fin_meta.get("priority_routes_missing", [])
            diag["dropped_routes_with_reason"] = []
            diag["final_routes"] = [(d.get("route_name") or "").strip() for d in final_departures]
            diag["h2_pipeline"] = _h2_pipeline_diagnostics([], [], None, final_departures)
            diag["success"] = True
            out["diagnostics"] = diag
        return out

    bundled = _bundle_two_directions_per_route(departures, route_order)
    bundled_sorted = _sort_priority_routes_first(bundled, route_order)
    final_departures, fin_meta = _finalize_bus_departures_for_display(bundled, max_dep, route_order)

    if include_diagnostics:
        diag["bundled_rows"] = len(bundled)
        diag["routes_histogram_bundled"] = _bus_route_histogram(
            [x for x in bundled if isinstance(x, dict)]
        )
        diag["priority_routes_found"] = fin_meta.get("priority_routes_found", [])
        diag["priority_routes_missing"] = fin_meta.get("priority_routes_missing", [])
        diag["dropped_routes_with_reason"] = _dropped_routes_after_bundle(departures, bundled)
        diag["final_routes"] = [(d.get("route_name") or "").strip() for d in final_departures]
        diag["h2_pipeline"] = _h2_pipeline_diagnostics(
            departures, bundled, bundled_sorted, final_departures
        )
        diag["success"] = True

    out = {
        "available": True,
        "source": "gtfs_static",
        "computed_at": now_ms,
        "valid_until": now_ms + cache_sec * 1000,
        "departures": final_departures,
    }
    if include_diagnostics:
        out["diagnostics"] = diag
    return out


def _get_bus_display_payload_cached() -> dict:
    cache_sec = int(get_setting("bus_cache_seconds", "60") or "60")
    now = time.time()
    p = _BUS_DISPLAY_CACHE.get("payload")
    if p is not None and (now - _BUS_DISPLAY_CACHE["ts"]) < cache_sec:
        return p
    p = _build_bus_display_payload()
    _BUS_DISPLAY_CACHE["ts"] = now
    _BUS_DISPLAY_CACHE["payload"] = p
    return p


BUS_STOPS = [
    {"id": "CSC01", "name": "Cité Scolaire (sens 1)"},
    {"id": "CSC02", "name": "Cité Scolaire (sens 2)"},
    {"id": "LAE01", "name": "Lycée Saint Anne (sens 1)"},
    {"id": "LAE02", "name": "Lycée Saint Anne (sens 2)"},
    {"id": "TRA01", "name": "Tranchée (sens 1)"},
    {"id": "TRA02", "name": "Tranchée (sens 2)"},
]


def _extract_bus_api_key(value: str) -> str:
    """Extract API key from value: accepts raw key or full URL with ?apiKey=xxx."""
    if not value or not isinstance(value, str):
        return ""
    value = value.strip()
    if "apiKey=" in value or "apikey=" in value.lower():
        import re
        m = re.search(r"apiKey=([a-zA-Z0-9]+)", value, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return value


def _fetch_bus_stop(api_key: str, stop_id: str, include_alerts: bool = True) -> dict | None:
    """Fetch real-time departures for a stop from Mecatran API."""
    import urllib.request
    import urllib.error
    params = f"apiKey={api_key}&lookAheadSec=86400"  # 24h pour avoir tous les prochains départs
    if include_alerts:
        params += "&includeAlerts=true&preferredLang=fr"
    url = f"https://app.mecatran.com/utw/ws/realtime/stop/stran-merge/{stop_id}?{params}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError, OSError):
        return None


# Cache GTFS statique : données brutes par clé API (refresh 1h), départs recalculés à chaque requête
_GTFS_RAW_CACHE: dict[str, tuple[dict, float]] = {}
_GTFS_CACHE_TTL = 3600  # 1 heure


def _parse_gtfs_time(s: str) -> tuple[int, int, int]:
    """Parse HH:MM:SS or H:MM:SS, support 25:30:00 for next day. Returns (hours, minutes, seconds)."""
    if not s or not isinstance(s, str):
        return (0, 0, 0)
    parts = s.strip().split(":")
    h = int(parts[0]) if len(parts) > 0 else 0
    m = int(parts[1]) if len(parts) > 1 else 0
    sec = int(parts[2]) if len(parts) > 2 else 0
    return (h, m, sec)


def _gtfs_time_to_minutes(h: int, m: int, sec: int) -> int:
    """Convert to minutes since midnight. 25:30 -> 24*60+90 = 1530 (next day offset)."""
    return h * 60 + m + (sec // 60)


def _fetch_gtfs_raw(api_key: str) -> dict | None:
    """Télécharge le GTFS STRAN (zip) et retourne les tables brutes (routes, trips, stop_times, etc.)."""
    import urllib.request
    import urllib.error
    url = f"https://app.mecatran.com/utw/ws/gtfsfeed/static/stran-merge?apiKey={api_key}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/zip, application/octet-stream, */*"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None
    if not data or len(data) < 100:
        return None
    try:
        z = zipfile.ZipFile(io.BytesIO(data), "r")
    except zipfile.BadZipFile:
        return None
    # Lire les fichiers GTFS
    def read_csv(name: str) -> list[dict]:
        try:
            with z.open(name) as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                return list(reader)
        except KeyError:
            return []
    routes_rows = read_csv("routes.txt")
    trips_rows = read_csv("trips.txt")
    stop_times_rows = read_csv("stop_times.txt")
    calendar_rows = read_csv("calendar.txt")
    calendar_dates_rows = read_csv("calendar_dates.txt")
    stops_rows = read_csv("stops.txt")
    if not stop_times_rows or not trips_rows:
        return None
    routes_map = {r.get("route_id", ""): r.get("route_short_name", r.get("route_id", "")) for r in routes_rows}
    trips_map = {}
    for t in trips_rows:
        rid = t.get("route_id", "")
        trips_map[t.get("trip_id", "")] = (
            routes_map.get(rid, rid),
            t.get("trip_headsign", ""),
            t.get("service_id", ""),
        )
    our_stop_ids = {s["id"] for s in BUS_STOPS}
    gtfs_to_our: dict[str, str] = {}
    for s in stops_rows:
        sid = (s.get("stop_id") or "").strip()
        scode = (s.get("stop_code") or "").strip()
        if scode and scode in our_stop_ids:
            gtfs_to_our[sid] = scode
        elif sid and sid in our_stop_ids:
            gtfs_to_our[sid] = sid
    return {
        "routes_map": routes_map,
        "trips_map": trips_map,
        "gtfs_to_our": gtfs_to_our,
        "stop_times_rows": stop_times_rows,
        "calendar_rows": calendar_rows,
        "calendar_dates_rows": calendar_dates_rows,
        "trips_rows": trips_rows,
    }


def _compute_gtfs_departures(raw: dict) -> dict[str, list[tuple[str, str, int, str]]]:
    """Calcule les prochains départs à partir des données GTFS brutes (heure actuelle)."""
    stop_times_rows = raw["stop_times_rows"]
    trips_map = raw["trips_map"]
    gtfs_to_our = raw["gtfs_to_our"]
    calendar_rows = raw["calendar_rows"]
    calendar_dates_rows = raw["calendar_dates_rows"]
    trips_rows = raw["trips_rows"]
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Paris")
    except ImportError:
        tz = timezone.utc
    now = datetime.now(tz)
    today = now.date()
    tomorrow = today + timedelta(days=1)
    weekday_map = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    dow = weekday_map[today.weekday()]
    dow_tomorrow = weekday_map[tomorrow.weekday()]
    active_services = set()
    for c in calendar_rows:
        sid = c.get("service_id", "")
        start_s = c.get("start_date", "")
        end_s = c.get("end_date", "")
        if not sid or not start_s or not end_s:
            continue
        try:
            start_d = datetime.strptime(start_s, "%Y%m%d").date()
            end_d = datetime.strptime(end_s, "%Y%m%d").date()
        except ValueError:
            continue
        if today < start_d or today > end_d:
            continue
        if c.get(dow, "0") == "1":
            active_services.add(sid)
    for c in calendar_rows:
        sid = c.get("service_id", "")
        start_s = c.get("start_date", "")
        end_s = c.get("end_date", "")
        if not sid or not start_s or not end_s:
            continue
        try:
            start_d = datetime.strptime(start_s, "%Y%m%d").date()
            end_d = datetime.strptime(end_s, "%Y%m%d").date()
        except ValueError:
            continue
        if tomorrow < start_d or tomorrow > end_d:
            continue
        if c.get(dow_tomorrow, "0") == "1":
            active_services.add(f"{sid}_tomorrow")
    for cd in calendar_dates_rows:
        sid = cd.get("service_id", "")
        dt_s = cd.get("date", "")
        ex = cd.get("exception_type", "1")
        if not sid or not dt_s:
            continue
        try:
            d = datetime.strptime(dt_s, "%Y%m%d").date()
        except ValueError:
            continue
        if ex == "1":
            if d == today:
                active_services.add(sid)
            elif d == tomorrow:
                active_services.add(f"{sid}_tomorrow")
        elif ex == "2":
            if d == today:
                active_services.discard(sid)
            if d == tomorrow:
                active_services.discard(f"{sid}_tomorrow")
    if not active_services:
        for t in trips_rows:
            sid = t.get("service_id", "")
            if sid:
                active_services.add(sid)
                active_services.add(f"{sid}_tomorrow")
    now_min = now.hour * 60 + now.minute
    look_ahead_min = 24 * 60  # 24h pour afficher toutes les lignes avec leur prochain passage
    result_by_stop: dict[str, list[tuple[str, str, int, str]]] = {}
    # D'abord collecter toutes les lignes (route, headsign) par arrêt (services actifs uniquement)
    lines_per_stop: dict[str, set[tuple[str, str]]] = {}
    for st in stop_times_rows:
        trip_id = st.get("trip_id", "")
        stop_id = (st.get("stop_id") or "").strip()
        trip_info = trips_map.get(trip_id)
        if not trip_info:
            continue
        route_short, headsign, service_id = trip_info
        if service_id not in active_services and f"{service_id}_tomorrow" not in active_services:
            continue
        our_id = gtfs_to_our.get(stop_id)
        if our_id:
            if our_id not in lines_per_stop:
                lines_per_stop[our_id] = set()
            lines_per_stop[our_id].add((route_short, headsign))
    for st in stop_times_rows:
        trip_id = st.get("trip_id", "")
        stop_id = (st.get("stop_id") or "").strip()
        dep_s = (st.get("departure_time") or st.get("arrival_time") or "").strip()
        if not trip_id or not stop_id or not dep_s:
            continue
        trip_info = trips_map.get(trip_id)
        if not trip_info:
            continue
        route_short, headsign, service_id = trip_info
        h, m, sec = _parse_gtfs_time(dep_s)
        dep_min_raw = _gtfs_time_to_minutes(h, m, sec)
        dep_time_str = f"{h % 24:02d}:{m:02d}" if h < 24 else f"{(h - 24) % 24:02d}:{m:02d}"
        if dep_min_raw >= 24 * 60:
            dep_date = tomorrow
            dep_min_today = dep_min_raw - 24 * 60
            service_ok = f"{service_id}_tomorrow" in active_services
            diff_min = (24 * 60 - now_min) + dep_min_today
        else:
            dep_date = today
            dep_min_today = dep_min_raw
            service_ok = service_id in active_services
            diff_min = dep_min_today - now_min
        if not service_ok:
            continue
        if diff_min < -60:
            continue
        if diff_min > look_ahead_min:
            continue
        our_id = gtfs_to_our.get(stop_id)
        if not our_id:
            continue
        if our_id not in result_by_stop:
            result_by_stop[our_id] = []
        result_by_stop[our_id].append((route_short, headsign, max(0, diff_min), dep_time_str))
    # Par arrêt : grouper par (route, headsign), garder uniquement les 2 prochains départs par ligne
    final_by_stop: dict[str, list[tuple[str, str, int, str]]] = {}
    for stop in BUS_STOPS:
        stop_id = stop["id"]
        all_lines = lines_per_stop.get(stop_id, set())
        deps = result_by_stop.get(stop_id, [])
        deps.sort(key=lambda x: x[2])
        by_line: dict[tuple[str, str], list[tuple[int, str]]] = {}
        for route_short, headsign, mins, time_str in deps:
            key = (route_short, headsign)
            if key not in by_line:
                by_line[key] = []
            if len(by_line[key]) < 2:
                by_line[key].append((mins, time_str))
        # Toutes les lignes qui passent à l'arrêt (les 2 sens avec leurs destinations)
        result_list = []
        for (route_short, headsign) in sorted(all_lines):
            items = by_line.get((route_short, headsign), [])
            if items:
                for mins, time_str in items:
                    result_list.append((route_short, headsign, mins, time_str))
            else:
                result_list.append((route_short, headsign, -1, "—"))  # Ligne sans départ dans la fenêtre
        final_by_stop[stop_id] = result_list
    return final_by_stop


def _get_gtfs_static_departures(api_key: str) -> dict | None:
    """Retourne les départs GTFS statiques. Cache les données brutes 1h par clé API, recalcule les départs à chaque appel."""
    global _GTFS_RAW_CACHE
    now_ts = time.time()
    cached = _GTFS_RAW_CACHE.get(api_key)
    if cached is not None and (now_ts - cached[1]) < _GTFS_CACHE_TTL:
        raw = cached[0]
    else:
        raw = _fetch_gtfs_raw(api_key)
        if raw is not None:
            _GTFS_RAW_CACHE[api_key] = (raw, now_ts)
    if raw is None:
        return None
    return _compute_gtfs_departures(raw)


def _format_departure_for_display(dep: dict, routes: list, now_dt) -> dict | None:
    """Format a departure: route, headsign, status (min/imminent/parti), temps."""
    route_id = dep.get("routeId", "")
    route_info = next((r for r in routes if r.get("id") == route_id), {})
    route_short = route_info.get("shortName", route_id)
    headsign = dep.get("headsign", "")
    dep_time_str = dep.get("departureTime") or dep.get("arrivalTime") or ""
    if not dep_time_str:
        return None
    try:
        dep_dt = datetime.fromisoformat(dep_time_str.replace("Z", "+00:00"))
        if dep_dt.tzinfo is None:
            dep_dt = dep_dt.replace(tzinfo=timezone.utc)
        diff_sec = (dep_dt - now_dt).total_seconds()
        if diff_sec < -60:
            return None  # parti, on ne l'affiche pas
        mins = max(0, int(diff_sec / 60))
        if diff_sec < 0:
            status = "parti"
        elif diff_sec < 120:
            status = "imminent"
        else:
            status = "temps_estime"
        return {
            "route": route_short,
            "headsign": headsign,
            "minutes": mins,
            "time": dep_dt.strftime("%H:%M"),
            "status": status,
        }
    except (ValueError, TypeError):
        return None


def _condense_alert(text: str) -> str:
    """Condense alert text via IA si disponible, sinon tronquer."""
    try:
        import llm_engine
        if llm_engine.is_available():
            fn = getattr(llm_engine, "_call_ollama", None)
            if fn:
                prompt = f"Résume cette perturbation transport en une phrase courte (max 80 car.):\n{text[:400]}"
                result = fn(prompt, temperature=0.1, num_predict=80, timeout=12)
                if result and 10 < len(result.strip()) < 120:
                    return result.strip()[:100]
    except Exception:
        pass
    return (text[:100] + "…") if len(text) > 100 else text


def _fake_bus_data(test_perturbations: bool = False) -> dict:
    """Données de test pour le mode bus. Toutes les lignes, 2 prochains départs max."""
    import random
    # Lignes avec les 2 sens (destination A et B)
    line_destinations = [
        ("H1", "Gavy", "Saint-Nazaire Gare"),
        ("H2", "Trignac", "Pornichet"),
        ("U2", "La Baule", "Saint-Nazaire"),
        ("U4", "Pornichet", "La Baule"),
        ("S1", "Saint-Nazaire", "Trignac"),
        ("L1", "Gavy", "Pornichet"),
    ]
    result = []
    for stop in BUS_STOPS:
        lines = []
        for route, dest_a, dest_b in line_destinations:
            for headsign in (dest_a, dest_b):
                items = []
                for _ in range(2):
                    rnd = random.random()
                    if rnd < 0.2:
                        items.append({"status": "imminent", "label": "Imminent"})
                    else:
                        m = random.choice([3, 8, 12, 18, 45, 90, 180, 360])
                        label = f"{m // 60}h" if m >= 60 else f"{m} min"
                        items.append({"status": "temps_estime", "label": label})
                items = items[:2]
                lines.append({"route": route, "headsign": headsign, "stop_name": stop["name"], "items": items})
        result.append({"stop_id": stop["id"], "name": stop["name"], "lines": lines, "error": False})
    alerts = []
    if test_perturbations:
        alerts = [{
            "text": "Perturbation ligne H1",
            "detail": "Retard d'environ 10 minutes sur la ligne H1 en direction de Gavy. Circulation ralentie suite à un incident. Prévoir un délai supplémentaire."
        }]
    return {"stops": result, "alerts": alerts}


@app.route("/api/debug/bus", methods=["GET"])
@admin_required
def debug_bus_pipeline():
    """Diagnostic pipeline GTFS (admin) : horizon, stop_ids, comptages H2/ligne 7, direction_id."""
    if get_setting("feature_bus_enabled", "true") != "true":
        return jsonify({"error": "feature_bus_disabled"}), 400
    url = (get_setting("bus_gtfs_url", "") or "").strip() or _DEFAULT_BUS_GTFS_URL
    cache_dir = os.path.join(app.instance_path, "gtfs_cache")
    refresh_days = int(get_setting("bus_gtfs_refresh_days", "7") or "7")
    gtfs = bus_gtfs.ensure_gtfs_loaded(url, cache_dir, refresh_days)
    if gtfs is None:
        return jsonify({"error": "gtfs_not_loaded", "gtfs_url": url}), 503
    if ZoneInfo:
        try:
            tz = ZoneInfo("Europe/Paris")
        except Exception:
            tz = timezone.utc
    else:
        tz = timezone.utc
    now_dt = datetime.now(tz)
    stop_ids = _parse_bus_stop_ids_list()
    horizon = _get_bus_compute_horizon_minutes()
    eta_tiers = _parse_bus_eta_tiers()
    deps, no_svc, pipe_dbg = bus_gtfs.compute_next_departures(
        gtfs,
        stop_ids,
        now_dt,
        horizon_minutes=horizon,
        eta_tiers=eta_tiers,
        pipeline_debug=True,
    )
    return jsonify({
        "generated_at": now_dt.isoformat(),
        "no_service_today": no_svc,
        "horizon_compute_minutes": horizon,
        "stop_ids_config": stop_ids,
        "pipeline": pipe_dbg,
        "departures_flat_count": len(deps or []),
    })


@app.route("/api/display/bus", methods=["GET"])
def display_bus():
    """Horaires théoriques GTFS (source principale) + cache court. Voir bus_gtfs.py.
    ?debug=1 ou header X-Bus-Debug: 1 → champ diagnostics (sans cache) pour console navigateur."""
    dbg = request.args.get("debug") in ("1", "true", "yes") or request.headers.get("X-Bus-Debug") == "1"
    if dbg:
        return jsonify(_build_bus_display_payload(include_diagnostics=True))
    return jsonify(_get_bus_display_payload_cached())


def _format_bus_time_label(mins: int) -> str:
    """Format: Imminent si < 2 min, X min si < 60 min, Xh sinon (ex: 6h)."""
    if mins < 0:
        return "—"
    if mins < 2:
        return "Imminent"
    if mins < 60:
        return f"{mins} min"
    return f"{mins // 60}h"


def _display_bus_from_gtfs_static(api_key: str):
    """Affiche les horaires à partir du GTFS statique. Toutes les lignes, 2 prochains départs max."""
    dep_by_stop = _get_gtfs_static_departures(api_key)
    if dep_by_stop is None:
        return jsonify({"stops": [], "alerts": [], "error": "Impossible de charger le GTFS statique"})
    result = []
    for stop in BUS_STOPS:
        deps = dep_by_stop.get(stop["id"], [])
        by_line = {}
        for route_short, headsign, mins, time_str in deps:
            key = (route_short, headsign)
            if key not in by_line:
                by_line[key] = {"route": route_short, "headsign": headsign, "items": []}
            label = _format_bus_time_label(mins)
            status = "imminent" if 0 <= mins < 2 else ("temps_estime" if mins >= 0 else "—")
            by_line[key]["items"].append({"minutes": mins, "status": status, "label": label})
        lines = []
        for v in by_line.values():
            items = [{"status": i["status"], "label": i["label"]} for i in v["items"][:2]]
            lines.append({
                "route": v["route"],
                "headsign": v["headsign"],
                "stop_name": stop["name"],
                "items": items,
            })
        result.append({
            "stop_id": stop["id"],
            "name": stop["name"],
            "lines": lines,
            "error": False,
        })
    return jsonify({"stops": result, "alerts": []})


def _display_bus_from_realtime(api_key: str):
    """Affiche les horaires à partir de l'API temps réel Mecatran. 2 prochains départs max par ligne."""
    now_dt = datetime.now(timezone.utc)
    result = []
    all_alerts = []
    our_route_ids = set()

    for stop in BUS_STOPS:
        data = _fetch_bus_stop(api_key, stop["id"])
        if not data:
            result.append({"stop_id": stop["id"], "name": stop["name"], "lines": [], "error": True})
            continue
        routes = data.get("routes", [])
        deps = data.get("departures", [])
        # Grouper par (route, headsign), garder uniquement les 2 prochains départs par ligne
        by_line = {}
        for d in deps:
            fd = _format_departure_for_display(d, routes, now_dt)
            if not fd:
                continue
            key = (fd["route"], fd["headsign"])
            if key not in by_line:
                by_line[key] = {"route": fd["route"], "headsign": fd["headsign"], "items": []}
            if len(by_line[key]["items"]) < 2:
                by_line[key]["items"].append({"minutes": fd["minutes"], "status": fd["status"]})
            our_route_ids.add(fd["route"])
        lines = []
        for v in by_line.values():
            sorted_items = sorted(v["items"], key=lambda x: (0 if x["status"] == "imminent" else 1 if x["status"] == "temps_estime" else 2, x.get("minutes", 999)))[:2]
            items = []
            for item in sorted_items:
                m = item.get("minutes", 0)
                label = "Parti" if item["status"] == "parti" else _format_bus_time_label(m)
                items.append({"status": item["status"], "label": label})
            lines.append({
                "route": v["route"],
                "headsign": v["headsign"],
                "stop_name": stop["name"],
                "items": items,
            })
        result.append({
            "stop_id": stop["id"],
            "name": stop["name"],
            "lines": lines,
            "error": False,
        })
        # Alerts (perturbations) — afficher si concerne nos lignes ou pas de filtre
        for a in data.get("alerts", []):
            desc = a.get("description") or a.get("descriptionText") or a.get("title") or a.get("headerText") or ""
            if not desc:
                continue
            route_ids = set()
            for k in ("routeIds", "routes", "routeId"):
                v = a.get(k)
                if v: route_ids.update([v] if isinstance(v, str) else v)
            if not route_ids or (route_ids & our_route_ids):
                all_alerts.append({"text": desc})

    # Dédupliquer : résumé court + détail complet à droite
    seen = set()
    alerts_final = []
    for a in all_alerts:
        t = a["text"].strip()
        if t and t not in seen:
            seen.add(t)
            condensed = _condense_alert(t)
            alerts_final.append({"text": condensed, "detail": t[:300] if len(t) > 80 else None})

    return jsonify({"stops": result, "alerts": alerts_final[:3]})


# --------------- Backup & Historique ---------------

BACKUP_DIR = os.path.join(app.instance_path, "backups")
BACKUP_RETENTION_DAYS = 3


def _ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


def _build_backup_data():
    """Construit le JSON complet pour une backup (suggestions, annonces, stats, etc.)."""
    suggestions = [s.to_dict() for s in Suggestion.query.order_by(Suggestion.id).all()]
    votes = [{"suggestion_id": v.suggestion_id, "session_id": v.session_id, "vote_type": v.vote_type} for v in Vote.query.all()]
    args = [{"suggestion_id": a.suggestion_id, "side": a.side, "original_text": a.original_text, "summary": a.summary or a.original_text, "status": a.status} for a in SuggestionArgument.query.all()]
    locations = [l.to_dict(include_placement_ids=True) for l in Location.query.all()]
    announcements = [a.to_dict() for a in Announcement.query.all()]
    proposals = [p.to_dict() for p in OfficialProposal.query.all()]
    proposal_votes = [{"proposal_id": v.proposal_id, "session_id": v.session_id, "vote_type": v.vote_type} for v in ProposalVote.query.all()]
    proposal_args = [{"proposal_id": a.proposal_id, "side": a.side, "original_text": a.original_text, "summary": a.summary or a.original_text, "status": a.status} for a in ProposalArgument.query.all()]
    cvl_infos = [c.to_dict() for c in CvlOfficialInfo.query.all()]
    settings = {r.key: r.value for r in SiteSettings.query.all()}
    presentations = [p.to_dict(include_slides=True) for p in Presentation.query.all()]
    pages = [p.to_dict() for p in DisplayPage.query.all()]
    media = [m.to_dict() for m in MediaFile.query.all()]
    scraped = [s.to_dict() for s in ScrapedNews.query.all()]
    total_votes = sum(s.vote_count for s in Suggestion.query.all())
    by_status = {}
    for s in Suggestion.query.all():
        by_status[s.status] = by_status.get(s.status, 0) + 1
    by_category = {}
    for s in Suggestion.query.all():
        by_category[s.category] = by_category.get(s.category, 0) + 1
    stats = {
        "suggestions_count": len(suggestions),
        "announcements_count": len(announcements),
        "votes_total": total_votes,
        "by_status": by_status,
        "by_category": by_category,
        "proposals_count": len(proposals),
        "locations_count": len(locations),
    }
    return {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stats": stats,
        "suggestions": suggestions,
        "votes": votes,
        "suggestion_arguments": args,
        "locations": locations,
        "announcements": announcements,
        "official_proposals": proposals,
        "proposal_votes": proposal_votes,
        "proposal_arguments": proposal_args,
        "cvl_official_info": cvl_infos,
        "site_settings": settings,
        "presentations": presentations,
        "display_pages": pages,
        "media_files": media,
        "scraped_news": scraped,
    }


def _run_backup() -> Backup | None:
    """Crée une backup et retourne l'enregistrement."""
    _ensure_backup_dir()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"backup_{ts}.json"
    filepath = os.path.join(BACKUP_DIR, filename)
    data = _build_backup_data()
    raw = json.dumps(data, ensure_ascii=False, indent=2)
    size = len(raw.encode("utf-8"))
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(raw)
    backup = Backup(filename=filename, size_bytes=size, stats_json=json.dumps(data["stats"], ensure_ascii=False))
    db.session.add(backup)
    db.session.commit()
    return backup


def _cleanup_expired_backups():
    """Supprime les backups de plus de 3 jours."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=BACKUP_RETENTION_DAYS)
    for b in Backup.query.filter(Backup.created_at < cutoff).all():
        try:
            fp = os.path.join(BACKUP_DIR, b.filename)
            if os.path.exists(fp):
                os.remove(fp)
        except OSError:
            pass
        db.session.delete(b)
    db.session.commit()


@app.route("/api/admin/backups", methods=["GET"])
@admin_required
def list_backups():
    """Liste les backups (disponibles pendant 3 jours)."""
    _cleanup_expired_backups()
    backups = Backup.query.order_by(Backup.created_at.desc()).all()
    return jsonify([b.to_dict() for b in backups])


@app.route("/api/admin/backups", methods=["POST"])
@admin_required
def create_backup():
    """Crée une backup manuelle."""
    try:
        backup = _run_backup()
        return jsonify(backup.to_dict()), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/backups/<int:bid>/preview", methods=["GET"])
@admin_required
def preview_backup(bid):
    """Prévisualisation complète d'une backup (contenu JSON)."""
    b = Backup.query.get_or_404(bid)
    filepath = os.path.join(BACKUP_DIR, b.filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "Fichier backup introuvable"}), 404
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return jsonify(data)


@app.route("/api/admin/backups/<int:bid>/download", methods=["GET"])
@admin_required
def download_backup(bid):
    """Télécharge une backup."""
    b = Backup.query.get_or_404(bid)
    filepath = os.path.join(BACKUP_DIR, b.filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "Fichier backup introuvable"}), 404
    return send_file(filepath, as_attachment=True, download_name=b.filename, mimetype="application/json")


@app.route("/api/admin/backups/<int:bid>/restore", methods=["POST"])
@admin_required
def restore_backup(bid):
    """Restaure le site à partir d'une backup."""
    b = Backup.query.get_or_404(bid)
    filepath = os.path.join(BACKUP_DIR, b.filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "Fichier backup introuvable"}), 404
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("version") != 1:
        return jsonify({"error": "Format de backup non supporté"}), 400
    try:
        Vote.query.delete()
        SuggestionArgument.query.delete()
        Suggestion.query.delete()
        ProposalVote.query.delete()
        ProposalArgument.query.delete()
        OfficialProposal.query.delete()
        Announcement.query.delete()
        CvlOfficialInfo.query.delete()
        Placement.query.delete()
        Location.query.delete()
        SiteSettings.query.delete()
        db.session.commit()

        loc_name_to_id = {}
        for loc_data in data.get("locations", []):
            loc = Location(name=loc_data["name"])
            db.session.add(loc)
            db.session.flush()
            loc_name_to_id[loc_data["name"]] = loc.id
            for pitem in loc_data.get("placements", []):
                pname = pitem.get("name", pitem) if isinstance(pitem, dict) else pitem
                db.session.add(Placement(location_id=loc.id, name=str(pname)))
        db.session.flush()

        old_to_new_sugg = {}
        for s in data.get("suggestions", []):
            loc_id = loc_name_to_id.get(s.get("location_name")) if s.get("location_name") else None
            sugg = Suggestion(
                original_text=s.get("original_text", ""),
                title=s.get("title", ""),
                subtitle=s.get("subtitle", ""),
                keywords=",".join(s.get("keywords", [])) if isinstance(s.get("keywords"), list) else (s.get("keywords") or ""),
                category=s.get("category", "Autre"),
                location_id=loc_id,
                status=s.get("status", "En attente"),
                vote_count=s.get("vote_count", 1),
                needs_debate=s.get("needs_debate", False),
                vote_for=s.get("vote_for", 0),
                vote_against=s.get("vote_against", 0),
            )
            db.session.add(sugg)
            db.session.flush()
            old_to_new_sugg[s.get("id")] = sugg.id

        for v in data.get("votes", []):
            new_sid = old_to_new_sugg.get(v.get("suggestion_id"))
            if new_sid:
                db.session.add(Vote(suggestion_id=new_sid, session_id=v.get("session_id", ""), vote_type=v.get("vote_type", "for")))

        for a in data.get("suggestion_arguments", []):
            new_sid = old_to_new_sugg.get(a.get("suggestion_id"))
            if new_sid:
                orig = a.get("original_text", "") or a.get("summary", "") or ""
                db.session.add(SuggestionArgument(suggestion_id=new_sid, session_id=a.get("session_id", ""), side=a.get("side", "for"), original_text=orig[:5000] if orig else "", summary=a.get("summary", ""), status=a.get("status", "pending")))

        for a in data.get("announcements", []):
            db.session.add(Announcement(title=a.get("title", ""), content=a.get("content", ""), style=a.get("style", "info"), active=a.get("active", True), is_priority=a.get("is_priority", False), extra_info=a.get("extra_info", "")))

        old_to_new_prop = {}
        for p in data.get("official_proposals", []):
            prop = OfficialProposal(content=p.get("content", ""), status=p.get("status", "En cours"), active=p.get("active", True), vote_for=p.get("vote_for", 0), vote_against=p.get("vote_against", 0), proportion=p.get("proportion", 0), feasibility=p.get("feasibility", 0.5), cost=p.get("cost", 0.5), needs_debate=p.get("needs_debate", False))
            db.session.add(prop)
            db.session.flush()
            old_to_new_prop[p.get("id")] = prop.id
            for pv in [x for x in data.get("proposal_votes", []) if x.get("proposal_id") == p.get("id")]:
                db.session.add(ProposalVote(proposal_id=prop.id, session_id=pv.get("session_id", ""), vote_type=pv.get("vote_type", "for")))
        for pa in data.get("proposal_arguments", []):
            new_pid = old_to_new_prop.get(pa.get("proposal_id"))
            if new_pid:
                orig = pa.get("original_text", "") or pa.get("summary", "") or ""
                db.session.add(ProposalArgument(proposal_id=new_pid, session_id=pa.get("session_id", ""), side=pa.get("side", "for"), original_text=orig[:5000] if orig else "", summary=pa.get("summary", ""), status=pa.get("status", "pending")))

        for c in data.get("cvl_official_info", []):
            db.session.add(CvlOfficialInfo(title=c.get("title", ""), content=c.get("content", ""), style=c.get("style", "info"), display_mode=c.get("display_mode", "banner"), active=c.get("active", True), display_order=c.get("display_order", 0)))

        for k, v in data.get("site_settings", {}).items():
            set_setting(k, str(v))

        db.session.commit()
        _log_activity("backup_restored", f"Restauration depuis backup {b.filename}")
        return jsonify({"success": True, "message": "Restauration terminée"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/backup-settings", methods=["GET"])
@admin_required
def get_backup_settings():
    return jsonify({
        "backup_interval_hours": int(get_setting("backup_interval_hours", "0") or "0"),
    })


@app.route("/api/admin/backup-settings", methods=["PUT"])
@admin_required
def update_backup_settings():
    data = request.get_json() or {}
    if "backup_interval_hours" in data:
        v = int(data.get("backup_interval_hours", 0) or 0)
        set_setting("backup_interval_hours", str(max(0, min(168, v))))
    return jsonify({"success": True})


# --------------- API Admin Bus ---------------

@app.route("/api/admin/bus-settings", methods=["GET"])
@admin_required
def get_bus_settings():
    return jsonify({
        "bus_api_key": get_setting("bus_api_key", ""),
        "bus_use_static": get_setting("bus_use_static", "false") == "true",
        "bus_gtfs_url": get_setting("bus_gtfs_url", "") or _DEFAULT_BUS_GTFS_URL,
        "bus_stop_ids": _parse_bus_stop_ids_list(),
        "bus_route_order": _parse_bus_route_order(),
        "bus_eta_imminent_max": int(get_setting("bus_eta_imminent_max", "1") or "1"),
        "bus_eta_soon_max": int(get_setting("bus_eta_soon_max", "3") or "3"),
        "bus_eta_near_max": int(get_setting("bus_eta_near_max", "7") or "7"),
        "bus_compute_horizon_minutes": _get_bus_compute_horizon_minutes(),
        "bus_horizon_minutes": int(get_setting("bus_horizon_minutes", "45") or "45"),
        "bus_max_departures": int(get_setting("bus_max_departures", "32") or "32"),
        "bus_relevance_minutes": int(get_setting("bus_relevance_minutes", "30") or "30"),
        "bus_cache_seconds": int(get_setting("bus_cache_seconds", "60") or "60"),
        "bus_gtfs_refresh_days": int(get_setting("bus_gtfs_refresh_days", "7") or "7"),
        "bus_force_display": get_setting("bus_force_display", "false") == "true",
        "bus_force_display_until": get_setting("bus_force_display_until", ""),
        "bus_restrict_to_schedule": get_setting("bus_restrict_to_schedule", "false") == "true",
        "bus_schedule": _parse_bus_schedule_slots(),
        "bus_alternance_enabled": get_setting("bus_alternance_enabled", "false") == "true",
        "bus_alternance_interval_sec": int(get_setting("bus_alternance_interval_sec", "60") or "60"),
        "bus_tv_show_only_during_schedule": get_setting("bus_tv_show_only_during_schedule", "false") == "true",
        "bus_test_mode": get_setting("bus_test_mode", "false") == "true",
        "bus_test_perturbations": get_setting("bus_test_perturbations", "false") == "true",
        "bus_display_pages": [{"id": p.id, "name": p.name, "slug": p.slug, "bus_excluded": getattr(p, "bus_excluded", False)} for p in DisplayPage.query.order_by(DisplayPage.name).all()],
    })


@app.route("/api/admin/bus-settings", methods=["PUT"])
@admin_required
def update_bus_settings():
    data = request.get_json() or {}
    if "bus_api_key" in data:
        raw = str(data.get("bus_api_key") or "")
        key = _extract_bus_api_key(raw)
        set_setting("bus_api_key", key if key else raw.strip())
    if "bus_use_static" in data:
        set_setting("bus_use_static", "true" if data["bus_use_static"] else "false")
    if "bus_restrict_to_schedule" in data:
        set_setting("bus_restrict_to_schedule", "true" if data["bus_restrict_to_schedule"] else "false")
    if "bus_force_display" in data:
        set_setting("bus_force_display", "true" if data["bus_force_display"] else "false")
    if "bus_force_display_until" in data:
        val = data["bus_force_display_until"]
        set_setting("bus_force_display_until", str(val) if val else "")
    if "bus_schedule" in data:
        sched = data["bus_schedule"]
        if isinstance(sched, list):
            set_setting("bus_schedule", json.dumps(sched, ensure_ascii=False))
    if "bus_alternance_enabled" in data:
        set_setting("bus_alternance_enabled", "true" if data["bus_alternance_enabled"] else "false")
    if "bus_alternance_interval_sec" in data:
        v = int(data["bus_alternance_interval_sec"]) if data["bus_alternance_interval_sec"] else 60
        set_setting("bus_alternance_interval_sec", str(max(10, min(600, v))))
    if "bus_tv_show_only_during_schedule" in data:
        set_setting("bus_tv_show_only_during_schedule", "true" if data["bus_tv_show_only_during_schedule"] else "false")
    if "bus_test_mode" in data:
        set_setting("bus_test_mode", "true" if data["bus_test_mode"] else "false")
    if "bus_test_perturbations" in data:
        set_setting("bus_test_perturbations", "true" if data["bus_test_perturbations"] else "false")
    if "bus_excluded_page_ids" in data:
        ids = [int(x) for x in data["bus_excluded_page_ids"] if isinstance(x, (int, str)) and str(x).isdigit()]
        for page in DisplayPage.query.all():
            page.bus_excluded = page.id in ids
    if "bus_gtfs_url" in data:
        set_setting("bus_gtfs_url", str(data.get("bus_gtfs_url") or "").strip())
        bus_gtfs.reset_gtfs_data()
    if "bus_stop_ids" in data:
        ids = data.get("bus_stop_ids")
        if isinstance(ids, list):
            set_setting("bus_stop_ids", json.dumps([str(x) for x in ids], ensure_ascii=False))
            bus_gtfs.reset_gtfs_data()
    if "bus_route_order" in data:
        arr = data.get("bus_route_order")
        if isinstance(arr, list):
            if len(arr) == 0:
                set_setting("bus_route_order", "")
            else:
                set_setting("bus_route_order", json.dumps([str(x) for x in arr], ensure_ascii=False))
    if "bus_eta_imminent_max" in data:
        v = int(data.get("bus_eta_imminent_max") or 1)
        set_setting("bus_eta_imminent_max", str(max(0, min(15, v))))
    if "bus_eta_soon_max" in data:
        v = int(data.get("bus_eta_soon_max") or 3)
        set_setting("bus_eta_soon_max", str(max(0, min(30, v))))
    if "bus_eta_near_max" in data:
        v = int(data.get("bus_eta_near_max") or 7)
        set_setting("bus_eta_near_max", str(max(0, min(45, v))))
    if "bus_compute_horizon_minutes" in data:
        v = int(data.get("bus_compute_horizon_minutes") or 600)
        v = max(30, min(600, v))
        set_setting("bus_compute_horizon_minutes", str(v))
        set_setting("bus_horizon_minutes", str(v))
    if "bus_horizon_minutes" in data and "bus_compute_horizon_minutes" not in data:
        v = int(data.get("bus_horizon_minutes") or 45)
        set_setting("bus_horizon_minutes", str(max(5, min(600, v))))
    if "bus_max_departures" in data:
        v = int(data.get("bus_max_departures") or 32)
        set_setting("bus_max_departures", str(max(1, min(40, v))))
    if "bus_relevance_minutes" in data:
        v = int(data.get("bus_relevance_minutes") or 30)
        set_setting("bus_relevance_minutes", str(max(5, min(120, v))))
    if "bus_cache_seconds" in data:
        v = int(data.get("bus_cache_seconds") or 60)
        set_setting("bus_cache_seconds", str(max(10, min(300, v))))
    if "bus_gtfs_refresh_days" in data:
        v = int(data.get("bus_gtfs_refresh_days") or 7)
        set_setting("bus_gtfs_refresh_days", str(max(1, min(30, v))))
    _BUS_DISPLAY_CACHE["ts"] = 0.0
    _BUS_DISPLAY_CACHE["payload"] = None
    db.session.commit()
    return jsonify({"success": True})


# --------------- API Activity Logs ---------------

@app.route("/api/admin/activity-logs", methods=["GET"])
@admin_required
def get_activity_logs():
    """Logs en direct : événements du site (suggestions, filtres, etc.)."""
    since_id = request.args.get("since_id", type=int)
    limit = min(int(request.args.get("limit", 150)), 250)
    query = ActivityLog.query.order_by(ActivityLog.id.desc())
    if since_id:
        query = query.filter(ActivityLog.id > since_id)
    logs = query.limit(limit).all()
    return jsonify([log.to_dict() for log in logs])


def _parse_iso_dt(s: str):
    if not s or not str(s).strip():
        return None
    s = str(s).strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _activity_logs_filtered_query():
    """Filtre commun pour export (ordre chronologique)."""
    q = ActivityLog.query.order_by(ActivityLog.created_at.asc(), ActivityLog.id.asc())
    day = request.args.get("day")
    hours = request.args.get("hours", type=int)
    from_s = request.args.get("from")
    to_s = request.args.get("to")
    if day:
        try:
            parts = [int(x) for x in day.split("-")[:3]]
            start = datetime(parts[0], parts[1], parts[2], tzinfo=timezone.utc)
            end = start + timedelta(days=1)
            q = q.filter(ActivityLog.created_at >= start, ActivityLog.created_at < end)
        except Exception:
            pass
    elif hours and hours > 0:
        cut = datetime.now(timezone.utc) - timedelta(hours=min(hours, 24 * 90))
        q = q.filter(ActivityLog.created_at >= cut)
    else:
        f = _parse_iso_dt(from_s or "")
        t = _parse_iso_dt(to_s or "")
        if f:
            q = q.filter(ActivityLog.created_at >= f)
        if t:
            q = q.filter(ActivityLog.created_at <= t)
    return q


@app.route("/api/admin/activity-logs/export", methods=["GET"])
@admin_required
def export_activity_logs():
    """Télécharge les logs : ?day=YYYY-MM-DD | ?hours=N | ?from=&to= (ISO) — format=csv|json"""
    q = _activity_logs_filtered_query()
    fmt = (request.args.get("format") or "csv").lower()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    logs = q.limit(25000).all()
    if fmt == "json":
        return Response(
            json.dumps([log.to_dict() for log in logs], ensure_ascii=False, indent=2),
            mimetype="application/json; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename=activity-logs-{stamp}.json"},
        )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "created_at", "event_type", "message", "detail", "ip", "visitor_id"])
    for log in logs:
        w.writerow([
            log.id,
            log.created_at.isoformat() if log.created_at else "",
            log.event_type,
            (log.message or "").replace("\n", " "),
            (log.detail or "").replace("\n", " "),
            log.ip or "",
            log.visitor_id or "",
        ])
    return Response(
        "\ufeff" + buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=activity-logs-{stamp}.csv"},
    )


@app.route("/api/admin/suggestion-archive", methods=["GET"])
@admin_required
def admin_suggestion_archive_list():
    """Historique des suggestions (y compris supprimées)."""
    qstr = (request.args.get("q") or "").strip().lower()
    status_f = (request.args.get("status") or "").strip()
    deleted_only = request.args.get("deleted_only") == "1"
    query = SuggestionArchive.query.order_by(SuggestionArchive.updated_at.desc())
    if deleted_only:
        query = query.filter(SuggestionArchive.deleted_at.isnot(None))
    if status_f:
        query = query.filter(SuggestionArchive.status == status_f)
    rows = query.limit(800).all()
    if qstr:
        rows = [
            r for r in rows
            if qstr in (r.title or "").lower() or qstr in (r.original_text or "").lower()
            or qstr in str(r.suggestion_id)
        ]
    return jsonify([r.to_dict() for r in rows])


@app.route("/api/admin/suggestion-archive/restore", methods=["POST"])
@admin_required
def admin_restore_suggestion_from_archive():
    """Remet une suggestion supprimée en ligne (recréée avec le même id si possible)."""
    data = request.get_json() or {}
    try:
        sid = int(data.get("suggestion_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "suggestion_id invalide"}), 400
    arch = SuggestionArchive.query.filter_by(suggestion_id=sid).first()
    if not arch:
        return jsonify({"error": "Entrée d’archive introuvable"}), 404
    if Suggestion.query.get(sid):
        return jsonify({"error": "Cette suggestion existe déjà"}), 400
    s = Suggestion(
        id=sid,
        original_text=arch.original_text or "",
        title=(arch.title or "Sans titre")[:200],
        subtitle="",
        keywords="",
        category=arch.category or "Autre",
        location_id=None,
        status="En attente",
        vote_count=0,
        needs_debate=bool(arch.needs_debate),
        vote_for=0,
        vote_against=0,
        reject_reason="",
    )
    try:
        db.session.add(s)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Restauration impossible : {e!s}"[:300]}), 400
    _sync_suggestion_archive(s)
    row = SuggestionArchive.query.filter_by(suggestion_id=sid).first()
    if row:
        row.deleted_at = None
    db.session.commit()
    _log_activity(
        "suggestion_restored",
        f"Suggestion #{sid} remise en ligne depuis l’historique",
        detail=(s.title or "")[:100],
    )
    return jsonify({"suggestion": s.to_dict()})


@app.route("/api/admin/suggestion-archive/export", methods=["GET"])
@admin_required
def admin_suggestion_archive_export():
    """Export CSV/JSON : ?suggestion_id= | ?day= (updated_at) | ?from=&to="""
    query = SuggestionArchive.query.order_by(SuggestionArchive.updated_at.desc())
    sid = request.args.get("suggestion_id", type=int)
    if sid:
        query = query.filter(SuggestionArchive.suggestion_id == sid)
    else:
        day = request.args.get("day")
        from_s = request.args.get("from")
        to_s = request.args.get("to")
        if day:
            try:
                parts = [int(x) for x in day.split("-")[:3]]
                start = datetime(parts[0], parts[1], parts[2], tzinfo=timezone.utc)
                end = start + timedelta(days=1)
                query = query.filter(
                    SuggestionArchive.updated_at >= start,
                    SuggestionArchive.updated_at < end,
                )
            except Exception:
                pass
        else:
            f = _parse_iso_dt(from_s or "")
            t = _parse_iso_dt(to_s or "")
            if f:
                query = query.filter(SuggestionArchive.updated_at >= f)
            if t:
                query = query.filter(SuggestionArchive.updated_at <= t)
    rows = query.all()
    fmt = (request.args.get("format") or "csv").lower()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    if fmt == "json":
        return Response(
            json.dumps([r.to_dict() for r in rows], ensure_ascii=False, indent=2),
            mimetype="application/json; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename=suggestion-archive-{stamp}.json"},
        )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "suggestion_id", "title", "status", "category", "reject_reason", "vote_count",
        "needs_debate", "created_at", "completed_at", "deleted_at", "updated_at", "original_text",
    ])
    for r in rows:
        w.writerow([
            r.suggestion_id,
            r.title or "",
            r.status or "",
            r.category or "",
            (r.reject_reason or "").replace("\n", " "),
            r.vote_count or 0,
            "1" if r.needs_debate else "0",
            r.created_at.isoformat() if r.created_at else "",
            r.completed_at.isoformat() if r.completed_at else "",
            r.deleted_at.isoformat() if r.deleted_at else "",
            r.updated_at.isoformat() if r.updated_at else "",
            (r.original_text or "").replace("\n", " ")[:8000],
        ])
    return Response(
        "\ufeff" + buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=suggestion-archive-{stamp}.csv"},
    )


# --------------- API Media Upload ---------------

@app.route("/api/admin/media", methods=["GET"])
@admin_required
def list_media():
    files = MediaFile.query.order_by(MediaFile.created_at.desc()).all()
    return jsonify([f.to_dict() for f in files])


@app.route("/api/admin/media", methods=["POST"])
@admin_required
def upload_media():
    if "file" not in request.files:
        return jsonify({"error": "Aucun fichier"}), 400
    file = request.files["file"]
    if not file.filename or not _allowed_file(file.filename):
        return jsonify({"error": "Type de fichier non autorisé"}), 400

    ext = file.filename.rsplit(".", 1)[1].lower()
    unique_name = f"{uuid.uuid4().hex[:12]}.{ext}"
    file.save(os.path.join(UPLOAD_FOLDER, unique_name))

    stat = os.stat(os.path.join(UPLOAD_FOLDER, unique_name))
    media = MediaFile(
        filename=unique_name,
        original_name=file.filename,
        mime_type=file.content_type or f"image/{ext}",
        size=stat.st_size,
    )
    db.session.add(media)
    db.session.commit()
    return jsonify(media.to_dict()), 201


@app.route("/api/admin/media/<int:mid>", methods=["DELETE"])
@admin_required
def delete_media(mid):
    media = MediaFile.query.get_or_404(mid)
    filepath = os.path.join(UPLOAD_FOLDER, media.filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    db.session.delete(media)
    db.session.commit()
    return jsonify({"success": True})


# --------------- API Presentations ---------------

@app.route("/api/admin/presentations", methods=["GET"])
@admin_required
def list_presentations():
    presos = Presentation.query.order_by(Presentation.created_at.desc()).all()
    return jsonify([p.to_dict() for p in presos])


@app.route("/api/admin/presentations", methods=["POST"])
@admin_required
def create_presentation():
    data = request.get_json()
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Nom requis"}), 400

    import re as _re
    slug = data.get("slug", "").strip()
    if not slug:
        slug = _re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    slug = _re.sub(r"[^a-z0-9-]", "", slug)

    if Presentation.query.filter_by(slug=slug).first():
        slug = f"{slug}-{uuid.uuid4().hex[:4]}"

    preso = Presentation(name=name, slug=slug, active=True)
    db.session.add(preso)
    db.session.commit()
    return jsonify(preso.to_dict(include_slides=True)), 201


@app.route("/api/admin/presentations/<int:pid>", methods=["GET"])
@admin_required
def get_presentation(pid):
    preso = Presentation.query.get_or_404(pid)
    return jsonify(preso.to_dict(include_slides=True))


@app.route("/api/admin/presentations/<int:pid>", methods=["PUT"])
@admin_required
def update_presentation(pid):
    preso = Presentation.query.get_or_404(pid)
    data = request.get_json()
    if "name" in data:
        preso.name = data["name"]
    if "active" in data:
        preso.active = data["active"]
    db.session.commit()
    return jsonify(preso.to_dict(include_slides=True))


@app.route("/api/admin/presentations/<int:pid>", methods=["DELETE"])
@admin_required
def delete_presentation(pid):
    preso = Presentation.query.get_or_404(pid)
    for page in DisplayPage.query.filter_by(presentation_id=pid).all():
        page.presentation_id = None
    db.session.delete(preso)
    db.session.commit()
    return jsonify({"success": True})


# --------------- API Slides ---------------

def _touch_presentation(preso_id):
    preso = Presentation.query.get(preso_id)
    if preso:
        preso.updated_at = datetime.now(timezone.utc)


@app.route("/api/admin/presentations/<int:pid>/slides", methods=["POST"])
@admin_required
def create_slide(pid):
    preso = Presentation.query.get_or_404(pid)
    data = request.get_json()
    max_pos = db.session.query(db.func.max(Slide.position)).filter_by(
        presentation_id=pid).scalar() or -1

    slide = Slide(
        presentation_id=pid,
        slide_type=data.get("slide_type", "image"),
        position=max_pos + 1,
        duration=data.get("duration", 10),
        transition=data.get("transition", "fade"),
    )
    slide.set_content(data.get("content", {}))
    db.session.add(slide)
    _touch_presentation(pid)
    db.session.commit()
    return jsonify(slide.to_dict()), 201


@app.route("/api/admin/slides/<int:sid>", methods=["PUT"])
@admin_required
def update_slide(sid):
    slide = Slide.query.get_or_404(sid)
    data = request.get_json()
    if "slide_type" in data:
        slide.slide_type = data["slide_type"]
    if "duration" in data:
        slide.duration = data["duration"]
    if "transition" in data:
        slide.transition = data["transition"]
    if "content" in data:
        slide.set_content(data["content"])
    if "position" in data:
        slide.position = data["position"]
    _touch_presentation(slide.presentation_id)
    db.session.commit()
    return jsonify(slide.to_dict())


@app.route("/api/admin/slides/<int:sid>", methods=["DELETE"])
@admin_required
def delete_slide(sid):
    slide = Slide.query.get_or_404(sid)
    pid = slide.presentation_id
    db.session.delete(slide)
    _touch_presentation(pid)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/admin/presentations/<int:pid>/reorder", methods=["PUT"])
@admin_required
def reorder_slides(pid):
    Presentation.query.get_or_404(pid)
    data = request.get_json()
    order = data.get("order", [])
    for i, slide_id in enumerate(order):
        slide = Slide.query.get(slide_id)
        if slide and slide.presentation_id == pid:
            slide.position = i
    _touch_presentation(pid)
    db.session.commit()
    return jsonify({"success": True})


# --------------- API Display Pages ---------------

@app.route("/api/admin/display-pages", methods=["GET"])
@admin_required
def list_display_pages():
    pages = DisplayPage.query.order_by(DisplayPage.created_at.desc()).all()
    return jsonify([p.to_dict() for p in pages])


@app.route("/api/admin/display-pages", methods=["POST"])
@admin_required
def create_display_page():
    data = request.get_json()
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Nom requis"}), 400

    import re as _re
    slug = data.get("slug", "").strip()
    if not slug:
        slug = _re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    slug = _re.sub(r"[^a-z0-9-]", "", slug)

    if DisplayPage.query.filter_by(slug=slug).first():
        slug = f"{slug}-{uuid.uuid4().hex[:4]}"

    page_type = data.get("page_type", "presentation")
    page = DisplayPage(
        name=name, slug=slug,
        presentation_id=data.get("presentation_id") if page_type == "presentation" else None,
        page_type=page_type,
        active=True,
    )
    db.session.add(page)
    db.session.commit()
    return jsonify(page.to_dict()), 201


@app.route("/api/admin/display-pages/<int:dpid>", methods=["PUT"])
@admin_required
def update_display_page(dpid):
    page = DisplayPage.query.get_or_404(dpid)
    data = request.get_json() or {}
    if "name" in data:
        page.name = data["name"]
    if "presentation_id" in data:
        val = data["presentation_id"]
        try:
            pid = int(val) if val is not None and val != "" else 0
            page.presentation_id = pid if pid > 0 else None
        except (TypeError, ValueError):
            page.presentation_id = None
    if "active" in data:
        page.active = data["active"]
    if "page_type" in data:
        page.page_type = data["page_type"]
    if "bus_excluded" in data:
        page.bus_excluded = bool(data["bus_excluded"])
    db.session.commit()
    return jsonify(page.to_dict())


@app.route("/api/admin/display-pages/<int:dpid>", methods=["DELETE"])
@admin_required
def delete_display_page(dpid):
    page = DisplayPage.query.get_or_404(dpid)
    db.session.delete(page)
    db.session.commit()
    return jsonify({"success": True})


# --------------- TV Viewer ---------------

@app.route("/tv/<slug>")
def tv_viewer(slug):
    page = DisplayPage.query.filter_by(slug=slug, active=True).first_or_404()
    return render_template("tv.html", page=page)


@app.route("/api/tv/<slug>", methods=["GET"])
def tv_data(slug):
    if get_setting("feature_display_dynamic_enabled", "true") != "true":
        return jsonify({
            "page_type": "disabled",
            "message": "Affichage dynamique temporairement désactivé",
            "slides": [],
            "name": "",
            "show_bus": False,
            "bus_schedule": _parse_bus_schedule_slots(),
        })
    page = DisplayPage.query.filter_by(slug=slug, active=True).first()
    if not page:
        return jsonify({"slides": [], "name": "", "page_type": "presentation"})

    bus_enabled = get_setting("feature_bus_enabled", "true") == "true"
    bus_excluded = getattr(page, "bus_excluded", False)
    show_bus = _tv_should_show_bus_fullscreen(bus_enabled, bus_excluded)
    bus_schedule = _parse_bus_schedule_slots()

    page_type = getattr(page, "page_type", None) or "presentation"
    if page_type == "autonews":
        exclude = request.args.get("exclude", "")
        exclude_ids = [int(x) for x in exclude.split(",") if x.strip().isdigit()]
        query = _scraped_news_query()
        if exclude_ids:
            query = query.filter(ScrapedNews.id.notin_(exclude_ids))
        articles = query.all()
        if not articles:
            return jsonify({"page_type": "autonews", "article": None, "name": page.name})
        import random
        article = random.choice(articles)
        return jsonify({
            "page_type": "autonews",
            "article": article.to_dict(),
            "name": page.name,
            "show_bus": show_bus,
            "bus_schedule": bus_schedule,
        })

    if not page.presentation_id:
        return jsonify({"slides": [], "name": "", "page_type": "presentation", "show_bus": show_bus, "bus_schedule": bus_schedule})
    preso = Presentation.query.get(page.presentation_id)
    if not preso or not preso.active:
        return jsonify({"slides": [], "name": "", "page_type": "presentation", "show_bus": show_bus, "bus_schedule": bus_schedule})
    return jsonify({
        "name": preso.name,
        "slides": [s.to_dict() for s in preso.slides],
        "updated_at": preso.updated_at.isoformat() if preso.updated_at else None,
        "page_type": "presentation",
        "show_bus": show_bus,
        "bus_schedule": bus_schedule,
    })


@app.route("/api/admin/scraped-news", methods=["GET"])
@admin_required
def admin_list_scraped_news():
    articles = ScrapedNews.query.order_by(ScrapedNews.scraped_at.desc()).all()
    return jsonify([a.to_dict() for a in articles])


@app.route("/api/admin/scraped-news/batches", methods=["GET"])
@admin_required
def admin_list_scrap_batches():
    from sqlalchemy import func
    coalesced = func.coalesce(ScrapedNews.batch_id, "legacy")
    rows = db.session.query(coalesced.label("batch_id"), func.count(ScrapedNews.id).label("count")).group_by(coalesced).all()
    current = get_setting("autonews_current_batch", "")
    batches = [{"batch_id": r[0], "count": r[1]} for r in rows]
    batches.sort(key=lambda b: b["batch_id"], reverse=True)
    return jsonify({"batches": batches, "current_batch": current or None})


@app.route("/api/admin/scraped-news/current-batch", methods=["PUT"])
@admin_required
def admin_set_current_scrap_batch():
    data = request.get_json() or {}
    batch_id = (data.get("batch_id") or "").strip() or None
    set_setting("autonews_current_batch", batch_id or "")
    return jsonify({"current_batch": batch_id})


@app.route("/api/admin/scraped-news/batch/<batch_id>", methods=["DELETE"])
@admin_required
def admin_delete_scrap_batch(batch_id):
    bid = (batch_id or "legacy").strip()
    deleted = ScrapedNews.query.filter_by(batch_id=bid).delete()
    db.session.commit()
    if get_setting("autonews_current_batch", "") == bid:
        set_setting("autonews_current_batch", "")
    return jsonify({"deleted": deleted})


@app.route("/api/admin/scraped-news/run-scrape", methods=["POST"])
@admin_required
def admin_run_scrape():
    _run_autonews_scraper()
    return jsonify({"status": "started"})


@app.route("/api/admin/scraped-news/<int:aid>", methods=["PUT"])
@admin_required
def admin_update_scraped_news(aid):
    article = ScrapedNews.query.get_or_404(aid)
    data = request.get_json() or {}
    if "title" in data:
        article.title = str(data["title"])[:300]
    if "excerpt" in data:
        article.excerpt = str(data["excerpt"])[:500]
    if "summary" in data:
        article.summary = str(data["summary"])[:2000]
    if "image_url" in data:
        article.image_url = str(data["image_url"])[:500]
    db.session.commit()
    return jsonify(article.to_dict())


def _scraped_news_query():
    """Query ScrapedNews filtré par batch actuel si défini."""
    current = get_setting("autonews_current_batch", "").strip()
    if current:
        return ScrapedNews.query.filter_by(batch_id=current)
    return ScrapedNews.query


@app.route("/api/autonews", methods=["GET"])
def get_autonews_article():
    """Retourne un article aléatoire, en excluant les IDs passés en paramètre."""
    exclude = request.args.get("exclude", "")
    exclude_ids = [int(x) for x in exclude.split(",") if x.strip().isdigit()]
    query = _scraped_news_query()
    if exclude_ids:
        query = query.filter(ScrapedNews.id.notin_(exclude_ids))
    articles = query.all()
    if not articles:
        return jsonify(None)
    import random
    article = random.choice(articles)
    return jsonify(article.to_dict())


# --------------- AutoNews Scraper ---------------

def _run_autonews_scraper():
    """Scrape e-lyco actualités, résume avec IA, stocke en DB."""
    def _work():
        with app.app_context():
            try:
                from scraper import scrape_elyco_news
                import llm_engine
                articles = scrape_elyco_news()
                if not articles:
                    return
                batch_id = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
                for art in articles[:15]:
                    existing = ScrapedNews.query.filter_by(
                        title=art["title"][:200], url=art.get("url", "")[:500]
                    ).first()
                    if existing:
                        existing.excerpt = art.get("excerpt", "")[:500]
                        existing.full_text = art.get("full_text", "")[:2000]
                        existing.image_url = art.get("image_url", "")[:500]
                        if not existing.summary and art.get("full_text"):
                            existing.summary = llm_engine.summarize_news(art["title"], art["full_text"]) or art.get("excerpt", "")
                        existing.scraped_at = datetime.now(timezone.utc)
                        existing.batch_id = batch_id
                    else:
                        summary = ""
                        if art.get("full_text"):
                            summary = llm_engine.summarize_news(art["title"], art["full_text"]) or art.get("excerpt", "")
                        if not summary:
                            summary = art.get("excerpt", "")[:300]
                        n = ScrapedNews(
                            title=art["title"],
                            url=art.get("url", ""),
                            image_url=art.get("image_url", ""),
                            excerpt=art.get("excerpt", ""),
                            full_text=art.get("full_text", ""),
                            summary=summary,
                            batch_id=batch_id,
                        )
                        db.session.add(n)
                set_setting("autonews_current_batch", batch_id)
                db.session.commit()
            except Exception:
                pass

    threading.Thread(target=_work, daemon=True).start()


def _schedule_autonews():
    """Lance le scraper au démarrage et toutes les 2h."""
    def _delayed_start():
        time.sleep(5)
        _run_autonews_scraper()
    threading.Thread(target=_delayed_start, daemon=True).start()

    def _loop():
        while True:
            time.sleep(2 * 60 * 60)
            _run_autonews_scraper()
    threading.Thread(target=_loop, daemon=True).start()


def _schedule_backup():
    """Auto-backup selon l'intervalle configuré (heures). 0 = désactivé."""
    def _loop():
        while True:
            time.sleep(60 * 5)
            with app.app_context():
                try:
                    interval = int(get_setting("backup_interval_hours", "0") or "0")
                    if interval <= 0:
                        continue
                    last = Backup.query.order_by(Backup.created_at.desc()).first()
                    now = datetime.now(timezone.utc)
                    last_ts = last.created_at if last and last.created_at else None
                    if last_ts and last_ts.tzinfo is None:
                        last_ts = last_ts.replace(tzinfo=timezone.utc)
                    if last_ts is None or (now - last_ts).total_seconds() >= interval * 3600:
                        _run_backup()
                except Exception:
                    pass

    threading.Thread(target=_loop, daemon=True).start()


def _schedule_calibration_from_completed():
    """Toutes les 5 min : « En cours » depuis 10 h, « Terminée » depuis 2 h → calibration IA."""
    def _loop():
        while True:
            time.sleep(5 * 60)
            try:
                with app.app_context():
                    now = datetime.now(timezone.utc)
                    cutoff_long = now - timedelta(hours=10)
                    cutoff_term = now - timedelta(hours=TERMINATED_CALIBRATION_HOURS)
                    q1 = Suggestion.query.filter(
                        Suggestion.status == "En cours de mise en place",
                        Suggestion.completed_at.isnot(None),
                        Suggestion.completed_at <= cutoff_long,
                        Suggestion.calibrated_at.is_(None),
                    ).all()
                    q2 = Suggestion.query.filter(
                        Suggestion.status == "Terminée",
                        Suggestion.completed_at.isnot(None),
                        Suggestion.completed_at <= cutoff_term,
                        Suggestion.calibrated_at.is_(None),
                    ).all()
                    to_calibrate = q1 + q2
                    for s in to_calibrate:
                        ex = CalibrationExample(
                            original_text=s.original_text or s.title,
                            title=s.title,
                            status="validated",
                            batch_id="auto-completed",
                        )
                        db.session.add(ex)
                        s.calibrated_at = datetime.now(timezone.utc)
                    if to_calibrate:
                        db.session.commit()
                        ai.reload_training_data()
            except Exception:
                pass
    threading.Thread(target=_loop, daemon=True).start()


# --------------- Init ---------------

with app.app_context():
    db.create_all()
    try:
        if not music_utils.spotify_credentials_configured():
            app.logger.warning(
                "Spotify non configuré : Administration → Sondage musique (Client ID / Secret) ou variables SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET."
            )
    except Exception:
        pass
    try:
        from sqlalchemy import text
        db.session.execute(text("ALTER TABLE calibration_details ADD COLUMN suggestion_base VARCHAR(300)"))
        db.session.commit()
    except Exception:
        db.session.rollback()
    try:
        from sqlalchemy import text as _sql_text
        for stmt in (
            "ALTER TABLE proposal_arguments ADD COLUMN reject_reason TEXT DEFAULT ''",
            "ALTER TABLE suggestions ADD COLUMN reject_reason TEXT DEFAULT ''",
            # Anciennes BDD sans migration manuelle (sinon OperationalError sur /api/suggestions)
            "ALTER TABLE suggestions ADD COLUMN importance_score REAL DEFAULT 0",
            "ALTER TABLE suggestions ADD COLUMN subtitle_generated_at_support_count INTEGER DEFAULT 0",
        ):
            try:
                db.session.execute(_sql_text(stmt))
                db.session.commit()
            except Exception:
                db.session.rollback()
    except Exception:
        pass
    try:
        for s in Suggestion.query.all():
            if not SuggestionArchive.query.filter_by(suggestion_id=s.id).first():
                _sync_suggestion_archive(s)
        db.session.commit()
    except Exception:
        db.session.rollback()
    ai.reload_training_data()
    ai.reload_context()

    import llm_engine as _llm_init
    _max = int(get_setting("llm_max_credits", "100"))
    _period = int(get_setting("llm_credits_period_hours", "24"))
    _llm_init.configure_credits(_max, _period)


if __name__ == "__main__":
    _sk = os.environ.get("SECRET_KEY", "")
    _ap = os.environ.get("ADMIN_PASSWORD", "")
    if not _sk or _sk == "lycee-suggestions-secret-key-2026":
        print("[!] SECRET_KEY non defini ou valeur par defaut. Definissez SECRET_KEY en production.")
    if not _ap or _ap == "cvl2026":
        print("[!] ADMIN_PASSWORD non defini ou valeur par defaut. Definissez ADMIN_PASSWORD en production.")
    _schedule_autonews()
    _schedule_calibration_from_completed()
    _schedule_backup()
    app.run(debug=True, port=5000)
