#!/usr/bin/env python3
"""
diagnostic_votes.py
-------------------
Vérifie le comportement réel de l'API vote de façon automatisée.
Lance depuis la racine du projet :  python diagnostic_votes.py

Modifie BASE_URL et SUGGESTION_ID selon ton environnement.
"""

import requests
import json
import time

# ─── CONFIG ───────────────────────────────────────────────────────
BASE_URL      = "http://127.0.0.1:5000/"  # ex. http://88.99.165.188/ en prod
# None = auto : première suggestion avec needs_debate=False (GET /api/suggestions)
SUGGESTION_ID = None
VERBOSE       = True
# ──────────────────────────────────────────────────────────────────

session = requests.Session()  # simule un vrai navigateur (cookie persistant)


def resolve_suggestion_id():
    """Retourne SUGGESTION_ID si fixé, sinon le premier id sans débat."""
    global SUGGESTION_ID
    if SUGGESTION_ID is not None:
        return SUGGESTION_ID
    r = session.get(f"{BASE_URL}/api/suggestions")
    r.raise_for_status()
    for s in r.json():
        if not s.get("needs_debate"):
            SUGGESTION_ID = s["id"]
            return SUGGESTION_ID
    return None

OK    = "\033[92m✓\033[0m"
FAIL  = "\033[91m✗\033[0m"
INFO  = "\033[94m→\033[0m"
WARN  = "\033[93m⚠\033[0m"

errors = []

def log(symbol, msg):
    print(f"  {symbol} {msg}")

def check(condition, label, detail=""):
    if condition:
        log(OK, label)
    else:
        log(FAIL, label)
        errors.append(f"{label} {detail}")

def get_suggestion_state():
    """Récupère l'état de la suggestion depuis GET /api/suggestions"""
    r = session.get(f"{BASE_URL}/api/suggestions")
    r.raise_for_status()
    data = r.json()
    suggestions = data if isinstance(data, list) else data.get("suggestions", [])
    for s in suggestions:
        if s["id"] == SUGGESTION_ID:
            return s
    return None

def vote(remove_vote: bool):
    """Envoie un POST vote"""
    payload = {"remove_vote": remove_vote, "vote_type": "for"}
    r = session.post(
        f"{BASE_URL}/api/suggestions/{SUGGESTION_ID}/vote",
        json=payload,
        headers={"Content-Type": "application/json"}
    )
    return r

def separator(title):
    line = "-" * 50
    print(f"\n{line}")
    print(f"  {title}")
    print(line)

# ═══════════════════════════════════════════════════════════════════
separator("TEST 0 — Vérification de base")
# ═══════════════════════════════════════════════════════════════════

if resolve_suggestion_id() is None:
    print("  Aucune suggestion simple (needs_debate=false). Impossible de tester le vote simple.")
    exit(1)

s = get_suggestion_state()
check(s is not None, f"Suggestion {SUGGESTION_ID} accessible via GET /api/suggestions")
if s is None:
    print(f"\n  {FAIL} Suggestion {SUGGESTION_ID} introuvable. Change SUGGESTION_ID dans le script.")
    exit(1)

check("has_voted" in s, "Champ has_voted présent dans la réponse GET")
check("vote_count" in s, "Champ vote_count présent dans la réponse GET")
check("server_ts" in s, "Champ server_ts présent dans la réponse GET")

initial_count = s["vote_count"]
log(INFO, f"État initial → has_voted={s.get('has_voted')} vote_count={initial_count}")

# ═══════════════════════════════════════════════════════════════════
separator("TEST 1 — Session cookie stable")
# ═══════════════════════════════════════════════════════════════════

# Vérifier que le cookie de session est posé
r_check = session.get(f"{BASE_URL}/api/suggestions")
check("session" in r_check.cookies or len(session.cookies) > 0,
      "Cookie de session posé par le serveur",
      "→ vérifier SESSION_PERMANENT=True et que get_session_id() est appelé")

