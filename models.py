from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone, timedelta
import json

db = SQLAlchemy()


class Suggestion(db.Model):
    __tablename__ = "suggestions"

    id = db.Column(db.Integer, primary_key=True)
    original_text = db.Column(db.Text, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    subtitle = db.Column(db.Text, default="")
    subtitle_generated_at_support_count = db.Column(db.Integer, default=0)
    keywords = db.Column(db.Text, default="")
    category = db.Column(db.String(50), default="Autre")
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), nullable=True)
    status = db.Column(db.String(50), default="En attente")
    vote_count = db.Column(db.Integer, default=1)
    needs_debate = db.Column(db.Boolean, default=False)
    vote_for = db.Column(db.Integer, default=0)
    vote_against = db.Column(db.Integer, default=0)
    ai_proportion = db.Column(db.Float, default=0.0)
    ai_feasibility = db.Column(db.Float, default=0.5)
    ai_cost = db.Column(db.Float, default=0.5)
    ai_needs_debate = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime, nullable=True)
    calibrated_at = db.Column(db.DateTime, nullable=True)
    reject_reason = db.Column(db.Text, default="")  # motif refus (IA ou admin)
    importance_score = db.Column(db.Float, default=0.0)  # 0–100, agrégat des notes importance
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    location = db.relationship("Location", backref="suggestions")
    votes = db.relationship("Vote", backref="suggestion", cascade="all, delete-orphan")
    arguments = db.relationship("SuggestionArgument", backref="suggestion", cascade="all, delete-orphan",
                                 order_by="SuggestionArgument.created_at")

    VALID_STATUSES = [
        "En attente", "En étude", "Acceptée",
        "Refusée", "En cours de mise en place", "Terminée"
    ]

    VALID_CATEGORIES = [
        "Cantine", "Infrastructure", "Vie scolaire",
        "Pédagogie", "Numérique", "Bien-être", "Autre"
    ]

    def to_dict(self):
        d = {
            "id": self.id,
            "original_text": self.original_text,
            "title": self.title,
            "subtitle": self.subtitle or "",
            "keywords": self.keywords.split(",") if self.keywords else [],
            "category": self.category,
            "location_id": self.location_id,
            "location_name": self.location.name if self.location else None,
            "status": self.status,
            "vote_count": self.vote_count,
            "needs_debate": getattr(self, "needs_debate", False),
            "vote_for": getattr(self, "vote_for", 0),
            "vote_against": getattr(self, "vote_against", 0),
            "ai_proportion": getattr(self, "ai_proportion", None),
            "ai_feasibility": getattr(self, "ai_feasibility", None),
            "ai_cost": getattr(self, "ai_cost", None),
            "ai_needs_debate": getattr(self, "ai_needs_debate", None),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "reject_reason": getattr(self, "reject_reason", None) or "",
            "importance_score": float(getattr(self, "importance_score", 0) or 0),
        }
        if self.status == "Terminée" and self.completed_at:
            ca = self.completed_at
            if ca.tzinfo is None:
                ca = ca.replace(tzinfo=timezone.utc)
            hide_at = ca + timedelta(hours=2)
            now = datetime.now(timezone.utc)
            d["terminée_hide_at"] = hide_at.isoformat()
            d["terminée_seconds_remaining"] = max(0, int((hide_at - now).total_seconds()))
        else:
            d["terminée_hide_at"] = None
            d["terminée_seconds_remaining"] = None
        if getattr(self, "needs_debate", False) and hasattr(self, "arguments"):
            d["arguments_for"] = [a.to_dict() for a in self.arguments if a.side == "for" and a.status == "approved"]
            d["arguments_against"] = [a.to_dict() for a in self.arguments if a.side == "against" and a.status == "approved"]
        return d


class Vote(db.Model):
    __tablename__ = "votes"

    id = db.Column(db.Integer, primary_key=True)
    suggestion_id = db.Column(db.Integer, db.ForeignKey("suggestions.id"), nullable=False)
    session_id = db.Column(db.String(100), nullable=False)
    vote_type = db.Column(db.String(10), default="for")  # "for" | "against" (pour débat)
    original_text = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("suggestion_id", "session_id", name="unique_vote"),
    )


