import re
from unidecode import unidecode

LEET_MAP = {
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s",
    "7": "t", "8": "b", "@": "a", "$": "s", "!": "i",
    "|": "l", "(": "c", ")": "d", "{": "c", "}": "d",
    "€": "e", "£": "l", "¥": "y",
}

# Inclut des formes verlan / déformées courantes (ex. « tepu » = « pute » inversé).
BLOCKED_WORDS = [
    "connard", "connasse", "salaud", "salope", "pute", "putain",
    "tepu", "tepue", "tepuu",
    "merde", "enculé", "enculer", "nique", "niquer", "ntm",
    "fdp", "tg", "ta gueule", "ferme ta gueule",
    "pd", "pédé", "gouine", "batard", "bâtard",
    "con", "conne", "abruti", "abrutie", "crétin", "crétine",
    "débile", "idiot", "idiote", "imbécile",
    "bouffon", "bouffonne", "tocard", "tocarde",
    "fils de pute", "fils de chien", "nique ta mère",
    "va te faire", "va crever", "dégage",
    "nazi", "raciste", "négro", "nègre",
    "chier", "foutre", "bordel",
    "couille", "bite", "queue", "cul",
    "enfoiré", "enfoirée", "branleur", "branleuse",
    "pouffiasse", "grognasse", "pétasse",
    "wesh", "wallah", "starfoulah",
]

SPAM_PATTERNS = [
    r"(.)\1{4,}",
    r"^[A-Z\s]{20,}$",
    r"(https?://|www\.)\S+",
    r"(.{2,}?)\1{3,}",
]

RELEVANCE_STOPWORDS = {
    "le", "la", "les", "de", "des", "du", "un", "une", "et", "est", "en",
    "que", "qui", "dans", "ce", "il", "elle", "au", "aux", "son", "sa",
    "ses", "sur", "pas", "pour", "ne", "se", "par", "avec", "tout", "mais",
    "ou", "nous", "vous", "ils", "elles", "leur", "leurs", "mon", "ma",
    "mes", "ton", "ta", "tes", "notre", "nos", "votre", "vos", "etre",
    "avoir", "fait", "faire", "dit", "dire", "comme", "plus", "aussi",
    "bien", "tres", "trop", "peu", "moins", "ca", "cette", "ces", "sont",
    "ont", "entre", "apres", "avant", "quand", "comment", "pourquoi",
    "si", "non", "oui", "alors", "donc", "car", "ni", "meme", "autre",
    "autres", "chaque", "tous", "toute", "toutes", "rien", "sans", "sous",
    "chez", "vers", "dont", "fois", "peut", "euh", "bah", "ben", "genre",
    "quoi", "voila", "hein", "bon", "bref", "on", "ici", "moi", "toi",
    "lui", "soi", "eux", "oui", "non", "merci", "bonjour", "salut",
}

SCHOOL_CONTEXT_ROOTS = [
    # Cantine / nourriture
    "cantin", "self", "repas", "nourri", "manger", "menu", "dejeu",
    "plat", "boiss", "salade", "desser", "fruit", "viand", "veget",
    "vegan", "micro", "distrib", "snack", "ketch", "mayon", "sauce",
    "petit-dej", "gouter", "allerg", "halal", "cuisin",
    # Infrastructure
    "batim", "salle", "porte", "fenet", "chauff", "climati", "clim",
    "toilet", "escali", "ascens", "parkin", "casier", "vestia",
    "eclair", "lampe", "peintu", "renov", "banc", "table", "chais",
    "preau", "auvent", "poubel", "recycl", "fontain", "robin",
    "couloir", "hall", "radiat", "isol",
    # Vie scolaire
    "horair", "emploi", "recrea", "pause", "perman", "absenc",
    "retard", "reglem", "sorti", "intern", "foyer", "club",
    "associ", "evenem", "voyage", "activi", "survei", "pion",
    "carnav", "soiree", "journee", "demi-pension", "extern",
    # Pédagogie
    "cours", "profess", "prof", "examen", "note", "devoir",
    "progra", "matier", "option", "orient", "stage", "format",
    "enseign", "aide", "soutien", "tutora", "bac", "brevet",
    "control", "evalua", "rattra", "revisi",
    # Numérique
    "wifi", "internet", "ordina", "tablet", "imprim", "logici",
    "applic", "reseau", "pronot", "numer", "inform", "ecran",
    "project", "videopro", "prise", "charge", "clavi", "souris",
    # Bien-être
    "stress", "harcel", "bruit", "calme", "detent", "sport",
    "sante", "infirm", "psycho", "bien-etre", "confor", "ambian",
    "propre", "hygien", "musiqu", "plant", "jardin", "verdur",
    "repos", "sieste", "meditat",
    # Termes généraux lycée
    "lycee", "colleg", "ecole", "eleve", "etudia", "class",
    "biblio", "gymnas", "labo", "amphi", "admin", "secret",
    "provis", "princi", "cour", "cdi", "recre", "educa",
    # Verbes de suggestion / demande
    "ajout", "install", "amelio", "chang", "remplac", "repar",
    "supprim", "augment", "redui", "autori", "interdi", "propos",
    "organi", "permet", "rajout", "enlev", "nettoy", "achet",
    "constr", "agrand", "prolong", "raccour", "modifi",
    "equip", "rendr", "offri", "arret", "commenc", "develop",
    # Indicateurs de problème / besoin
    "manqu", "besoin", "proble", "souci", "demand", "souhai",
    "sugges", "idee", "proposi", "voudr", "faudr", "devr",
    "pourr", "casse", "abime", "fuite", "panne", "sale",
    "degout", "degoutan", "odeur", "danger", "securit",
]


