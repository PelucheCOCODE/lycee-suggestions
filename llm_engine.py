"""
Integration with Ollama (gemma3:4b) for all LLM operations.
Falls back gracefully if Ollama is not running.
"""

import json
import os
import re
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma3:4b")

_school_context = ""

# --------------- Credit Tracking ---------------

_credits_max = 100
_credits_used = 0
_credits_reset_at = datetime.now(timezone.utc) + timedelta(hours=24)
_credits_period_hours = 24
_call_durations: list[float] = []


def configure_credits(max_credits: int, period_hours: int):
    global _credits_max, _credits_period_hours, _credits_reset_at
    _credits_max = max(1, max_credits)
    _credits_period_hours = max(1, period_hours)
    if datetime.now(timezone.utc) >= _credits_reset_at:
        _reset_credits()


def get_credits_info() -> dict:
    _check_reset()
    avg_duration = sum(_call_durations) / len(_call_durations) if _call_durations else 0
    calls_per_suggestion = 2.5
    return {
        "max": _credits_max,
        "used": _credits_used,
        "remaining": max(0, _credits_max - _credits_used),
        "period_hours": _credits_period_hours,
        "reset_at": _credits_reset_at.isoformat(),
        "avg_call_duration_ms": round(avg_duration * 1000),
        "est_time_per_suggestion_ms": round(avg_duration * calls_per_suggestion * 1000),
        "est_suggestions_remaining": max(0, int((_credits_max - _credits_used) / calls_per_suggestion)) if calls_per_suggestion > 0 else 0,
        "total_calls_tracked": len(_call_durations),
    }


def _check_reset():
    global _credits_used, _credits_reset_at
    now = datetime.now(timezone.utc)
    if now >= _credits_reset_at:
        _reset_credits()


def _reset_credits():
    global _credits_used, _credits_reset_at
    _credits_used = 0
    _credits_reset_at = datetime.now(timezone.utc) + timedelta(hours=_credits_period_hours)


def _consume_credit() -> bool:
    """Try to consume one credit. Returns False if limit reached."""
    global _credits_used
    _check_reset()
    if _credits_used >= _credits_max:
        return False
    _credits_used += 1
    return True


def _record_duration(duration: float):
    _call_durations.append(duration)
    if len(_call_durations) > 200:
        _call_durations.pop(0)


def set_school_context(context: str):
    global _school_context
    _school_context = context


# --------------- Prompts ---------------

RELEVANCE_PROMPT = """Tu es un filtre pour une boîte à idées de lycée. Tu dois décider si un message est une suggestion ACCEPTABLE.

ACCEPTER (OUI) si le message :
- Propose quelque chose en lien avec le lycée (installations, cantine, équipements, organisation, cours, activités, numérique, bien-être, horaires, etc.)
- Même si c'est formulé de manière familière, argotique ou avec des fautes, tant que l'IDÉE concerne le lycée
- Même si c'est vague, tant qu'on comprend que ça touche à la vie au lycée

REFUSER (NON) uniquement si :
- C'est un mot au hasard, une blague, du contenu absurde ou complètement hors-sujet (ex: "poule", "pizza hawaii", "je m'ennuie")
- Ça critique ou cible une personne (apparence, compétence, physique)
- C'est juste une opinion négative sans aucune proposition (ex: "c'est nul", "j'aime pas le lycée")
- C'est du spam ou du contenu inapproprié
{context}
Message : "{text}"

Réponds UNIQUEMENT par OUI ou NON."""

REFORMULATE_PROMPT = """Tu reformules des suggestions d'élèves de lycée. Phrase courte, directe, orale — pas un rapport administratif.
{context}
Règles :
- UNE phrase : verbe à l'infinitif + idée (Installer, Ajouter, Rajouter, Mettre, Réparer…)
- Corrige fautes et argot/SMS ; pas de sur-correction littéraire
- INTERDIT : « Proposer l'acquisition de », « Il serait souhaitable de », « Il conviendrait de », tournures de comité
- IMPORTANT : conserve les éléments concrets. N'invente pas de lieu. "panneaux solaires sur les toits" → "Installer des panneaux solaires sur les toits" (PAS "développer l'autonomie énergétique")
- Ne mentionne JAMAIS de nom de personne
- Maximum 18 mots
- Réponds UNIQUEMENT avec la phrase reformulée

Exemples :
Entrée: "installer des panneaux solaires sur les toits"
Sortie: "Installer des panneaux solaires sur les toits du lycée"

Entrée: "wsh faudrait des micro-ondes a la cantoche"
Sortie: "Installer des micro-ondes à la cantine"

Entrée: "les toilettes du bat C c'est degueu svp reparez"
Sortie: "Rénover et nettoyer les toilettes du bâtiment C"

Suggestion originale : "{text}"
Sortie:"""

CATEGORY_PROMPT = """Classe cette suggestion d'élève de lycée dans UNE seule catégorie parmi cette liste exacte :
Cantine, Infrastructure, Vie scolaire, Pédagogie, Numérique, Bien-être, Autre
{context}
Suggestion : "{text}"

Réponds UNIQUEMENT avec le nom exact de la catégorie, un seul mot ou groupe de mots, rien d'autre."""

KEYWORDS_PROMPT = """Extrais les mots-clés importants de cette suggestion d'élève de lycée.
Retourne entre 3 et 6 mots-clés, séparés par des virgules.
Ne retourne que des mots pertinents au sujet (pas de mots vides comme "le", "de", "il", etc.).
{context}
Suggestion : "{text}"

Mots-clés :"""

