import re
from unidecode import unidecode

import llm_engine

FRENCH_STOPWORDS = {
    "le", "la", "les", "de", "des", "du", "un", "une", "et", "est", "en",
    "que", "qui", "dans", "ce", "il", "elle", "au", "aux", "son", "sa",
    "ses", "sur", "pas", "pour", "ne", "se", "par", "avec", "tout", "mais",
    "ou", "où", "nous", "vous", "ils", "elles", "leur", "leurs", "mon",
    "ma", "mes", "ton", "ta", "tes", "notre", "nos", "votre", "vos",
    "été", "être", "avoir", "fait", "faire", "dit", "dire", "comme",
    "plus", "aussi", "bien", "très", "trop", "peu", "moins", "ça", "ca",
    "cette", "ces", "sont", "ont", "entre", "après", "avant",
    "quand", "quel", "quelle", "quels", "quelles", "comment", "pourquoi",
    "si", "non", "oui", "alors", "donc", "car", "ni", "soit", "même",
    "autre", "autres", "chaque", "tous", "toute", "toutes", "rien",
    "sans", "sous", "chez", "vers", "dont", "fois", "peut", "on",
    "là", "ici", "y", "a", "je", "tu", "me", "te", "lui",
    "euh", "bah", "ben", "genre", "quoi", "voila", "hein", "bon", "bref",
    "serait", "faut", "faudrait", "veux", "veut", "voudrais", "voudrait",
    "devrait", "pourrait", "pense", "trouve", "aimerai", "aimerais",
    "cool", "top", "grave", "trop", "vraiment",
}

FILLER_WORDS = [
    "euh", "bah", "ben", "genre", "en fait", "en gros", "du coup",
    "ça serait bien", "ce serait bien", "il faudrait", "on pourrait",
    "on devrait", "je pense que", "je trouve que", "moi je",
    "svp", "s'il vous plaît", "s'il vous plait", "please", "pls",
    "franchement", "sérieux", "sérieusement", "perso", "personnellement",
    "quoi", "voilà", "hein", "bon", "bref", "enfin",
    "ça serait cool", "ce serait cool", "ça serait top", "ce serait top",
    "est-ce que", "est ce que", "est-ce qu'on", "est ce qu'on",
    "j'aimerais bien", "j'aimerais", "j'aimerai",
    "je voudrais", "on voudrait", "je veux", "on veut",
    "il faut", "faut", "faudrait", "il faudrait qu'on",
    "wsh", "wesh", "wallah", "starfoullah", "frère", "gros",
    "grave", "trop", "carrément", "carrment", "vazy", "vas-y",
    "sah", "ça serait ouf", "ce serait ouf",
]

SLANG_REPLACEMENTS = {
    "cantoche": "cantine", "cantoches": "cantine",
    "perm": "permanence", "perms": "permanences",
    "degeu": "insalubre", "dégueu": "insalubre", "degueu": "insalubre",
    "dégueulasse": "insalubre", "degueulasse": "insalubre",
    "tel": "téléphone", "tels": "téléphones",
    "ordi": "ordinateur", "ordis": "ordinateurs",
    "pc": "ordinateur", "pcs": "ordinateurs",
    "projo": "vidéoprojecteur", "projos": "vidéoprojecteurs",
    "recap": "récapitulatif",
    "cdi": "CDI", "bat": "bâtiment",
    "clim": "climatisation", "clims": "climatisations",
    "toilettes": "sanitaires", "toilette": "sanitaire",
    "wc": "sanitaires",
    "bouffe": "nourriture", "bouffer": "manger",
    "nul": "insuffisant", "nulle": "insuffisante",
    "naze": "défaillant", "nazes": "défaillants",
    "chelou": "inadapté",
    "galère": "difficulté", "galères": "difficultés",
    "ouf": "remarquable", "cool": "appréciable",
    "top": "excellent",
}