class SuggestionArgument(db.Model):
    """Argument pour ou contre une suggestion à débat."""
    __tablename__ = "suggestion_arguments"

    id = db.Column(db.Integer, primary_key=True)
    suggestion_id = db.Column(db.Integer, db.ForeignKey("suggestions.id"), nullable=False)
    session_id = db.Column(db.String(100), nullable=False)
    side = db.Column(db.String(10), nullable=False)
    original_text = db.Column(db.Text, nullable=False)
    summary = db.Column(db.Text, default="")
    status = db.Column(db.String(20), default="pending")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "side": self.side,
            "summary": self.summary or self.original_text,
            "status": self.status,
        }


class Location(db.Model):
    __tablename__ = "locations"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    placements = db.relationship("Placement", backref="location", cascade="all, delete-orphan")

    def to_dict(self, include_placement_ids=False):
        count = Suggestion.query.filter_by(location_id=self.id).count()
        placements = (
            [{"id": p.id, "name": p.name} for p in self.placements]
            if include_placement_ids
            else [p.name for p in self.placements]
        )
        return {
            "id": self.id,
            "name": self.name,
            "suggestion_count": count,
            "placements": placements,
        }


class Placement(db.Model):
    """Sub-location within a Location (e.g. Batiment B -> salle de dance)."""
    __tablename__ = "placements"

    id = db.Column(db.Integer, primary_key=True)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint("location_id", "name", name="unique_placement_per_location"),)


class CalibrationExample(db.Model):
    __tablename__ = "calibration_examples"

    id = db.Column(db.Integer, primary_key=True)
    original_text = db.Column(db.Text, nullable=False)
    title = db.Column(db.String(200), default="")
    keywords = db.Column(db.Text, default="")
    category = db.Column(db.String(50), default="")
    location = db.Column(db.String(100), default="")
    status = db.Column(db.String(20), default="pending")
    batch_id = db.Column(db.String(50), default="")
    forbidden_words = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    VALID_STATUSES = ["pending", "processed", "validated", "rejected"]

    def to_dict(self):
        return {
            "id": self.id,
            "original_text": self.original_text,
            "title": self.title,
            "keywords": self.keywords.split(",") if self.keywords else [],
            "category": self.category,
            "location": self.location,
            "status": self.status,
            "batch_id": self.batch_id,
            "forbidden_words": self.forbidden_words.split(",") if self.forbidden_words else [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def to_training_dict(self):
        return {
            "message_original": self.original_text,
            "titre_reformule": self.title,
            "mots_cles": self.keywords.split(",") if self.keywords else [],
            "categorie": self.category,
            "lieu": self.location,
            "status": self.status,
            "forbidden_words": self.forbidden_words.split(",") if self.forbidden_words else [],
        }


class SchoolContext(db.Model):
    __tablename__ = "school_context"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, default="")


class ActivityLog(db.Model):
    """Log des événements du site pour le panel admin (en direct)."""
    __tablename__ = "activity_logs"

    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(50), nullable=False)  # suggestion_submitted, suggestion_rejected, suggestion_accepted, filter_blocked, etc.
    message = db.Column(db.Text, default="")
    detail = db.Column(db.Text, default="")  # JSON ou texte additionnel
    ip = db.Column(db.String(64), default="")
    visitor_id = db.Column(db.String(100), default="")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "event_type": self.event_type,
            "message": self.message,
            "detail": self.detail,
            "ip": self.ip or "",
            "visitor_id": self.visitor_id or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class SuggestionArchive(db.Model):
    """Copie durable des suggestions (y compris supprimées) pour l'historique admin."""
    __tablename__ = "suggestion_archive"

    id = db.Column(db.Integer, primary_key=True)
    suggestion_id = db.Column(db.Integer, unique=True, nullable=False, index=True)
    title = db.Column(db.String(200), default="")
    original_text = db.Column(db.Text, default="")
    category = db.Column(db.String(50), default="")
    status = db.Column(db.String(50), default="")
    reject_reason = db.Column(db.Text, default="")
    vote_count = db.Column(db.Integer, default=0)
    needs_debate = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "suggestion_id": self.suggestion_id,
            "title": self.title or "",
            "original_text": self.original_text or "",
            "category": self.category or "",
            "status": self.status or "",
            "reject_reason": self.reject_reason or "",
            "vote_count": self.vote_count or 0,
            "needs_debate": bool(self.needs_debate),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Backup(db.Model):
    """Backup / Historique : snapshot complet du site (suggestions, annonces, stats)."""
    __tablename__ = "backups"

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    size_bytes = db.Column(db.Integer, default=0)
    stats_json = db.Column(db.Text, default="{}")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        import json
        stats = {}
        try:
            stats = json.loads(self.stats_json) if self.stats_json else {}
        except (json.JSONDecodeError, TypeError):
            pass
        return {
            "id": self.id,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "stats": stats,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": (self.created_at + timedelta(days=3)).isoformat() if self.created_at else None,
        }