def normalize_leet(text: str) -> str:
    result = []
    for ch in text:
        result.append(LEET_MAP.get(ch, ch))
    return "".join(result)


def normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = normalize_leet(text)
    text = unidecode(text)
    text = re.sub(r"[^a-z\sàâäéèêëïîôùûüÿçœæ]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def check_profanity(text: str) -> bool:
    """Return True if the text contains profanity."""
    normalized = normalize_text(text)

    for word in BLOCKED_WORDS:
        pattern = r"\b" + re.escape(unidecode(word.lower())) + r"\b"
        if re.search(pattern, normalized):
            return True

    spaced = re.sub(r"\s", "", normalized)
    for word in BLOCKED_WORDS:
        clean_word = re.sub(r"\s", "", unidecode(word.lower()))
        if len(clean_word) >= 4 and clean_word in spaced:
            return True

    return False


def check_spam(text: str) -> bool:
    """Return True if the text looks like spam."""
    if len(text.strip()) < 5:
        return True
    if len(text) > 1000:
        return True
    for pattern in SPAM_PATTERNS:
        if re.search(pattern, text):
            return True
    return False


def check_relevance(text: str) -> bool:
    """Return True if the text seems irrelevant to school suggestions."""
    normalized = unidecode(text.lower().strip())
    clean = re.sub(r"[^a-z\s-]", " ", normalized)
    clean = re.sub(r"\s+", " ", clean).strip()

    tokens = [w for w in clean.split() if len(w) > 2 and w not in RELEVANCE_STOPWORDS]

    for token in tokens:
        for root in SCHOOL_CONTEXT_ROOTS:
            if token.startswith(root):
                return False

    if len(tokens) >= 5:
        return False

    return True


_dynamic_blocked_words: list[str] = []


def load_dynamic_blocked_words(words: list[str]):
    global _dynamic_blocked_words
    _dynamic_blocked_words = [w.strip().lower() for w in words if w.strip()]


def check_dynamic_profanity(text: str) -> bool:
    if not _dynamic_blocked_words:
        return False
    normalized = normalize_text(text)
    spaced = re.sub(r"\s", "", normalized)
    for word in _dynamic_blocked_words:
        clean = unidecode(word.lower())
        if len(clean) < 2:
            continue
        pattern = r"\b" + re.escape(clean) + r"\b"
        if re.search(pattern, normalized):
            return True
        if len(clean) >= 4 and clean in spaced:
            return True
    return False


PERSON_NAMING_PATTERNS = [
    r"\b(?:mme|madame|mademoiselle|m\.|mr|monsieur)\s*\.?\s+[A-ZÀ-Ü][a-zà-ü]",
    r"\b(?:le|la|les|un|une)\s+(?:prof|profs|professeur|professeure|surveillant|surveillante|pion|pionne|cpe|directeur|directrice|proviseur|proviseure|principal|principale|intendant|intendante|cuisinier|cuisinière|agent|agente|infirmier|infirmière)\s+(?:de\s+)?[A-ZÀ-Ü][a-zà-ü]",
    r"\b(?:prof|professeur|surveillant|cpe|proviseur|directeur|principal)\s+[A-ZÀ-Ü][a-zà-ü]{2,}",
]


def check_person_naming(text: str) -> bool:
    """Return True if the text names a specific person."""
    for pattern in PERSON_NAMING_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True

    honorifics = re.findall(
        r"\b(?:mme|madame|m\.|mr|monsieur|mademoiselle)\b",
        text, re.IGNORECASE,
    )
    if honorifics:
        return True

    return False


def filter_content_quick(text: str) -> tuple[bool, str]:
    """
    Fast rule-based filter only (no LLM call).
    Used for instant submission -- blocks obvious spam, profanity, naming.
    """
    if not text or not text.strip():
        return False, "Le message est vide."

    if check_spam(text):
        return False, "Ce message a été détecté comme spam."

    if check_profanity(text):
        return False, "Ce message contient du contenu inapproprié."

    if check_dynamic_profanity(text):
        return False, "Ce message contient du contenu inapproprié."

    if check_person_naming(text):
        return False, "Les suggestions ne doivent pas nommer de personnes. Décrivez le problème sans citer de noms."

    return True, ""


def filter_community_message_quick(text: str) -> tuple[bool, str]:
    """
    Filtre messages libres (pseudo + texte) : pas de détection de noms de personnes
    (les pseudos courts sont autorisés). Spam + grossièretés bloqués.
    """
    if not text or not text.strip():
        return False, "Le message est vide."
    if check_spam(text):
        return False, "Ce message a été détecté comme spam."
    if check_profanity(text):
        return False, "Ce message contient du contenu inapproprié."
    if check_dynamic_profanity(text):
        return False, "Ce message contient du contenu inapproprié."
    return True, ""


def filter_content(text: str) -> tuple[bool, str]:
    """
    Full filter including LLM relevance check.
    Used for background AI validation after submission.
    Returns (is_ok, error_message).
    """
    ok, msg = filter_content_quick(text)
    if not ok:
        return False, msg

    import llm_engine
    llm_result = llm_engine.check_relevance_llm(text)
    if llm_result is not None:
        if not llm_result:
            return False, "Votre message ne semble pas être une suggestion recevable. Proposez une amélioration concrète des installations ou services du lycée."
        return True, ""

    if check_relevance(text):
        return False, "Votre message ne semble pas être une suggestion recevable. Proposez une amélioration concrète des installations ou services du lycée."

    return True, ""
