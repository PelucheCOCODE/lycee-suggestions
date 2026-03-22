"""Spotify Client Credentials : token cache + métadonnées track."""
import base64
import re
import time
from urllib.parse import urlparse

import requests
from flask import current_app

_spotify_token_cache = {"token": None, "expires_at": 0.0}


def clear_spotify_token_cache():
    _spotify_token_cache["token"] = None
    _spotify_token_cache["expires_at"] = 0.0


def get_resolved_spotify_credentials():
    """
    Ordre : site_settings (panneau admin), puis variables d'environnement.
    Retourne (client_id, client_secret).
    """
    cid, csec = "", ""
    try:
        from models import SiteSettings

        r1 = SiteSettings.query.get("spotify_client_id")
        r2 = SiteSettings.query.get("spotify_client_secret")
        if r1 and (r1.value or "").strip():
            cid = r1.value.strip()
        if r2 and (r2.value or "").strip():
            csec = r2.value.strip()
    except Exception:
        pass
    if not cid:
        cid = (current_app.config.get("SPOTIFY_CLIENT_ID") or "").strip()
    if not csec:
        csec = (current_app.config.get("SPOTIFY_CLIENT_SECRET") or "").strip()
    return cid, csec


def spotify_credentials_configured() -> bool:
    cid, csec = get_resolved_spotify_credentials()
    return bool(cid and csec)


def get_spotify_token():
    """Retourne un access token ou None si credentials absents."""
    client_id, client_secret = get_resolved_spotify_credentials()
    if not client_id or not client_secret:
        return None

    now = time.time()
    if _spotify_token_cache["token"] and now < _spotify_token_cache["expires_at"] - 60:
        return _spotify_token_cache["token"]

    client_id = client_id.strip()
    client_secret = client_secret.strip()
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {credentials}"},
        timeout=5,
    )
    r.raise_for_status()
    data = r.json()

    _spotify_token_cache["token"] = data["access_token"]
    _spotify_token_cache["expires_at"] = now + float(data.get("expires_in", 3600))
    return data["access_token"]


# Segments type : /us/, /fr/, /intl-fr/, /intl-de/ … avant track/ ou playlist/
_SPOTIFY_OPEN_LOCALE = r"(?:(?:[a-z]{2}|[a-z]+-[a-z]{2})/)?"


def extract_spotify_track_id(url: str) -> str:
    url = (url or "").strip()
    m = re.match(r"spotify:track:([A-Za-z0-9]+)", url)
    if m:
        return m.group(1)
    m = re.search(rf"open\.spotify\.com/{_SPOTIFY_OPEN_LOCALE}track/([A-Za-z0-9]+)", url, re.I)
    if m:
        return m.group(1)
    raise ValueError(f"Impossible d'extraire un track_id Spotify depuis : {url}")


def extract_spotify_playlist_id(url: str) -> str:
    url = (url or "").strip()
    m = re.match(r"spotify:playlist:([A-Za-z0-9]+)", url)
    if m:
        return m.group(1)
    m = re.search(rf"open\.spotify\.com/{_SPOTIFY_OPEN_LOCALE}playlist/([A-Za-z0-9]+)", url, re.I)
    if m:
        return m.group(1)
    raise ValueError(f"Impossible d'extraire un playlist_id Spotify depuis : {url}")


def fetch_spotify_playlist_metadata(playlist_id_or_url: str) -> dict:
    """
    Métadonnées playlist (nom, lien public). playlist_id ou URL open.spotify.com.
    """
    s = (playlist_id_or_url or "").strip()
    if "open.spotify.com" in s or s.startswith("spotify:"):
        pid = extract_spotify_playlist_id(s)
    else:
        pid = s
    try:
        token = get_spotify_token()
        if not token:
            raise RuntimeError("Spotify non configuré")
        r = requests.get(
            f"https://api.spotify.com/v1/playlists/{pid}",
            headers={"Authorization": f"Bearer {token}"},
            params={"fields": "name,external_urls,images"},
            timeout=5,
        )
        r.raise_for_status()
        data = r.json()
        ext = (data.get("external_urls") or {}).get("spotify") or f"https://open.spotify.com/playlist/{pid}"
        imgs = data.get("images") or []
        thumb = imgs[0]["url"] if imgs else None
        return {
            "playlist_id": pid,
            "name": data.get("name") or "Playlist",
            "thumbnail_url": thumb,
            "external_url": ext,
        }
    except Exception as e:
        current_app.logger.error("Spotify playlist API error for %s: %s", pid, e)
        return {
            "playlist_id": pid,
            "name": "Playlist",
            "thumbnail_url": None,
            "external_url": f"https://open.spotify.com/playlist/{pid}",
            "error": str(e),
        }


def _deezer_preview_from_isrc(isrc: str) -> str | None:
    """API Deezer publique : extrait MP3 ~30 s par code ISRC."""
    s = (isrc or "").strip().upper()
    if len(s) < 4:
        return None
    try:
        r = requests.get(f"https://api.deezer.com/track/isrc:{s}", timeout=5)
        r.raise_for_status()
        d = r.json()
        if d.get("error"):
            return None
        return d.get("preview") or None
    except Exception:
        return None