class SiteSettings(db.Model):
    __tablename__ = "site_settings"

    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text, default="")

    DEFAULTS = {
        "submissions_open": "true",
        "display_mode": "normal",
        "feature_bus_enabled": "true",
        "feature_display_dynamic_enabled": "true",
        "display_waiting_title": "",
        "display_waiting_text": "",
        "llm_max_credits": "100",
        "llm_credits_period_hours": "24",
        "priority_announcement_id": "",
        "bus_api_key": "2e6071036d276153761f0c090b4a45420",
        "bus_force_display": "false",
        "bus_force_display_until": "",
        "bus_schedule": '[{"start":"07:40","end":"08:10"},{"start":"08:50","end":"09:10"},{"start":"09:50","end":"10:15"},{"start":"11:00","end":"11:10"},{"start":"11:50","end":"12:15"},{"start":"13:15","end":"13:30"},{"start":"14:20","end":"14:35"},{"start":"15:25","end":"15:35"},{"start":"16:25","end":"16:35"},{"start":"17:25","end":"17:40"}]',
        "bus_alternance_enabled": "false",
        "bus_alternance_interval_sec": "60",
        "bus_tv_show_only_during_schedule": "false",
        "bus_test_mode": "false",
        "bus_test_perturbations": "false",
        "autonews_current_batch": "",
        "subtitle_like_threshold": "5",
        "spotify_client_id": "",
        "spotify_client_secret": "",
        "feature_music_poll_enabled": "true",
        "feature_official_proposal_enabled": "true",
        "feature_cvl_official_info_enabled": "true",
        "feature_ringtone_banner_enabled": "false",
        "ringtone_selection_json": "",
        "music_poll_deezer_preview_fallback": "true",
    }