if VERBOSE:
    log(INFO, f"Cookies présents : {dict(session.cookies)}")

# Vérifier que le cookie survit à un second appel
r_check2 = session.get(f"{BASE_URL}/api/suggestions")
cookies_stable = len(session.cookies) > 0
check(cookies_stable, "Cookie stable après second appel")

# ═══════════════════════════════════════════════════════════════════
separator("TEST 2 — Like (remove_vote: false)")
# ═══════════════════════════════════════════════════════════════════

# S'assurer qu'on part d'un état non liké
if s.get("has_voted"):
    log(WARN, "Déjà liké au départ — on retire d'abord")
    vote(remove_vote=True)
    time.sleep(0.2)

r = vote(remove_vote=False)
check(r.status_code == 200, f"POST /vote (like) → status 200 (reçu: {r.status_code})")

try:
    resp = r.json()
    check(resp.get("has_voted") is True, "Réponse has_voted=true après like", str(resp))
    check("vote_count" in resp, "Réponse contient vote_count")
    check("server_ts" in resp, "Réponse contient server_ts")

    count_after_like = resp.get("vote_count", 0)
    check(count_after_like == initial_count + 1,
          f"vote_count incrémenté de 1 ({initial_count} → {count_after_like})")
    if VERBOSE:
        log(INFO, f"Réponse complète : {json.dumps(resp)}")
except Exception as e:
    log(FAIL, f"Impossible de parser la réponse JSON : {e}")
    errors.append("Réponse POST /vote non parseable")

# ═══════════════════════════════════════════════════════════════════
separator("TEST 3 — Idempotence (double like)")
# ═══════════════════════════════════════════════════════════════════

r2 = vote(remove_vote=False)
try:
    resp2 = r2.json()
    count_after_double = resp2.get("vote_count", 0)
    check(count_after_double == initial_count + 1,
          f"Double like n'incrémente pas → vote_count reste {initial_count + 1}",
          f"(reçu: {count_after_double})")
    check(resp2.get("has_voted") is True, "has_voted reste true après double like")
except Exception as e:
    log(FAIL, f"Erreur double like : {e}")

# ═══════════════════════════════════════════════════════════════════
separator("TEST 4 — Unlike (remove_vote: true)")
# ═══════════════════════════════════════════════════════════════════

r3 = vote(remove_vote=True)
check(r3.status_code == 200, f"POST /vote (unlike) → status 200 (reçu: {r3.status_code})")

try:
    resp3 = r3.json()
    check(resp3.get("has_voted") is False, "Réponse has_voted=false après unlike", str(resp3))
    count_after_unlike = resp3.get("vote_count", 0)
    check(count_after_unlike == initial_count,
          f"vote_count revenu à {initial_count} après unlike (reçu: {count_after_unlike})")
    if VERBOSE:
        log(INFO, f"Réponse unlike : {json.dumps(resp3)}")
except Exception as e:
    log(FAIL, f"Impossible de parser la réponse unlike : {e}")

# ═══════════════════════════════════════════════════════════════════
separator("TEST 5 — Unlike idempotent (double unlike)")
# ═══════════════════════════════════════════════════════════════════

r4 = vote(remove_vote=True)
try:
    resp4 = r4.json()
    check(resp4.get("has_voted") is False, "has_voted=false après double unlike")
    check(resp4.get("vote_count", 0) == initial_count,
          f"vote_count stable à {initial_count} après double unlike")
except Exception as e:
    log(FAIL, f"Erreur double unlike : {e}")

# ═══════════════════════════════════════════════════════════════════
separator("TEST 6 — Persistance après nouvelle session (simulate refresh)")
# ═══════════════════════════════════════════════════════════════════

# Anti-rafale serveur (_vote_burst_exceeded) : fenêtre 15s, max 4 votes/suggestion
log(INFO, "Pause 16s pour laisser expirer la fenêtre anti-rafale (TEST 1-5)...")
time.sleep(16)