CATEGORY_KEYWORDS = {
    "Cantine": [
        "cantine", "self", "repas", "nourriture", "manger", "sauce", "ketchup",
        "mayonnaise", "menu", "déjeuner", "plat", "boisson", "eau", "pain",
        "salade", "dessert", "fruit", "viande", "végétarien", "vegan",
        "micro-onde", "micro onde", "four", "distributeur", "snack",
        "petit-déjeuner", "goûter", "allergène", "halal", "cuisine",
    ],
    "Infrastructure": [
        "bâtiment", "batiment", "salle", "porte", "fenêtre", "fenetre",
        "chauffage", "climatisation", "clim",
        "toilette", "wc", "escalier", "ascenseur", "parking", "casier",
        "vestiaire", "éclairage", "lampe", "peinture", "mur", "sol", "toit",
        "rénovation", "banc", "table", "chaise", "préau", "auvent",
        "poubelle", "tri", "recyclage", "fontaine", "robinet", "cassé", "cassée",
    ],
    "Vie scolaire": [
        "horaire", "emploi du temps", "récréation", "pause", "permanence",
        "absence", "retard", "règlement", "sortie", "internat", "foyer",
        "club", "association", "événement", "fête", "voyage", "sortie scolaire",
        "carnaval", "bal", "soirée", "journée", "semaine", "activité",
        "midi", "demi-pension", "externe", "surveillant", "pion",
    ],
    "Pédagogie": [
        "cours", "professeur", "prof", "examen", "note", "devoir",
        "programme", "matière", "option", "orientation", "stage",
        "formation", "méthode", "enseignement", "aide", "soutien",
        "tutorat", "bac", "brevet", "contrôle", "évaluation", "rattrapage",
        "devoirs", "revision", "révision",
    ],
    "Numérique": [
        "wifi", "internet", "ordinateur", "pc", "tablette", "imprimante",
        "logiciel", "site", "application", "app", "réseau", "pronote",
        "ent", "numérique", "informatique", "écran", "projecteur",
        "vidéoprojecteur", "prise", "chargeur", "usb", "câble",
        "souris", "clavier",
    ],
    "Bien-être": [
        "stress", "harcèlement", "bruit", "calme", "espace", "détente",
        "relaxation", "sport", "santé", "infirmerie", "psychologue",
        "écoute", "bien-être", "confort", "ambiance", "propreté",
        "hygiène", "méditation", "zen", "repos", "sieste", "musique",
        "nature", "plante", "jardin", "verdure",
    ],
}