class Presentation(db.Model):
    __tablename__ = "presentations"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    slides = db.relationship("Slide", backref="presentation",
                             cascade="all, delete-orphan",
                             order_by="Slide.position")
    pages = db.relationship("DisplayPage", backref="presentation")

    def to_dict(self, include_slides=False):
        d = {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "active": self.active,
            "slide_count": len(self.slides),
            "page_count": len(self.pages),
            "pages": [p.to_dict_short() for p in self.pages],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_slides:
            d["slides"] = [s.to_dict() for s in self.slides]
        return d


class Slide(db.Model):
    __tablename__ = "slides"

    id = db.Column(db.Integer, primary_key=True)
    presentation_id = db.Column(db.Integer, db.ForeignKey("presentations.id"), nullable=False)
    slide_type = db.Column(db.String(30), nullable=False, default="image")
    position = db.Column(db.Integer, default=0)
    duration = db.Column(db.Integer, default=10)
    transition = db.Column(db.String(20), default="fade")
    content = db.Column(db.Text, default="{}")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    VALID_TYPES = ["image", "multi_image", "text", "video", "suggestions", "autonews", "bus", "custom"]
    VALID_TRANSITIONS = ["fade", "slide", "zoom", "cut", "appear"]

    def get_content(self):
        try:
            return json.loads(self.content) if self.content else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_content(self, data):
        self.content = json.dumps(data, ensure_ascii=False)

    def to_dict(self):
        return {
            "id": self.id,
            "presentation_id": self.presentation_id,
            "slide_type": self.slide_type,
            "position": self.position,
            "duration": self.duration,
            "transition": self.transition,
            "content": self.get_content(),
        }


class DisplayPage(db.Model):
    __tablename__ = "display_pages"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    presentation_id = db.Column(db.Integer, db.ForeignKey("presentations.id"), nullable=True)
    page_type = db.Column(db.String(30), default="presentation")  # "presentation" | "autonews"
    active = db.Column(db.Boolean, default=True)
    bus_excluded = db.Column(db.Boolean, default=False)  # Exclure cette page de l'affichage bus
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "presentation_id": self.presentation_id,
            "presentation_name": self.presentation.name if self.presentation else None,
            "page_type": self.page_type or "presentation",
            "active": self.active,
            "bus_excluded": getattr(self, "bus_excluded", False),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def to_dict_short(self):
        return {"id": self.id, "name": self.name, "slug": self.slug}


class ScrapedNews(db.Model):
    """Articles scrapés depuis e-lyco, résumés par l'IA."""
    __tablename__ = "scraped_news"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(300), nullable=False)
    url = db.Column(db.Text, default="")
    image_url = db.Column(db.Text, default="")
    excerpt = db.Column(db.Text, default="")
    summary = db.Column(db.Text, default="")
    full_text = db.Column(db.Text, default="")
    scraped_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    batch_id = db.Column(db.String(50), default="legacy")

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url or "",
            "image_url": self.image_url or "",
            "excerpt": self.excerpt or "",
            "summary": self.summary or self.excerpt or "",
            "scraped_at": self.scraped_at.isoformat() if self.scraped_at else None,
            "batch_id": getattr(self, "batch_id", None) or "legacy",
        }


class MediaFile(db.Model):
    __tablename__ = "media_files"

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    mime_type = db.Column(db.String(50), default="")
    size = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "filename": self.filename,
            "original_name": self.original_name,
            "mime_type": self.mime_type,
            "size": self.size,
            "url": f"/static/uploads/{self.filename}",
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class OfficialProposal(db.Model):
    """Proposition officielle du CVL, affichée en premier partout."""
    __tablename__ = "official_proposals"

    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, default="")
    status = db.Column(db.String(50), default="En cours")
    active = db.Column(db.Boolean, default=True)
    vote_for = db.Column(db.Integer, default=0)
    vote_against = db.Column(db.Integer, default=0)
    proportion = db.Column(db.Float, default=0.0)  # 0-1, impact sur la vie au lycée
    feasibility = db.Column(db.Float, default=0.5)  # 0-1, réalisme du projet
    cost = db.Column(db.Float, default=0.5)  # 0-1, coût relatif (argent, temps, moyens)
    needs_debate = db.Column(db.Boolean, default=False)  # Si True: pour/contre + arguments
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                          onupdate=lambda: datetime.now(timezone.utc))
    published_at = db.Column(db.DateTime, nullable=True)

    votes = db.relationship("ProposalVote", backref="proposal", cascade="all, delete-orphan")
    arguments = db.relationship("ProposalArgument", backref="proposal", cascade="all, delete-orphan",
                                order_by="ProposalArgument.created_at")

    VALID_STATUSES = ["En cours", "En délibération", "Adoptée", "Rejetée", "Reportée"]

    def to_dict(self):
        return {
            "id": self.id,
            "content": self.content or "",
            "status": self.status,
            "active": self.active,
            "vote_for": self.vote_for,
            "vote_against": self.vote_against,
            "proportion": self.proportion,
            "feasibility": self.feasibility,
            "cost": self.cost,
            "needs_debate": self.needs_debate,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "published_at": self.published_at.isoformat() if self.published_at else None,
        }