# Like avec la session courante
r6_like = vote(remove_vote=False)
check(r6_like.status_code == 200, f"POST like (TEST 6) → 200 (reçu: {r6_like.status_code})")
if r6_like.status_code == 200:
    try:
        j6 = r6_like.json()
        check(j6.get("has_voted") is True, "POST like (TEST 6) renvoie has_voted=true", str(j6))
    except Exception as e:
        log(FAIL, f"JSON POST like TEST 6: {e}")
time.sleep(0.35)

# Vérifier que le GET suivant renvoie has_voted=true avec la MÊME session
s_after = get_suggestion_state()
check(s_after is not None, "Suggestion récupérable après like")
if s_after:
    check(s_after.get("has_voted") is True,
          "GET /api/suggestions renvoie has_voted=true pour la session courante",
          "→ vérifier que has_voted est calculé avec session_id dans GET /api/suggestions")

# Simuler un "refresh" : nouvelle instance Session MAIS les cookies sont perdus
session_fresh = requests.Session()
s_fresh = None
try:
    r_fresh = session_fresh.get(f"{BASE_URL}/api/suggestions")
    data_fresh = r_fresh.json()
    suggestions_fresh = data_fresh if isinstance(data_fresh, list) else data_fresh.get("suggestions", [])
    for s_f in suggestions_fresh:
        if s_f["id"] == SUGGESTION_ID:
            s_fresh = s_f
            break
    if s_fresh:
        log(INFO, f"Nouvelle session (sans cookie) → has_voted={s_fresh.get('has_voted')}")
        log(INFO, "  (Normal que has_voted=false ici : nouvelle session = pas de vote)")
except Exception as e:
    log(WARN, f"Impossible de tester la nouvelle session : {e}")

# Nettoyer : retirer le like
vote(remove_vote=True)

# ═══════════════════════════════════════════════════════════════════
separator("TEST 7 — Cycle complet Like → Unlike → Like → Unlike")
# ═══════════════════════════════════════════════════════════════════

log(INFO, "Pause 16s avant le cycle (anti-rafale)...")
time.sleep(16)

states = []
for action, expected_voted, expected_delta in [
    (False, True,  +1),
    (True,  False,  0),
    (False, True,  +1),
    (True,  False,  0),
]:
    r = vote(remove_vote=action)
    if r.status_code != 200:
        log(FAIL, f"Vote cycle HTTP {r.status_code} — possible 429 anti-rafale : {r.text[:120]}")
    try:
        resp = r.json()
        states.append({
            "action": "unlike" if action else "like",
            "has_voted": resp.get("has_voted"),
            "vote_count": resp.get("vote_count"),
            "http": r.status_code,
        })
    except Exception:
        pass
    time.sleep(0.55)

if len(states) == 4:
    check(states[0]["has_voted"] is True,  "Cycle : Like → has_voted=true")
    check(states[1]["has_voted"] is False, "Cycle : Unlike → has_voted=false")
    check(states[2]["has_voted"] is True,  "Cycle : Like again → has_voted=true")
    check(states[3]["has_voted"] is False, "Cycle : Unlike again → has_voted=false")
    check(states[3]["vote_count"] == states[0]["vote_count"] - 1 or
          states[3]["vote_count"] == initial_count,
          f"Cycle : vote_count revenu à {initial_count} après 2 likes + 2 unlikes")
    if VERBOSE:
        for st in states:
            log(INFO, f"  {st['action']:6} → has_voted={st['has_voted']} vote_count={st['vote_count']}")

# ═══════════════════════════════════════════════════════════════════
separator("RÉSULTAT FINAL")
# ═══════════════════════════════════════════════════════════════════

if not errors:
    print(f"\n  {OK} Tous les tests sont passés. Le backend fonctionne correctement.")
    print(f"  {INFO} Si le bug persiste sur le site, il est dans student.js (frontend uniquement).")
else:
    print(f"\n  {FAIL} {len(errors)} erreur(s) détectée(s) :\n")
    for e in errors:
        print(f"    • {e}")
    print()