DUPLICATE_CHECK_PROMPT = """Compare ces deux suggestions d'élèves de lycée.
Est-ce qu'elles parlent du MÊME SUJET et demandent la même chose (ou presque) ?
Même sujet = même thème au lycée (ex: micro-ondes, toilettes, horaires, cantine, wifi, etc.)
Même formulation différente = OUI (ex: "des micro-ondes" et "micro ondes a la cantine" = doublon)
{context}
Suggestion existante : "{existing}"
Nouvelle suggestion : "{new}"

Réponds UNIQUEMENT par OUI (même sujet, doublon) ou NON (sujet différent)."""

RAPPORT_PRECISION_PROMPT = """Tu décides pour deux suggestions d'élèves de lycée :
1) RAPPORT : la nouvelle a-t-elle un lien avec l'existante (même sujet, même problème) ?
2) PRÉCISION : si rapport=oui, la nouvelle apporte-t-elle des DÉTAILS (salle, cause, contexte) ou juste la même idée reformulée ? Ex: "Dans la salle 10 du bat C, fenêtre qui s'ouvre avec le vent car loquet cassé" = PRÉCISION.
{calibration_examples}{context}
Suggestion existante : "{existing}"
Nouvelle suggestion : "{new}"

Réponds EXACTEMENT sur 2 lignes :
RAPPORT: OUI ou NON
PRECISION: OUI ou NON (si RAPPORT=NON, mets NON)"""

DETAIL_HINT_PROMPT = """Tu décides si une suggestion d'élève MANQUE de détails utiles ou si elle est DÉJÀ ASSEZ PRÉCISE.
{calibration_examples}
Suggestion d'élève : "{text}"

- VAGUE (pas de lieu, problème trop général) : écris UNE question courte (max 12 mots). Ex: "Quel bâtiment ?", "Quel impact concret ?"
- PRÉCISE : réponds "non". Une suggestion est PRÉCISE si elle a : lieu/bâtiment/salle, OU problème concret détaillé (cause, contexte). Ex: "Dans la salle 10 du bat C, fenêtre qui s'ouvre avec le vent car loquet cassé" = déjà précis (salle + cause + contexte).

Réponds UNIQUEMENT par la question courte ou "non"."""

SUBTITLE_PROMPT = """Tu rédiges le court texte affiché SOUS le titre sur la page élève (boîte à idées). Ce n'est pas un compte rendu administratif.

{context}
Titre affiché : "{title}"

Textes des élèves (messages ou précisions) :
{original_texts}

Règles STRICTES :
- Utilise UNIQUEMENT ce qui figure dans les textes ci-dessus (y compris [for]/[against] si présent)
- N'invente rien : pas de chiffres, pas de "plusieurs élèves ont signalé", pas de "consigné pour intervention", pas de ton rapport ou de comité
- Style : direct, simple, comme une note lycée — une ou deux phrases courtes, au plus trois si vraiment nécessaire
- Évite le jargon : pas de "au sein de", "ont été constatés", "échanges informels", "faciliter une éventuelle intervention"
- Longueur : environ 120 à 400 caractères (court et lisible sur mobile)
- Réponds UNIQUEMENT avec ce texte, sans titre ni guillemets autour"""

PROPORTION_PROMPT = """Tu évalues une proposition de lycée pour décider si elle mérite un débat (arguments pour ET contre) ou seulement des soutiens.

Évalue ces 3 critères de manière réaliste pour un lycée français :

1. IMPACT (0-1) : Importance pour la vie au lycée.
2. FAISABILITE (0-1) : Réalisme du projet (budget, personnel, réglementation).
3. COUT (0-1) : Coût relatif (argent, temps, moyens). 0=quasi nul, 1=très élevé.

DÉBAT : OUI OBLIGATOIREMENT pour :
- Horaires (décaler cours, commencer plus tard, récré plus longue, etc.) → TOUJOURS OUI
- Rénovation, travaux, bâtiment (rénover, construire, réparer, etc.) → TOUJOURS OUI
- Énergie, environnement, équipements importants (panneaux solaires, isolation, chauffage, etc.) → TOUJOURS OUI
- Projets d'équipement ou d'infrastructure significatifs (coût, impact, choix techniques) → OUI
- Changements d'organisation (emploi du temps, répartition cours, etc.) → TOUJOURS OUI
- Activités obligatoires, bien-être imposé, décision médicale (yoga obligatoire, sport imposé, etc.) → TOUJOURS OUI
- Tout sujet où on peut légitimement être pour OU contre (coût, priorité, faisabilité, impact)

DÉBAT : NON uniquement pour :
- Petites améliorations consensuelles et sans enjeu (ketchup, micro-ondes, peinture décorative, petits aménagements) → NON

{calibration_examples}
{context}
Proposition : "{text}"

Réponds EXACTEMENT dans ce format (4 lignes) :
IMPACT: <0-1>
FAISABILITE: <0-1>
COUT: <0-1>
DEBAT: OUI ou NON"""

