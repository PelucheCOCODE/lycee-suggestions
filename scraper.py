"""
Scraper des actualités e-lyco (Lycée Aristide Briand).
Lance au démarrage et toutes les 2h.
"""
import re
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

ELYXO_URL = "https://aristide-briand.paysdelaloire.e-lyco.fr/actualites/"


def _fetch_html(url: str, timeout: int = 15) -> str | None:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; LyceeSuggestions/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _abs_url(path: str, base: str = ELYXO_URL) -> str:
    if not path or path.startswith("http"):
        return path or ""
    base = base.rsplit("/", 1)[0] + "/"
    if path.startswith("/"):
        return "https://aristide-briand.paysdelaloire.e-lyco.fr" + path
    return base + path


def scrape_elyco_news() -> list[dict]:
    """Scrape la page actualités e-lyco. Retourne liste de {title, url, image_url, excerpt, full_text}."""
    html = _fetch_html(ELYXO_URL)
    if not html:
        return []

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    soup = BeautifulSoup(html, "html.parser")
    articles = []
    items = soup.find_all("article", limit=20)
    if not items:
        items = soup.find_all(class_=re.compile(r"post|item|entry|actualite", re.I), limit=20)
    if not items:
        items = soup.select("[class*='article'], [class*='news']")[:20]

    for art in items:
        link = art.find("a", href=True)
        if not link:
            continue
        href = link.get("href", "")
        url = _abs_url(href)

        title_el = art.find(["h2", "h3", "h4"]) or link
        title = (title_el.get_text(strip=True) if title_el else "").strip()
        if not title or len(title) < 5:
            continue

        excerpt_el = art.find(class_=re.compile(r"excerpt|summary|content|texte", re.I))
        if not excerpt_el:
            for p in art.find_all("p"):
                t = p.get_text(strip=True)
                if len(t) > 30:
                    excerpt_el = p
                    break
        excerpt = excerpt_el.get_text(strip=True)[:500] if excerpt_el else ""

        img = art.find("img", src=True)
        image_url = _abs_url(img["src"]) if img else ""

        full_text = excerpt
        if url and url != ELYXO_URL:
            time.sleep(0.5)
            detail_html = _fetch_html(url)
            if detail_html:
                detail = BeautifulSoup(detail_html, "html.parser")
                content = detail.find(class_=re.compile(r"content|article|post", re.I))
                if content:
                    full_text = content.get_text(separator=" ", strip=True)[:2000]

        articles.append({
            "title": title[:300],
            "url": url,
            "image_url": image_url,
            "excerpt": excerpt[:500],
            "full_text": full_text[:2000] or excerpt[:500],
        })

    return articles