def _deezer_preview_from_search(artist: str, title: str) -> str | None:
    """Recherche Deezer (sans clé) pour trouver un extrait quand Spotify n’en fournit pas."""
    q = f"{artist} {title}".strip()
    if len(q) < 5:
        return None
    try:
        r = requests.get(
            "https://api.deezer.com/search",
            params={"q": q, "limit": 15},
            timeout=5,
        )
        r.raise_for_status()
        d = r.json()
        for item in d.get("data") or []:
            prev = item.get("preview")
            if prev:
                return prev
        return None
    except Exception:
        return None


def try_deezer_preview_for_track(artist: str, title: str, isrc: str | None = None) -> str | None:
    """Tente ISRC puis recherche titre + artiste (sans API Spotify)."""
    if isrc:
        p = _deezer_preview_from_isrc(isrc)
        if p:
            return p
    return _deezer_preview_from_search(artist, title)


def _preview_url_from_spotify_track_data(data: dict, *, deezer_preview_fallback: bool) -> str | None:
    """
    Champ preview_url de la réponse GET /v1/tracks (URL CDN Spotify, souvent p.scdn.co).
    Si absent et deezer_preview_fallback=True, complète via l’API publique Deezer (ISRC puis recherche).
    """
    preview = data.get("preview_url")
    if preview:
        return preview
    if not deezer_preview_fallback:
        return None
    artists = ", ".join(a["name"] for a in data.get("artists", []))
    title = data.get("name") or ""
    isrc = (data.get("external_ids") or {}).get("isrc")
    if isrc:
        p = _deezer_preview_from_isrc(isrc)
        if p:
            return p
    return _deezer_preview_from_search(artists, title)


def fetch_spotify_track_metadata(spotify_url: str, *, deezer_preview_fallback: bool = True) -> dict:
    """
    Retourne titre, artiste, album, pochette, preview_url.
    Par défaut, si Spotify ne fournit pas preview_url, complète via Deezer (comportement historique).
    Ne plante jamais (fallbacks en cas d'erreur API).
    Lève ValueError uniquement si l'URL est invalide.
    """
    track_id = extract_spotify_track_id(spotify_url)

    try:
        token = get_spotify_token()
        if not token:
            raise RuntimeError("Spotify non configuré")

        r = requests.get(
            f"https://api.spotify.com/v1/tracks/{track_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        r.raise_for_status()
        data = r.json()

        artists = ", ".join(a["name"] for a in data.get("artists", []))
        images = data.get("album", {}).get("images", [])
        thumbnail = images[0]["url"] if images else None
        preview_url = _preview_url_from_spotify_track_data(data, deezer_preview_fallback=deezer_preview_fallback)

        return {
            "spotify_track_id": track_id,
            "title": data.get("name", "Titre inconnu"),
            "artist": artists or "Artiste inconnu",
            "album": data.get("album", {}).get("name", "") or "",
            "thumbnail_url": thumbnail,
            "preview_url": preview_url,
            "spotify_url": f"https://open.spotify.com/track/{track_id}",
        }

    except Exception as e:
        current_app.logger.error("Spotify API error for %s: %s", track_id, e)
        return {
            "spotify_track_id": track_id,
            "title": "Titre indisponible",
            "artist": "Artiste inconnu",
            "album": "",
            "thumbnail_url": None,
            "preview_url": None,
            "spotify_url": f"https://open.spotify.com/track/{track_id}",
        }


def preview_upstream_headers_for_url(target_url: str) -> dict:
    """En-têtes pour télécharger un extrait MP3 (Spotify CDN ou Deezer) — aligné sur le proxy Flask."""
    pr = urlparse(target_url)
    netloc = (pr.netloc or "").lower()
    h = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    }
    if netloc.endswith(".dzcdn.net"):
        h["Referer"] = "https://www.deezer.com/"
        h["Sec-Fetch-Dest"] = "audio"
        h["Sec-Fetch-Mode"] = "no-cors"
        h["Sec-Fetch-Site"] = "cross-site"
    elif "scdn.co" in netloc or "spotifycdn.com" in netloc or netloc == "p.scdn.co":
        h["Referer"] = "https://open.spotify.com/"
    return h


def download_preview_audio_bytes(url: str) -> bytes | None:
    """
    Télécharge l’extrait MP3 (pour cache disque). Les URLs Deezer expirent après quelques minutes.
    Retourne None si échec.
    """
    u = (url or "").strip()
    if not u.startswith("https://"):
        return None
    try:
        r = requests.get(
            u,
            timeout=35,
            headers=preview_upstream_headers_for_url(u),
            proxies={"http": None, "https": None},
        )
        if not r.ok:
            return None
        data = r.content
        if len(data) < 512 or len(data) > 5_000_000:
            return None
        return data
    except Exception:
        return None