ARGUMENT_PROMPT = """Tu es un modérateur pour une boîte à idées lycée. Tu évalues UN argument écrit par un élève.

CONTEXTE DU VOTE : l'élève a voté {side_fr} — son texte doit défendre clairement ce camp ({side_check}) par rapport à la proposition ci-dessous.

RÈGLES STRICTES :
1) Lis la PROPOSITION en entier (sujet réel du projet). Lis l'ARGUMENT en entier.
2) VALIDE uniquement si l'argument apporte une raison, un exemple ou un point concret {side_check} cette proposition (même court ou mal écrit).
3) REFUSE (INVALIDE) si : hors-sujet par rapport à la proposition, vide de sens, pur spam, insultes, incohérent avec le vote ({side_check}), ou copie quasi identique d'un argument déjà listé.
4) Ne confonds pas « peu convaincant » et « invalide » : un argument faible mais pertinent peut être VALIDE avec un résumé neutre.

{context}
PROPOSITION (sujet du débat) :
{proposal}

ARGUMENT DE L'ÉLÈVE :
{argument}
{existing_args_block}

FORMAT DE RÉPONSE (obligatoire) — une seule ligne de décision, sans saut de ligne avant le texte final :
VALIDE: <résumé en une phrase, max 30 mots, français correct, fidèle au sens>
ou
INVALIDE: <raison précise en une courte phrase : pourquoi ce n'est pas un argument valable pour ce camp>

Commence impérativement par VALIDE: ou INVALIDE:"""

PROCESS_PROMPT = """Tu traites une suggestion d'élève de lycée. Effectue les 4 tâches suivantes.
{context}
Suggestion originale : "{text}"

TÂCHE 1 - REFORMULATION : Une phrase courte, directe, comme un élève la dirait : verbe à l'infinitif + idée (ex. « Ajouter… », « Rajouter… », « Mettre… », « Réparer… »). Corrige l'argot / SMS (pr→pour, psk/pcq→parce que, chill→détente ou calme, etc.) sans sur-polir. Conserve les lieux/objets concrets déjà dans le texte ; n'invente rien.

INTERDIT — ton « rapport » ou administratif : « Proposer l'acquisition de », « Il serait souhaitable de », « Il conviendrait de », « procéder à », « mettre en œuvre » quand un verbe simple suffit.

BON : « Rajouter du ketchup à la cantine » — pas « Proposer l'acquisition de ketchup pour la cantine ».
BON : « Ajouter davantage de prises électriques » — pas « Il serait souhaitable d'ajouter davantage de prises électriques ».

Maximum 18 mots. Pas de nom de personne.

TÂCHE 2 - CATÉGORIE : Classe dans UNE catégorie parmi : Cantine, Infrastructure, Vie scolaire, Pédagogie, Numérique, Bien-être, Autre
Règles : repas, desserts, menus, boissons, self, cantine, goûter → **Cantine** (pas Infrastructure). Radiateurs, fenêtres, salles de classe, bâtiments → Infrastructure. Musique d'ambiance, événements, clubs (hors cours) → souvent **Vie scolaire** ou **Bien-être** selon le sens.

TÂCHE 3 - MOTS-CLÉS : Liste 3 à 6 mots-clés pertinents, séparés par des virgules.

TÂCHE 4 - LIEU : Si la suggestion mentionne un lieu précis (bâtiment A/B/C…, salle nommée, self, cantine, CDI, gymnase, etc.), écris le nom tel que compris (ex: "Bâtiment C", "Self"). Sinon écris "Aucun". N'invente pas un lieu absent du texte.

Réponds EXACTEMENT dans ce format (4 lignes) :
TITRE: <phrase reformulée>
CATÉGORIE: <catégorie>
MOTS-CLÉS: <mot1, mot2, mot3>
LIEU: <nom du lieu ou Aucun>"""

VERIFY_PROMPT = """Tu es une IA de VÉRIFICATION. Tu contrôles le résultat d'une autre IA qui a traité une suggestion d'élève.
{calibration_examples}
Suggestion originale de l'élève : "{original}"

Résultat actuel de l'IA principale :
- Titre : {title}
- Catégorie : {category}
- Mots-clés : {keywords}
- Lieu : {location}

{context}

VÉRIFIE :
1. Cohérence : le titre correspond à l'idée de l'élève ; pas d'invention ; les détails (bâtiment, self, radiateurs…) sont conservés.
2. Syntaxe et français : orthographe correcte ; argot/SMS remplacé par du français standard dans le TITRE.
3. Ton : titre court et direct (verbe à l'infinitif). Si le titre est trop formel (« Il serait souhaitable… », « Proposer l'acquisition de… »), simplifie-le vers une forme du type « Ajouter… », « Rajouter… », « Mettre… ».
4. Catégorie : desserts / self / cantine / repas → Cantine ; pas "Infrastructure" pour de la nourriture sans travaux.
5. Lieu : si l'élève cite un bâtiment (ex: bat C, bâtiment C), le LIEU doit le refléter ; "self" ou cantine si mentionné.

Si tout est correct, réponds avec le résultat tel quel.
Si des corrections sont nécessaires, corrige et réponds avec le format exact :

TITRE: <titre corrigé>
CATÉGORIE: <catégorie>
MOTS-CLÉS: <mot1, mot2, mot3>
LIEU: <lieu ou Aucun>

Réponds UNIQUEMENT avec les 4 lignes au format ci-dessus."""