class ProposalVote(db.Model):
    __tablename__ = "proposal_votes"

    id = db.Column(db.Integer, primary_key=True)
    proposal_id = db.Column(db.Integer, db.ForeignKey("official_proposals.id"), nullable=False)
    session_id = db.Column(db.String(100), nullable=False)
    vote_type = db.Column(db.String(10), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("proposal_id", "session_id", name="unique_proposal_vote"),
    )


class ProposalArgument(db.Model):
    """Argument pour ou contre une proposition à débat, résumé par l'IA."""
    __tablename__ = "proposal_arguments"

    id = db.Column(db.Integer, primary_key=True)
    proposal_id = db.Column(db.Integer, db.ForeignKey("official_proposals.id"), nullable=False)
    session_id = db.Column(db.String(100), nullable=False)
    side = db.Column(db.String(10), nullable=False)  # "for" ou "against"
    original_text = db.Column(db.Text, nullable=False)
    summary = db.Column(db.Text, default="")  # Résumé IA après anti-troll
    status = db.Column(db.String(20), default="pending")  # pending, approved, rejected
    reject_reason = db.Column(db.Text, default="")  # motif si refus (IA / filtre)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    VALID_STATUSES = ["pending", "approved", "rejected"]

    def to_dict(self):
        return {
            "id": self.id,
            "side": self.side,
            "original_text": self.original_text,
            "summary": self.summary or self.original_text,
            "status": self.status,
            "reject_reason": self.reject_reason or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TraceFeedback(db.Model):
    """Feedback sur une simulation de traitement IA (traçabilité)."""
    __tablename__ = "trace_feedback"

    id = db.Column(db.Integer, primary_key=True)
    original_text = db.Column(db.Text, nullable=False)
    main_result = db.Column(db.Text, default="")  # JSON
    verify_result = db.Column(db.Text, default="")  # JSON
    user_validated = db.Column(db.Boolean, nullable=True)  # True=ok, False=refusé, None=modifié
    user_correction = db.Column(db.Text, default="")  # JSON si modifié
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        import json
        return {
            "id": self.id,
            "original_text": self.original_text,
            "main_result": json.loads(self.main_result) if self.main_result else {},
            "verify_result": json.loads(self.verify_result) if self.verify_result else {},
            "user_validated": self.user_validated,
            "user_correction": json.loads(self.user_correction) if self.user_correction else {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class CalibrationVerification(db.Model):
    """Exemples de correction pour l'IA de vérification (original → main → correction attendue)."""
    __tablename__ = "calibration_verification"

    id = db.Column(db.Integer, primary_key=True)
    original_text = db.Column(db.Text, nullable=False)
    main_result = db.Column(db.Text, default="")  # JSON (résultat IA principale)
    verify_result = db.Column(db.Text, default="")  # JSON (résultat IA vérification avant correction)
    correction = db.Column(db.Text, nullable=False)  # JSON (correction validée par l'admin)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        import json
        return {
            "id": self.id,
            "original_text": self.original_text,
            "main_result": json.loads(self.main_result) if self.main_result else {},
            "verify_result": json.loads(self.verify_result) if self.verify_result else {},
            "correction": json.loads(self.correction) if self.correction else {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class CalibrationDebat(db.Model):
    """Exemples pour entraîner l'IA à décider si une proposition mérite un débat."""
    __tablename__ = "calibration_debat"

    id = db.Column(db.Integer, primary_key=True)
    proposition = db.Column(db.Text, nullable=False)
    needs_debate = db.Column(db.Boolean, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "proposition": self.proposition,
            "needs_debate": self.needs_debate,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class CalibrationDetails(db.Model):
    """Exemples pour entraîner l'IA à détecter si une suggestion manque de détails (lieu, impact, etc.)."""
    __tablename__ = "calibration_details"

    id = db.Column(db.Integer, primary_key=True)
    suggestion_text = db.Column(db.Text, nullable=False)
    hint = db.Column(db.String(120), nullable=True)  # Question courte (max 12 mots) ou null si "non"
    suggestion_base = db.Column(db.String(300), nullable=True)  # Suggestion de base pour regrouper les exemples
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "suggestion_text": self.suggestion_text,
            "hint": self.hint,
            "suggestion_base": self.suggestion_base,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class CalibrationRapport(db.Model):
    """Exemples pour entraîner l'IA : la nouvelle suggestion a-t-elle un rapport avec l'existante ? Est-ce une précision ?"""
    __tablename__ = "calibration_rapport"

    id = db.Column(db.Integer, primary_key=True)
    existing_text = db.Column(db.Text, nullable=False)  # Texte de la suggestion existante
    new_text = db.Column(db.Text, nullable=False)  # Nouvelle suggestion soumise
    has_rapport = db.Column(db.Boolean, nullable=False)  # Même sujet / connexion ?
    is_precision = db.Column(db.Boolean, nullable=False)  # Apporte des précisions (détails) à l'existante ?
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "existing_text": self.existing_text,
            "new_text": self.new_text,
            "has_rapport": self.has_rapport,
            "is_precision": self.is_precision,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class CvlOfficialInfo(db.Model):
    """Information officielle du CVL : affichée en haut de la page Boîte à Idées, sans vote ni argument."""
    __tablename__ = "cvl_official_info"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, default="")
    style = db.Column(db.String(20), default="info")  # info, success, warning, urgent
    display_mode = db.Column(db.String(20), default="banner")  # banner, compact, full
    active = db.Column(db.Boolean, default=True)
    display_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    VALID_STYLES = ["info", "success", "warning", "urgent"]
    VALID_MODES = ["banner", "compact", "full"]

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "style": self.style,
            "display_mode": self.display_mode,
            "active": self.active,
            "display_order": self.display_order,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Announcement(db.Model):
    __tablename__ = "announcements"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, default="")
    style = db.Column(db.String(20), default="info")
    active = db.Column(db.Boolean, default=True)
    is_priority = db.Column(db.Boolean, default=False)
    extra_info = db.Column(db.Text, default="")
    duration_minutes = db.Column(db.Integer, default=60)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime, nullable=True)

    VALID_STYLES = ["info", "success", "warning", "urgent"]

    def is_active(self):
        if not self.active:
            return False
        if self.expires_at:
            now = datetime.now(timezone.utc)
            exp = self.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if now > exp:
                return False
        return True

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "style": self.style,
            "active": self.is_active(),
            "is_priority": getattr(self, "is_priority", False),
            "extra_info": getattr(self, "extra_info", "") or "",
            "duration_minutes": self.duration_minutes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


class SuggestionImportance(db.Model):
    """Note d'importance (1–4) par session sur une suggestion."""
    __tablename__ = "suggestion_importance"

    id = db.Column(db.Integer, primary_key=True)
    suggestion_id = db.Column(db.Integer, db.ForeignKey("suggestions.id"), nullable=False)
    session_id = db.Column(db.String(100), nullable=False)
    level = db.Column(db.Integer, nullable=False)  # 1 = pas important … 4 = très important
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("suggestion_id", "session_id", name="unique_importance_per_session"),
    )


class DailySessionActivity(db.Model):
    """Activité quotidienne (swipes, likes) pour stats et percentile."""
    __tablename__ = "daily_session_activity"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(100), nullable=False)
    day = db.Column(db.String(10), nullable=False)  # YYYY-MM-DD (Europe/Paris)
    swipe_count = db.Column(db.Integer, default=0)
    like_count = db.Column(db.Integer, default=0)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("session_id", "day", name="unique_activity_per_day"),
    )