ABBREVIATION_MAP = {
    "bâtiment": ["bat", "bat.", "bât", "bât.", "batiment"],
    "batiment": ["bat", "bat.", "bât", "bât."],
    "salle": ["sl", "sl."],
    "gymnase": ["gym", "gym."],
    "restaurant": ["resto", "rest.", "rest"],
    "laboratoire": ["labo", "lab", "lab."],
    "bibliothèque": ["biblio", "bib", "bib."],
    "amphithéâtre": ["amphi", "amphi."],
    "informatique": ["info", "info."],
    "permanence": ["perm", "perm."],
    "administration": ["admin", "admin."],
    "self": ["self-service"],
    "cdi": ["centre de documentation"],
    "eps": ["sport", "éducation physique"],
}


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _remove_fillers(text: str) -> str:
    result = text.lower()
    for filler in sorted(FILLER_WORDS, key=len, reverse=True):
        pattern = r"\b" + re.escape(filler) + r"\b"
        result = re.sub(pattern, " ", result, flags=re.IGNORECASE)
        ascii_filler = unidecode(filler)
        if ascii_filler != filler:
            result = re.sub(r"\b" + re.escape(ascii_filler) + r"\b", " ", result, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", result).strip()


def _tokenize(text: str) -> list[str]:
    text = unidecode(text.lower())
    text = re.sub(r"[^a-z\s]", " ", text)
    return [w for w in text.split() if len(w) > 2 and w not in FRENCH_STOPWORDS]


def classify_category(text: str) -> str:
    """Classify using LLM first, fallback to keyword matching."""
    llm_cat = llm_engine.classify_with_llm(text)
    if llm_cat:
        return llm_cat

    tokens = set(_tokenize(text))
    text_lower = unidecode(text.lower())

    scores = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = 0
        for kw in keywords:
            kw_normalized = unidecode(kw.lower())
            if kw_normalized in text_lower:
                score += 2
            elif any(kw_normalized.startswith(t) or t.startswith(kw_normalized) for t in tokens):
                score += 1
        scores[category] = score

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "Autre"


def extract_keywords(text: str, category: str) -> list[str]:
    """Extract keywords using LLM first, fallback to tokenization."""
    llm_kw = llm_engine.extract_keywords_llm(text)
    if llm_kw:
        return llm_kw

    tokens = _tokenize(text)
    keywords = list(dict.fromkeys(tokens))

    cat_keywords = CATEGORY_KEYWORDS.get(category, [])
    text_lower = unidecode(text.lower())
    for kw in cat_keywords:
        if unidecode(kw.lower()) in text_lower and kw.lower() not in keywords:
            keywords.append(kw.lower())

    return keywords[:10]


def reformulate_title(text: str) -> str:
    """Reformulate using LLM first, fallback to rule-based."""
    llm_result = llm_engine.reformulate(text)
    if llm_result:
        return llm_result

    return _rule_based_reformulate(text)


ACTION_VERBS = {
    "ajouter": "Ajout de",
    "mettre": "Mise en place de",
    "installer": "Installation de",
    "créer": "Création de",
    "creer": "Création de",
    "améliorer": "Amélioration de",
    "ameliorer": "Amélioration de",
    "changer": "Changement de",
    "remplacer": "Remplacement de",
    "réparer": "Réparation de",
    "reparer": "Réparation de",
    "ouvrir": "Ouverture de",
    "fermer": "Fermeture de",
    "supprimer": "Suppression de",
    "augmenter": "Augmentation de",
    "réduire": "Réduction de",
    "reduire": "Réduction de",
    "autoriser": "Autorisation de",
    "interdire": "Interdiction de",
}


def _replace_slang(text: str) -> str:
    result = text
    for slang, formal in SLANG_REPLACEMENTS.items():
        pattern = r"\b" + re.escape(slang) + r"\b"
        result = re.sub(pattern, formal, result, flags=re.IGNORECASE)
    return result


def _rule_based_reformulate(text: str) -> str:
    cleaned = _remove_fillers(text)
    cleaned = _replace_slang(cleaned)
    cleaned = re.sub(r"[.!?]+$", "", cleaned).strip()
    if not cleaned:
        cleaned = text.strip()

    cleaned = re.sub(r"\bd[''\s]", "d'", cleaned)
    cleaned = re.sub(r"\bl[''\s]", "l'", cleaned)

    leading_noise = re.match(
        r"^(?:d'|qu'on\s|avoir\s|qu'il\s|que\s|y\s|en\s)+",
        cleaned, re.IGNORECASE,
    )
    if leading_noise:
        cleaned = cleaned[leading_noise.end():]

    words = cleaned.split()

    for i, w in enumerate(words):
        w_low = w.lower()
        if w_low in ACTION_VERBS:
            rest = " ".join(words[i + 1:])
            if rest:
                prefix = ACTION_VERBS[w_low]
                target = re.sub(r"^(de la |de l'|du |des |de |d')", "", rest, flags=re.IGNORECASE)
                if not target or target == rest:
                    target = rest
                sep = "" if prefix.endswith("'") else " "
                return (prefix + sep + target).strip()[:120]
            break

    if len(words) > 10:
        words = words[:10]

    title = " ".join(words)
    title = title[0].upper() + title[1:] if title else text.strip()
    return re.sub(r"\s+", " ", title).strip()[:120]


# --------------- Location Detection ---------------

REVERSE_ABBREV_MAP = {}
for _full, _abbrevs in ABBREVIATION_MAP.items():
    for _a in _abbrevs:
        REVERSE_ABBREV_MAP.setdefault(unidecode(_a.lower().rstrip(".")), []).append(_full)
    REVERSE_ABBREV_MAP.setdefault(unidecode(_full.lower()), []).append(_full)


def _generate_location_variants(name: str) -> list[str]:
    """Generate all plausible variants/abbreviations for a location name."""
    variants = [name.lower(), unidecode(name.lower())]

    words = name.split()
    first_lower = unidecode(words[0].lower().rstrip("."))
    rest = " ".join(words[1:]) if len(words) > 1 else ""

    if len(words) >= 1:
        for base, abbrevs in ABBREVIATION_MAP.items():
            if first_lower == unidecode(base):
                for abbr in abbrevs:
                    if rest:
                        variants.append(f"{abbr} {rest}".lower())
                        variants.append(unidecode(f"{abbr} {rest}".lower()))
                    else:
                        variants.append(abbr.lower())
                break

    if first_lower in REVERSE_ABBREV_MAP:
        full_words = REVERSE_ABBREV_MAP[first_lower]
        for fw in full_words:
            if rest:
                variants.append(f"{fw} {rest}".lower())
                variants.append(unidecode(f"{fw} {rest}".lower()))
            else:
                variants.append(fw.lower())
                variants.append(unidecode(fw.lower()))

    if len(words) > 1:
        acronym = "".join(w[0] for w in words).lower()
        if len(acronym) >= 2:
            variants.append(acronym)

    return list(set(v for v in variants if v))


def _match_building_letter_from_text(text_norm: str, locations: list[dict]) -> int | None:
    """Repère « bat C », « batiment c », etc. et associe à une entrée lieu du même type."""
    m = re.search(r"\b(?:bat|bat\.|batiment|batiments)\s+([a-z])\b", text_norm)
    if not m:
        return None
    letter = m.group(1).lower()
    for loc in locations:
        names_to_check = loc.get("names") or [loc["name"]]
        for name in names_to_check:
            if not name:
                continue
            n = unidecode(name.lower())
            n = re.sub(r"[^a-z0-9\s]", " ", n)
            n = re.sub(r"\s+", " ", n).strip()
            if re.search(rf"\b(?:bat|batiment)\s+{re.escape(letter)}\b", n):
                return loc["id"]
    return None


def detect_location(text: str, locations: list[dict]) -> int | None:
    """
    Detect if the text mentions a known location or placement.
    locations: [{"id": int, "name": str, "names": [str, ...]?}, ...]
    "names" includes main name + sub-placements (e.g. Batiment B, salle de dance).
    Returns location_id or None.
    """
    text_norm = unidecode(text.lower())
    text_norm = re.sub(r"[^a-z0-9\s]", " ", text_norm)
    text_norm = re.sub(r"\s+", " ", text_norm).strip()

    best_match = None
    best_len = 0

    for loc in locations:
        names_to_check = loc.get("names") or [loc["name"]]
        for name in names_to_check:
            if not name or not name.strip():
                continue
            variants = _generate_location_variants(name.strip())
            for v in variants:
                v_clean = re.sub(r"[^a-z0-9\s]", " ", v).strip()
                v_clean = re.sub(r"\s+", " ", v_clean)
                if len(v_clean) < 2:
                    continue
                pattern = r"\b" + re.escape(v_clean) + r"\b"
                if re.search(pattern, text_norm):
                    if len(v_clean) > best_len:
                        best_len = len(v_clean)
                        best_match = loc["id"]

    if best_match:
        return best_match
    return _match_building_letter_from_text(text_norm, locations)


def _match_location_by_name(llm_name: str, locations: list[dict]) -> int | None:
    """
    Match LLM-extracted location name to a known location.
    locations: [{"id": int, "name": str, "names": [str, ...]?}, ...]
    """
    if not llm_name or not locations:
        return None
    llm_norm = unidecode(llm_name.lower().strip())
    llm_norm = re.sub(r"[^a-z0-9\s]", " ", llm_norm)
    llm_norm = re.sub(r"\s+", " ", llm_norm).strip()
    if len(llm_norm) < 2:
        return None

    for loc in locations:
        names_to_check = loc.get("names") or [loc["name"]]
        for name in names_to_check:
            if not name:
                continue
            n_norm = unidecode(name.lower().strip())
            n_norm = re.sub(r"[^a-z0-9\s]", " ", n_norm)
            n_norm = re.sub(r"\s+", " ", n_norm)
            if llm_norm in n_norm or n_norm in llm_norm:
                return loc["id"]
            if llm_norm == n_norm:
                return loc["id"]
    return None


# --------------- Duplicate Detection (LLM-based) ---------------

class DuplicateDetector:
    def find_duplicate(self, new_text: str, existing: list[dict],
                       threshold: float = 0.22) -> dict | None:
        """Find duplicates using keyword overlap + LLM verification."""
        if not existing:
            return None

        new_keywords = set(extract_keywords(new_text, classify_category(new_text)))
        new_category = classify_category(new_text)

        best_match = None
        best_score = 0.0

        for s in existing:
            existing_kw = set(s.get("keywords", []))
            if new_keywords and existing_kw:
                overlap = len(new_keywords & existing_kw)
                union = len(new_keywords | existing_kw)
                kw_score = overlap / union if union else 0
                if s.get("category") == new_category and overlap >= 1:
                    kw_score += 0.15
                if kw_score >= 0.2:
                    if kw_score > best_score:
                        best_score = kw_score
                        best_match = s

        if best_match:
            return best_match

        new_norm = unidecode(new_text.lower().strip())
        for s in existing:
            existing_text = unidecode(
                f"{s.get('title', '')} {s.get('original_text', '')}".lower().strip()
            )
            new_words = set(new_norm.split())
            existing_words = set(existing_text.split())
            common = new_words & existing_words - FRENCH_STOPWORDS
            union = (new_words | existing_words) - FRENCH_STOPWORDS
            if union and len(common) / len(union) >= threshold:
                return s

        return None

    def verify_with_llm(self, existing_title: str, new_text: str) -> bool:
        """Extra verification with LLM to confirm duplicate."""
        result = llm_engine.verify_duplicate(existing_title, new_text)
        if result is not None:
            return result
        return False


class AIEngine:
    def __init__(self):
        self.duplicate_detector = DuplicateDetector()
        self._training_examples: list[dict] = []

    def reload_training_data(self):
        """Reload from database (called from app context)."""
        from models import CalibrationExample
        examples = CalibrationExample.query.filter_by(status="validated").all()
        self._training_examples = [e.to_training_dict() for e in examples]

        all_with_fw = CalibrationExample.query.filter(CalibrationExample.forbidden_words != "").all()
        forbidden = []
        for ex in all_with_fw:
            if ex.forbidden_words:
                forbidden.extend(ex.forbidden_words.split(","))
        from content_filter import load_dynamic_blocked_words
        load_dynamic_blocked_words(forbidden)

    def reload_context(self):
        """Reload school context for LLM prompts."""
        from models import SchoolContext
        ctx = SchoolContext.query.filter_by(key="school_info").first()
        if ctx and ctx.value:
            llm_engine.set_school_context(ctx.value)

    def _find_similar_examples(self, text: str, max_examples: int = 3) -> list[dict]:
        """Find the most relevant training examples for few-shot prompting."""
        if not self._training_examples:
            return []

        text_tokens = set(_tokenize(text))
        scored = []
        for ex in self._training_examples:
            ex_tokens = set(_tokenize(ex.get("message_original", "")))
            if not ex_tokens:
                continue
            overlap = len(text_tokens & ex_tokens)
            if overlap > 0:
                scored.append((overlap, ex))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [ex for _, ex in scored[:max_examples]]

    def process(self, text: str, locations: list[dict] | None = None, nfc_context: dict | None = None) -> dict:
        """Process a suggestion: reformulate, classify, extract keywords, detect location.
        Uses the all-in-one LLM call first, falls back to individual calls.
        nfc_context: optional NFC location metadata forwarded to the LLM prompt."""
        cleaned = _clean_text(text)

        # NFC-V2.2-AI: pass NFC context so the LLM prompt constrains category/location to the physical tag.
        llm_result = llm_engine.process_suggestion(cleaned, nfc_context=nfc_context)
        if llm_result and llm_result.get("title"):
            # IA de vérification : cohérence, syntaxe, français (avec exemples de calibration)
            calib_verify = []
            try:
                from models import CalibrationVerification
                calib_verify = [e.to_dict() for e in CalibrationVerification.query.order_by(CalibrationVerification.created_at.desc()).limit(15).all()]
            except Exception:
                pass
            llm_result = llm_engine.verify_and_refine(cleaned, llm_result, calibration_verify=calib_verify or None) or llm_result
            title = llm_result["title"]
            category = llm_result.get("category") or classify_category(cleaned)
            keywords = llm_result.get("keywords") or extract_keywords(cleaned, category)
        else:
            llm_result = None
            similar = self._find_similar_examples(cleaned)
            category = self._classify_with_training(cleaned, similar)
            keywords = extract_keywords(cleaned, category)
            title = self._reformulate_with_training(cleaned, similar)

        location_id = None
        if locations:
            location_id = detect_location(cleaned, locations)
            if not location_id and llm_result and llm_result.get("location_name"):
                location_id = _match_location_by_name(llm_result["location_name"], locations)

        tl = unidecode(cleaned.lower())
        if category == "Infrastructure" and any(
            x in tl for x in ("dessert", "desserts", "repas", "menu", "cantine", "self", "gouter")
        ):
            category = "Cantine"
        if not location_id and locations and re.search(r"\bself\b", tl):
            for loc in locations:
                for nm in loc.get("names") or [loc["name"]]:
                    if not nm:
                        continue
                    nn = unidecode(nm.lower().strip())
                    if nn in ("self", "self-service", "self service"):
                        location_id = loc["id"]
                        break
                if location_id:
                    break

        needs_debate = False
        proportion = 0.0
        feasibility = 0.5
        cost = 0.5
        if llm_engine.is_available():
            calib_debat = []
            try:
                from models import CalibrationDebat
                calib_debat = [e.to_dict() for e in CalibrationDebat.query.order_by(CalibrationDebat.created_at.desc()).limit(20).all()]
            except Exception:
                pass
            prop_result = llm_engine.analyze_proportion(title, calibration_debat=calib_debat or None)
            if prop_result:
                needs_debate = prop_result.get("needs_debate", False)
                proportion = prop_result.get("proportion", 0.0)
                feasibility = prop_result.get("feasibility", 0.5)
                cost = prop_result.get("cost", 0.5)

        return {
            "title": title,
            "category": category,
            "keywords": keywords,
            "location_id": location_id,
            "needs_debate": needs_debate,
            "ai_proportion": proportion,
            "ai_feasibility": feasibility,
            "ai_cost": cost,
        }

    def _classify_with_training(self, text: str, similar: list[dict]) -> str:
        if similar:
            cat_votes: dict[str, int] = {}
            for ex in similar:
                cat = ex.get("categorie", "")
                if cat:
                    cat_votes[cat] = cat_votes.get(cat, 0) + 1
            if cat_votes:
                best_cat = max(cat_votes, key=cat_votes.get)
                if cat_votes[best_cat] >= 2:
                    return best_cat

        return classify_category(text)

    def _reformulate_with_training(self, text: str, similar: list[dict]) -> str:
        if similar and llm_engine.is_available():
            examples_text = ""
            for ex in similar[:3]:
                orig = ex.get("message_original", "")
                title = ex.get("titre_reformule", "")
                if orig and title:
                    examples_text += f'Entrée: "{orig}"\nSortie: "{title}"\n\n'

            if examples_text:
                prompt = f"""Tu reformules des suggestions d'élèves de lycée. Conserve les éléments concrets (panneaux solaires, toits, micro-ondes, etc.). Ne généralise pas.

Voici des exemples validés :

{examples_text}Maintenant reformule cette suggestion de la même manière (garde les détails importants) :
Entrée: "{text}"
Sortie:"""
                result = llm_engine._call_ollama(prompt, temperature=0.3)
                if result:
                    result = result.strip().strip('"').strip("'").strip("«»").strip()
                    if result and 3 < len(result) < 200:
                        return result[0].upper() + result[1:]

        return reformulate_title(text)

    def _get_duplicate_candidates(self, text: str, existing: list[dict], top_n: int = 8) -> list[tuple[float, dict]]:
        """Return top N suggestions by similarity score for LLM verification."""
        if not existing:
            return []

        new_norm = unidecode(text.lower())
        new_words = set(w for w in new_norm.split() if len(w) > 2) - FRENCH_STOPWORDS

        scored: list[tuple[float, dict]] = []
        for s in existing:
            combined = f"{s.get('title', '')} {s.get('original_text', '')} {' '.join(s.get('keywords', []))}"
            existing_words = set(w for w in unidecode(combined.lower()).split() if len(w) > 2) - FRENCH_STOPWORDS

            if not new_words or not existing_words:
                continue

            common = new_words & existing_words
            union = new_words | existing_words
            score = len(common) / len(union) if union else 0

            if score > 0.08:
                scored.append((score, s))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_n]

    def check_duplicate(self, text: str, existing: list[dict]) -> dict | None:
        # 1. Candidats par find_duplicate (keywords + overlap texte)
        match = self.duplicate_detector.find_duplicate(text, existing)
        if match:
            confirmed = self.duplicate_detector.verify_with_llm(match.get("title", ""), text)
            if confirmed:
                return match

        # 2. Candidats par similarité large, vérification LLM
        candidates = self._get_duplicate_candidates(text, existing, top_n=8)
        for score, s in candidates:
            if score < 0.12:
                continue
            confirmed = self.duplicate_detector.verify_with_llm(
                f"{s.get('title', '')} {s.get('original_text', '')}".strip(), text
            )
            if confirmed:
                return s

        return None

    def quick_duplicate_check(self, text: str, existing: list[dict]) -> dict | None:
        """Fast duplicate check using keyword overlap (no external libs)."""
        if not existing:
            return None

        new_norm = unidecode(text.lower())
        new_words = set(w for w in new_norm.split() if len(w) > 2) - FRENCH_STOPWORDS

        best_match = None
        best_score = 0.0

        for s in existing:
            combined = f"{s.get('title', '')} {s.get('original_text', '')} {' '.join(s.get('keywords', []))}"
            existing_words = set(w for w in unidecode(combined.lower()).split() if len(w) > 2) - FRENCH_STOPWORDS

            if not new_words or not existing_words:
                continue

            common = new_words & existing_words
            union = new_words | existing_words
            score = len(common) / len(union) if union else 0

            if score > best_score:
                best_score = score
                best_match = s

        if best_score >= 0.22 and best_match:
            return best_match

        return None

    def llm_available(self) -> bool:
        return llm_engine.is_available()