SPLIT_PROMPT = """Tu analyses un message d'élève de lycée pour une boîte à idées.
{context}{hint}
RÈGLES :
1. UNE seule demande (même avec justifications « parce que », « car », détails) → PARTS: 1
2. DEUX demandes DISTINCTES (deux actions sur des sujets ou lieux différents) → PARTS: 2

COMMENT DÉTECTER 2 IDÉES :
- Connecteurs forts : « et aussi », « en plus », « de plus », « également », « mais aussi »
- Deux verbes d'action avec des CIBLES différentes (ex. toilettes/gymnase vs bancs/cour)
- Deux lieux ou objets principaux différents (toilettes ≠ bancs ; gymnase ≠ cour)

CE QUI N'EST PAS 2 IDÉES (NE PAS SPLITTER) :
- Justification seule : « améliorer la cantine parce que c'est mauvais »
- Même sujet : « des bancs et des tables dans la cour »
- Reformulation : « plus de récré et plus longues » (une seule demande)

EXEMPLES :
« les toilettes vers le gymnase sont sales et aussi faudrait des bancs dans la cour » → PARTS: 2
« la cantine c nul et aussi faudrait plus de micro ondes » → PARTS: 2
« améliorer la cantine parce que c'est mauvais » → PARTS: 1
« plus de bancs et de tables dans la cour » → PARTS: 1

FORMAT :
PARTS: 1
1: <reformulation courte, verbe à l'infinitif, ton direct — pas « il serait souhaitable d'… » ni « proposer l'acquisition de… »>

ou

PARTS: 2
1: <première demande seule, même style direct>
2: <deuxième demande seule, même style direct>

En cas de doute, PARTS: 1.

Message : "{text}"

Réponse :"""


# ═══════════════════════════════════════════════════════════════════
# SPLIT — heuristiques Python (aide au LLM + repli si LLM indisponible)
# ═══════════════════════════════════════════════════════════════════

_SLANG_MAP = {
    "vrmt": "vraiment", "c": "c'est", "pr": "pour", "psk": "parce que",
    "pcq": "parce que", "pk": "parce que", "bcp": "beaucoup",
    "jsp": "je ne sais pas", "nrv": "énervé", "dsl": "désolé",
    "stp": "s'il te plaît", "ouf": "bien", "cheum": "moche",
    "relou": "ennuyeux", "trkl": "tranquille",
}

_SPLIT_CONNECTORS_RE = [
    (r"\bet\s+aussi\b", "et aussi"),
    (r"\bet\s+en\s+plus\b", "et en plus"),
    (r"\bmais\s+aussi\b", "mais aussi"),
    (r"\ben\s+plus\s+de\s+[çc]a\b", "en plus de ça"),
    (r"\ben\s+plus\s+(?:il\s+)?faudr", "en plus faudrait"),
    (r"\baussi\s+(?:il\s+)?faudr", "aussi faudrait"),
    (r"\bet\s+(?:puis|ensuite)\b", "et puis/ensuite"),
    (r"\bde\s+plus\b", "de plus"),
    (r"\begalement\b", "également"),
    (r"\bsinon\s+(?:aussi\s+)?(?:il\s+)?faudr", "sinon faudrait"),
]

def _normalize_for_split(text: str) -> str:
    """Normalise l'argot oral pour que le LLM comprenne mieux la structure."""
    words = text.split()
    result = []
    for w in words:
        clean = re.sub(r"[.,!?;:]", "", w.lower())
        replacement = _SLANG_MAP.get(clean)
        if replacement:
            result.append(replacement)
        else:
            result.append(w)
    return " ".join(result)


def _heuristic_should_split(text: str) -> tuple[bool, str | None]:
    """Pré-LLM : connecteur fort avec matière des deux côtés, ou double cible forte."""
    from unidecode import unidecode

    norm = unidecode(text.lower().strip())
    if len(norm.split()) < 8:
        return False, None

    for pattern, label in _SPLIT_CONNECTORS_RE:
        m = re.search(pattern, norm)
        if m:
            before_words = [w for w in norm[: m.start()].split() if len(w) > 2]
            after_words = [w for w in norm[m.end() :].split() if len(w) > 2]
            if len(before_words) >= 3 and len(after_words) >= 3:
                return True, label

    return False, None


def _fallback_split_at_connector(text: str) -> list[str] | None:
    """Découpe sur connecteurs littéraux si le LLM échoue ou fusionne à tort."""
    tl = text.lower()
    phrases = [
        " et aussi ",
        " et en plus ",
        " mais aussi ",
        " en plus de ça ",
        " et également ",
        " également ",
        " de plus, ",
        " de plus ",
        " en plus ",
    ]
    for p in phrases:
        needle = p.strip()
        idx = tl.find(needle)
        if idx <= 0:
            continue
        if needle == "de plus":
            tail = tl[idx + len(needle) : idx + len(needle) + 8].lstrip()
            if tail.startswith("de "):
                continue
        before = text[:idx].strip().rstrip(",.; ")
        after = text[idx + len(needle) :].strip()
        bw = [w for w in before.split() if len(w) > 2]
        aw = [w for w in after.split() if len(w) > 2]
        if len(bw) >= 3 and len(aw) >= 3 and _two_distinct_suggestion_parts(before, after):
            return [before, after]
    return None


# --------------- Core API ---------------