class DailyPresence(db.Model):
    """Présence « connecté aujourd'hui » (une ligne par session et par jour)."""
    __tablename__ = "daily_presence"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(100), nullable=False)
    day = db.Column(db.String(10), nullable=False)
    first_seen_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("session_id", "day", name="unique_presence_per_day"),
    )


class EngagementCardDone(db.Model):
    """Carte engagement déjà vue / complétée aujourd'hui (max une fois par type)."""
    __tablename__ = "engagement_card_done"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(100), nullable=False)
    day = db.Column(db.String(10), nullable=False)
    card_type = db.Column(db.String(32), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("session_id", "day", "card_type", name="unique_engagement_card_day"),
    )


class EngagementGuess(db.Model):
    """Réponse au jeu « devine l'avis »."""
    __tablename__ = "engagement_guess"

    id = db.Column(db.Integer, primary_key=True)
    suggestion_id = db.Column(db.Integer, db.ForeignKey("suggestions.id"), nullable=False)
    session_id = db.Column(db.String(100), nullable=False)
    bucket = db.Column(db.String(16), nullable=False)  # lt30 | mid | gt60
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("suggestion_id", "session_id", name="unique_guess_per_suggestion"),
    )


class CommunityMessage(db.Model):
    """Message libre modéré (pseudo + texte)."""
    __tablename__ = "community_messages"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(100), nullable=False)
    display_name = db.Column(db.String(80), nullable=False)
    body = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="approved")  # approved | rejected
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class DailyMood(db.Model):
    """Humeur du jour (une par session et par jour)."""
    __tablename__ = "daily_mood"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(100), nullable=False)
    day = db.Column(db.String(10), nullable=False)
    mood = db.Column(db.String(20), nullable=False)  # bien | bof | fatigue | stresse
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("session_id", "day", name="unique_mood_per_day"),
    )


class Dilemma(db.Model):
    """Dilemme planifié pour un jour (tous les élèves voient le même)."""
    __tablename__ = "dilemmas"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(220), nullable=False)
    option_a = db.Column(db.String(500), nullable=False)
    option_b = db.Column(db.String(500), nullable=False)
    scheduled_day = db.Column(db.String(10), nullable=False, index=True)  # YYYY-MM-DD (Paris ou serveur)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("scheduled_day", name="unique_dilemma_per_day"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "option_a": self.option_a,
            "option_b": self.option_b,
            "scheduled_day": self.scheduled_day,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class DilemmaVote(db.Model):
    """Vote A ou B pour un dilemme (une fois par session par dilemme)."""
    __tablename__ = "dilemma_votes"

    id = db.Column(db.Integer, primary_key=True)
    dilemma_id = db.Column(db.Integer, db.ForeignKey("dilemmas.id"), nullable=False)
    session_id = db.Column(db.String(100), nullable=False)
    side = db.Column(db.String(1), nullable=False)  # a | b
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("dilemma_id", "session_id", name="unique_dilemma_vote_per_session"),
    )


class MusicPoll(db.Model):
    """Sondage musique (Spotify) — au plus un actif à la fois (mur + swipe)."""
    __tablename__ = "music_poll"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    is_active = db.Column(db.Boolean, default=False)
    max_votes = db.Column(db.Integer, default=1)
    end_date = db.Column(db.DateTime, nullable=True)
    spotify_playlist_url = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    tracks = db.relationship(
        "MusicTrack",
        backref="poll",
        cascade="all, delete-orphan",
        order_by="MusicTrack.position",
    )


class MusicTrack(db.Model):
    __tablename__ = "music_tracks"

    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey("music_poll.id"), nullable=False, index=True)
    spotify_url = db.Column(db.String(500), nullable=False)
    spotify_track_id = db.Column(db.String(30), nullable=False, index=True)
    title = db.Column(db.String(300), nullable=False)
    artist = db.Column(db.String(200), nullable=False, default="")
    album = db.Column(db.String(200), nullable=False, default="")
    thumbnail_url = db.Column(db.String(500), nullable=True)
    preview_url = db.Column(db.String(500), nullable=True)
    vote_count = db.Column(db.Integer, default=0)
    position = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    votes = db.relationship("MusicVote", backref="track", cascade="all, delete-orphan")


class MusicVote(db.Model):
    __tablename__ = "music_votes"

    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey("music_poll.id"), nullable=False, index=True)
    track_id = db.Column(db.Integer, db.ForeignKey("music_tracks.id"), nullable=False, index=True)
    session_id = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("track_id", "session_id", name="uq_music_vote_track_session"),
    )