def _call_ollama(prompt: str, temperature: float = 0.3, num_predict: int = 100, timeout: int = 30) -> str | None:
    """Call Ollama API with gemma3:4b. Returns response text or None if unavailable or credits exhausted."""
    if not _consume_credit():
        return None

    t0 = time.monotonic()
    try:
        body = json.dumps({
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": num_predict},
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            _record_duration(time.monotonic() - t0)
            return data.get("response", "").strip()

    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        _record_duration(time.monotonic() - t0)
        return None


# --------------- Individual Functions ---------------

def check_relevance_llm(text: str) -> bool | None:
    """
    Ask the LLM if a message is a relevant school suggestion.
    Returns True (relevant), False (irrelevant), or None (LLM unavailable).
    """
    ctx = f"\nContexte : {_school_context}\n" if _school_context else ""
    prompt = RELEVANCE_PROMPT.format(text=text, context=ctx)
    result = _call_ollama(prompt, temperature=0.05, num_predict=10, timeout=15)

    if result:
        answer = result.strip().upper().replace(".", "")
        if "OUI" in answer:
            return True
        if "NON" in answer:
            return False

    return None


def reformulate(text: str) -> str | None:
    """Reformulate a suggestion using the LLM. Returns None if unavailable."""
    ctx = f"\nContexte de l'établissement : {_school_context}\n" if _school_context else ""
    prompt = REFORMULATE_PROMPT.format(text=text, context=ctx)
    result = _call_ollama(prompt, temperature=0.2, num_predict=60, timeout=30)

    if result:
        result = result.strip().strip('"').strip("'").strip("\u00ab\u00bb").strip()
        lines = result.split("\n")
        result = lines[0].strip()
        if result.upper().startswith("SORTIE"):
            result = result.split(":", 1)[-1].strip().strip('"').strip()
        result = result.rstrip(".").strip()
        if result and 3 < len(result) < 200:
            return result[0].upper() + result[1:]

    return None


def classify_with_llm(text: str) -> str | None:
    """Classify a suggestion using the LLM. Returns category or None."""
    ctx = f"\nContexte : {_school_context}\n" if _school_context else ""
    prompt = CATEGORY_PROMPT.format(text=text, context=ctx)
    result = _call_ollama(prompt, temperature=0.1, num_predict=20, timeout=15)

    valid = {"Cantine", "Infrastructure", "Vie scolaire", "Pédagogie", "Numérique", "Bien-être", "Autre"}
    if result:
        for cat in valid:
            if cat.lower() in result.lower():
                return cat

    return None


def extract_keywords_llm(text: str) -> list[str] | None:
    """Extract keywords using the LLM. Returns list or None."""
    ctx = f"\nContexte : {_school_context}\n" if _school_context else ""
    prompt = KEYWORDS_PROMPT.format(text=text, context=ctx)
    result = _call_ollama(prompt, temperature=0.1, num_predict=60, timeout=15)

    if result:
        line = result.split("\n")[0].strip()
        keywords = [k.strip().lower() for k in line.split(",") if k.strip()]
        if keywords:
            return keywords[:8]

    return None


def verify_duplicate(existing_title: str, new_text: str) -> bool | None:
    """
    Ask the LLM if two suggestions are really the same.
    Returns True if same, False if different, None if LLM unavailable.
    """
    ctx = f"\nContexte : {_school_context}\n" if _school_context else ""
    prompt = DUPLICATE_CHECK_PROMPT.format(existing=existing_title, new=new_text, context=ctx)
    result = _call_ollama(prompt, temperature=0.1, num_predict=10, timeout=15)

    if result:
        answer = result.strip().upper().replace(".", "")
        if "OUI" in answer:
            return True
        if "NON" in answer:
            return False

    return None


def check_rapport_precision(
    existing_text: str, new_text: str, calibration: list[dict] | None = None
) -> tuple[bool, bool] | None:
    """
    Vérifie si la nouvelle suggestion a un rapport avec l'existante et si c'est une précision.
    Returns (has_rapport, is_precision) or None si IA indisponible.
    """
    calib_block = ""
    if calibration:
        lines = []
        for ex in calibration[:10]:
            e = (ex.get("existing_text") or "")[:60]
            n = (ex.get("new_text") or "")[:60]
            hr = "OUI" if ex.get("has_rapport") else "NON"
            ip = "OUI" if ex.get("is_precision") else "NON"
            lines.append(f'- "{e}" + "{n}" → RAPPORT:{hr} PRECISION:{ip}')
        if lines:
            calib_block = "Exemples (respecte ces décisions) :\n" + "\n".join(lines) + "\n\n"
    ctx = f"\nContexte : {_school_context}\n" if _school_context else ""
    prompt = RAPPORT_PRECISION_PROMPT.format(
        existing=existing_text[:200], new=new_text[:200],
        calibration_examples=calib_block, context=ctx
    )
    result = _call_ollama(prompt, temperature=0.1, num_predict=15, timeout=5)
    if not result:
        return None
    has_rapport = False
    is_precision = False
    for line in result.strip().upper().split("\n"):
        if "RAPPORT:" in line:
            has_rapport = "OUI" in line
        if "PRECISION:" in line or "PRÉCISION:" in line:
            is_precision = "OUI" in line
    return (has_rapport, is_precision)


def process_suggestion(text: str) -> dict | None:
    """
    All-in-one processing: reformulate + classify + keywords in a single LLM call.
    Returns dict with title, category, keywords or None if LLM unavailable.
    """
    ctx = f"\nContexte : {_school_context}\n" if _school_context else ""
    prompt = PROCESS_PROMPT.format(text=text, context=ctx)
    result = _call_ollama(prompt, temperature=0.2, num_predict=150, timeout=45)

    if not result:
        return None

    parsed = {"title": None, "category": None, "keywords": None, "location_name": None}

    for line in result.split("\n"):
        line = line.strip()
        upper = line.upper()
        if upper.startswith("TITRE:") or upper.startswith("TITRE :"):
            val = line.split(":", 1)[1].strip().strip('"').strip("'").strip("\u00ab\u00bb").strip().rstrip(".")
            if val and len(val) > 3:
                parsed["title"] = val[0].upper() + val[1:]
        elif upper.startswith("CATÉGORIE:") or upper.startswith("CATEGORIE:") or upper.startswith("CATÉGORIE :") or upper.startswith("CATEGORIE :"):
            val = line.split(":", 1)[1].strip()
            valid = {"Cantine", "Infrastructure", "Vie scolaire", "Pédagogie", "Numérique", "Bien-être", "Autre"}
            for cat in valid:
                if cat.lower() in val.lower():
                    parsed["category"] = cat
                    break
        elif upper.startswith("MOTS-CLÉS:") or upper.startswith("MOTS-CLES:") or upper.startswith("MOTS-CLÉS :") or upper.startswith("MOTS-CLES :"):
            val = line.split(":", 1)[1].strip()
            kws = [k.strip().lower() for k in val.split(",") if k.strip()]
            if kws:
                parsed["keywords"] = kws[:8]
        elif upper.startswith("LIEU:") or upper.startswith("LIEU :"):
            val = line.split(":", 1)[1].strip().strip('"').strip("'")
            if val and "aucun" not in val.lower():
                parsed["location_name"] = val.strip()

    if parsed["title"]:
        return parsed

    return None


def _two_distinct_suggestion_parts(a: str, b: str) -> bool:
    """Rejette deux lignes trop proches (même idée coupée en deux par erreur)."""
    la = re.findall(r"[a-zàâäéèêëïîôùûçœæ]+", a.lower())
    lb = re.findall(r"[a-zàâäéèêëïîôùûçœæ]+", b.lower())
    sw = {
        "le", "la", "les", "de", "des", "du", "un", "une", "et", "au", "aux", "pour", "plus", "moins",
        "lycée", "lycee", "dans",
    }
    wa = {w for w in la if len(w) > 2 and w not in sw}
    wb = {w for w in lb if len(w) > 2 and w not in sw}
    if len(wa) < 2 or len(wb) < 2:
        return False
    union = wa | wb
    if not union:
        return False
    j = len(wa & wb) / len(union)
    return j <= 0.58


def split_suggestion_parts(text: str) -> list[str] | None:
    """
    Si le message mélange deux suggestions distinctes, retourne 2 parties.
    Hybride : heuristique Python → LLM → validation Jaccard → repli sur découpe au connecteur.
    """
    if not text or len(text.strip()) < 12:
        return None

    normalized = _normalize_for_split(text)
    should_split, connector = _heuristic_should_split(normalized)

    ctx = f"\nContexte : {_school_context}\n" if _school_context else ""
    hint = ""
    if should_split:
        hint = (
            f"\nNote : signal fort (« {connector} ») : probablement DEUX demandes distinctes. "
            "Si deux actions/lieux/objets différents → PARTS: 2.\n"
        )

    prompt = SPLIT_PROMPT.format(text=normalized[:900], context=ctx, hint=hint)
    result = _call_ollama(prompt, temperature=0.08, num_predict=200, timeout=40)

    if not result:
        if should_split:
            return _fallback_split_at_connector(text)
        return None

    n_parts = None
    chunks: list[str] = []
    for line in result.split("\n"):
        line = line.strip()
        if not line:
            continue
        ul = line.upper()
        if ul.startswith("PARTS:"):
            try:
                n_parts = int(line.split(":", 1)[1].strip().split()[0])
            except (ValueError, IndexError):
                n_parts = None
            continue
        m = re.match(r"^[12]\s*:\s*(.+)$", line)
        if m:
            val = m.group(1).strip().strip('"').strip("'").strip()
            if val and len(val) >= 5:
                chunks.append(val)

    if n_parts == 2 and len(chunks) >= 2:
        p1, p2 = chunks[0], chunks[1]
        if not _two_distinct_suggestion_parts(p1, p2):
            return _fallback_split_at_connector(text) if should_split else None
        return [p1, p2]

    if should_split and n_parts != 2:
        return _fallback_split_at_connector(text)

    return None


def verify_and_refine(original_text: str, result: dict, calibration_verify: list[dict] | None = None) -> dict | None:
    """
    IA de vérification : contrôle cohérence, syntaxe, français du résultat de l'IA principale.
    A accès au contexte et à la suggestion d'origine.
    calibration_verify: exemples {original_text, main_result, verify_result, correction} pour few-shot.
    Retourne le résultat corrigé ou None si indisponible.
    """
    if not result or not result.get("title"):
        return result

    calib_block = ""
    if calibration_verify:
        lines = []
        for ex in calibration_verify[:10]:
            orig = (ex.get("original_text") or "")[:80]
            cor = ex.get("correction") or {}
            tit = cor.get("title", "")
            if orig and tit:
                lines.append(f'- "{orig}..." → Titre corrigé: "{tit}"')
        if lines:
            calib_block = "Exemples de corrections validées (respecte ce style) :\n" + "\n".join(lines) + "\n\n"

    ctx = f"\nContexte établissement : {_school_context}\n" if _school_context else ""
    kw = result.get("keywords")
    kw_str = ", ".join(kw) if isinstance(kw, list) else (kw or "")
    prompt = VERIFY_PROMPT.format(
        calibration_examples=calib_block,
        original=original_text[:500],
        title=result.get("title", ""),
        category=result.get("category", "Autre"),
        keywords=kw_str,
        location=result.get("location_name") or "Aucun",
        context=ctx,
    )
    resp = _call_ollama(prompt, temperature=0.1, num_predict=120, timeout=30)
    if not resp:
        return result

    parsed = {"title": result["title"], "category": result.get("category"), "keywords": result.get("keywords"), "location_name": result.get("location_name")}
    for line in resp.split("\n"):
        line = line.strip()
        upper = line.upper()
        if upper.startswith("TITRE:") or upper.startswith("TITRE :"):
            val = line.split(":", 1)[1].strip().strip('"').strip("'").strip()
            if val and len(val) > 3:
                parsed["title"] = val[0].upper() + val[1:]
        elif "CATÉGORIE" in upper or "CATEGORIE" in upper:
            val = line.split(":", 1)[1].strip()
            valid = {"Cantine", "Infrastructure", "Vie scolaire", "Pédagogie", "Numérique", "Bien-être", "Autre"}
            for cat in valid:
                if cat.lower() in val.lower():
                    parsed["category"] = cat
                    break
        elif "MOTS-CLÉS" in upper or "MOTS-CLES" in upper:
            val = line.split(":", 1)[1].strip()
            kws = [k.strip().lower() for k in val.split(",") if k.strip()]
            if kws:
                parsed["keywords"] = kws[:8]
        elif upper.startswith("LIEU:") or upper.startswith("LIEU :"):
            val = line.split(":", 1)[1].strip().strip('"').strip("'")
            if val and "aucun" not in val.lower():
                parsed["location_name"] = val

    return {**result, **parsed}


def analyze_proportion(text: str, calibration_debat: list[dict] | None = None) -> dict | None:
    """
    Analyze proposal: impact, feasibility, cost. Decide if it deserves debate.
    calibration_debat: optional list of {"proposition": str, "needs_debate": bool} for few-shot learning.
    Returns dict with proportion (impact), feasibility, cost, needs_debate; or None if LLM unavailable.
    """
    calibration_examples = ""
    if calibration_debat:
        lines = []
        for ex in calibration_debat[:15]:
            prop = ex.get("proposition", "").strip()
            nd = ex.get("needs_debate", False)
            if prop:
                lines.append(f'- "{prop}" → DEBAT:{"OUI" if nd else "NON"}')
        if lines:
            calibration_examples = "Exemples de calibration (respecte ces décisions) :\n" + "\n".join(lines) + "\n\n"
    ctx = f"\nContexte : {_school_context}\n" if _school_context else ""
    prompt = PROPORTION_PROMPT.format(text=text, context=ctx, calibration_examples=calibration_examples)
    result = _call_ollama(prompt, temperature=0.1, num_predict=80, timeout=25)

    if not result:
        return None

    import re
    parsed = {"proportion": 0.0, "feasibility": 0.5, "cost": 0.5, "needs_debate": False}

    for line in result.split("\n"):
        line = line.strip().upper()
        if line.startswith("IMPACT:") or line.startswith("IMPACT :"):
            m = re.search(r"0?\.\d+|1\.0?|1", line)
            if m:
                parsed["proportion"] = max(0.0, min(1.0, float(m.group())))
        elif line.startswith("FAISABILITE:") or line.startswith("FAISABILITE :"):
            m = re.search(r"0?\.\d+|1\.0?|1", line)
            if m:
                parsed["feasibility"] = max(0.0, min(1.0, float(m.group())))
        elif line.startswith("COUT:") or line.startswith("COUT :"):
            m = re.search(r"0?\.\d+|1\.0?|1", line)
            if m:
                parsed["cost"] = max(0.0, min(1.0, float(m.group())))
        elif line.startswith("DEBAT:") or line.startswith("DEBAT :"):
            parsed["needs_debate"] = "OUI" in line

    return parsed


def process_argument(proposal_text: str, argument_text: str, side: str, existing_arguments: list[str] | None = None) -> tuple[bool, str]:
    """
    Process an argument: anti-troll check + duplicate check + summarize.
    Returns (is_valid, summary_or_reason).
    """
    side_fr = "pour" if side == "for" else "contre"
    side_check = "POUR" if side == "for" else "CONTRE"
    ctx = f"\nContexte : {_school_context}\n" if _school_context else ""
    existing_args_block = ""
    if existing_arguments:
        existing_args_block = "\nArguments déjà présents (ne pas répéter) :\n" + "\n".join(f"- {a}" for a in existing_arguments[:10])
    prompt = ARGUMENT_PROMPT.format(
        proposal=proposal_text,
        argument=argument_text,
        side_fr=side_fr,
        side_check=side_check,
        context=ctx,
        existing_args_block=existing_args_block,
    )
    result = _call_ollama(prompt, temperature=0.15, num_predict=220, timeout=22)

    if not result:
        # Fallback : ne pas bloquer le parcours si Ollama est lent / hors ligne (filtre déjà passé en amont)
        cleaned = " ".join(argument_text.split())
        if len(cleaned) >= 5:
            summary = cleaned[:220] + ("…" if len(cleaned) > 220 else "")
            return True, summary
        return False, "Texte trop court."

    result = result.strip()
    # Prendre la première ligne utile (certains modèles ajoutent un préambule)
    lines = [ln.strip() for ln in result.split("\n") if ln.strip()]
    first = lines[0] if lines else result
    for ln in lines:
        ul = ln.upper().replace("É", "E")
        if ul.startswith("INVALIDE") or ul.startswith("VALIDE"):
            first = ln
            break

    raw = first.strip()
    ul = raw.upper().replace("É", "E")
    if ul.startswith("INVALIDE"):
        payload = raw[len("INVALIDE") :].lstrip(" :").strip()
        return False, (payload[:500] if payload else "Argument non pertinent")
    if ul.startswith("VALIDE"):
        payload = raw[len("VALIDE") :].lstrip(" :").strip().strip('"').strip("'")
        if payload:
            if len(payload) > 500:
                payload = payload[:497] + "…"
            return True, payload
    # Réponse LLM hors format : repli sur texte nettoyé (évite rejet brutal si le modèle bavarde ou tronque)
    cleaned = " ".join(argument_text.split())
    if len(cleaned) >= 8:
        summary = cleaned[:400] + ("…" if len(cleaned) > 400 else "")
        return True, summary
    return False, "Texte trop court ou illisible."


NEWS_SUMMARY_PROMPT = """Résume cet article d'actualité de lycée en 2 à 3 phrases courtes et claires. Ton informatif, pas de formules de politesse.
Titre : "{title}"
Contenu : "{text}"

Réponds UNIQUEMENT avec le résumé, rien d'autre."""


def summarize_news(title: str, text: str) -> str | None:
    """Résume un article d'actualité pour l'affichage."""
    if not text or len(text) < 20:
        return title
    text = text[:1500]
    prompt = NEWS_SUMMARY_PROMPT.format(title=title, text=text)
    result = _call_ollama(prompt, temperature=0.3, num_predict=120, timeout=25)
    if result:
        result = result.strip().strip('"').strip("'").strip()
        if result and len(result) < 400:
            return result[0].upper() + result[1:]
    return text[:300] + ("..." if len(text) > 300 else "")


def suggest_detail_hint(text: str, calibration_details: list[dict] | None = None) -> str | None:
    """Rapide : suggère une question pour enrichir une suggestion vague. Timeout court pour prioriser la rapidité."""
    if not text or len(text) < 10:
        return None
    calib_block = ""
    if calibration_details:
        lines = []
        for ex in calibration_details[:12]:
            s = (ex.get("suggestion_text") or "")[:80]
            h = ex.get("hint")
            lines.append(f'- "{s}" → {h if h else "non"}')
        if lines:
            calib_block = "Exemples de calibration (respecte ces décisions) :\n" + "\n".join(lines) + "\n\n"
    prompt = DETAIL_HINT_PROMPT.format(text=text[:200], calibration_examples=calib_block)
    result = _call_ollama(prompt, temperature=0.1, num_predict=25, timeout=2)
    if not result:
        return None
    result = result.strip().strip('"').strip("'").strip().rstrip(".")
    if not result or len(result) < 3:
        return None
    rl = result.lower()
    if rl == "non" or rl.startswith("non ") or "non." in rl:
        return None
    if len(result) > 80:
        return None
    return result[0].upper() + result[1:]


MODERATE_COMMUNITY_PROMPT = """Tu modères un court message public entre élèves. Tu ne réécris pas le message.
Réponds UNIQUEMENT par OUI ou NON.

OUI = le message est acceptable (ton léger, humeur, encouragement, sujets variés ; pas besoin d'avoir un rapport avec le lycée).
NON = menace, incitation à la violence, contenu sexuel explicite, harcèlement ciblé, drogue, autodestruction, contenu clairement illégal ou dangereux.

Message :
"{text}"

Réponds UNIQUEMENT par OUI ou NON."""


def moderate_community_message_llm(text: str) -> tuple[bool, str]:
    """(ok, raison). Si le modèle est indisponible, retourne (True, '') pour ne pas bloquer après filtre règles."""
    safe = (text or "").replace('"', "'")[:600]
    prompt = MODERATE_COMMUNITY_PROMPT.format(text=safe)
    result = _call_ollama(prompt, temperature=0.05, num_predict=8, timeout=12)
    if not result:
        return True, ""
    answer = result.strip().upper().replace(".", "")
    if answer.startswith("OUI"):
        return True, ""
    if answer.startswith("NON"):
        return False, "Ce message ne peut pas être publié (modération)."
    return True, ""


def generate_subtitle(title: str, original_texts: list[str]) -> str | None:
    """Résumé agrégé IA (sous-titre long) à partir du titre et de tous les textes élèves / précisions."""
    ctx = f"\nContexte de l'établissement : {_school_context}\n" if _school_context else ""
    texts = [t.strip() for t in (original_texts or []) if t and str(t).strip()]
    if not texts:
        return None
    # Cap pour le prompt (évite les dépassements de contexte sur des listes énormes)
    formatted = "\n".join(f'- "{t[:1200]}"' for t in texts[:80])
    prompt = SUBTITLE_PROMPT.format(title=(title or "")[:400], original_texts=formatted, context=ctx)
    result = _call_ollama(prompt, temperature=0.2, num_predict=380, timeout=90)

    if result:
        result = result.strip().strip('"').strip("'").strip("\u00ab\u00bb").strip()
        if result and 20 <= len(result) <= 8000:
            # Plafond affichage élève : évite les dérives longues même si le modèle déborde
            if len(result) > 650:
                result = result[:647].rsplit(" ", 1)[0] + "…"
            if len(result) == 1:
                return result.upper()
            return result[0].upper() + result[1:]

    return None


def is_available() -> bool:
    """Check if Ollama is reachable."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
