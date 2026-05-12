from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import shutil
import sys
import textwrap
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from lxml import html as lxml_html


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "liberoblog_posts_000.csv"
HTML_PATH = ROOT / "liberoblog_000.html"

IMG_DIR = ROOT / "img"
SRC_DIR = ROOT / "src"
ASSETS_DIR = ROOT / "assets"
DATA_DIR = ROOT / "data"

SITE_TITLE = "CHIMICA sperimentale"
SITE_SUBTITLE = "Esperienze in home-lab: considerazioni di chimica sperimentale e altro"
AUTHOR = "paoloalbert"
ORIGINAL_BLOG = "https://blog.libero.it/paoloalbert/"


def _build_mojibake_map() -> dict[str, str]:
    chars = [chr(codepoint) for codepoint in range(0x00A0, 0x0100)]
    chars.extend(
        [
            "€",
            "‚",
            "ƒ",
            "„",
            "…",
            "†",
            "‡",
            "ˆ",
            "‰",
            "Š",
            "‹",
            "Œ",
            "Ž",
            "‘",
            "’",
            "“",
            "”",
            "•",
            "–",
            "—",
            "˜",
            "™",
            "š",
            "›",
            "œ",
            "ž",
            "Ÿ",
        ]
    )
    mapping: dict[str, str] = {}
    for char in chars:
        try:
            mapping[char.encode("utf-8").decode("latin-1")] = char
        except UnicodeEncodeError:
            continue
    return dict(sorted(mapping.items(), key=lambda item: len(item[0]), reverse=True))


MOJIBAKE_MAP = _build_mojibake_map()
LEGACY_MOJIBAKE_MAP = {
    "Â\x91": "'",
    "Â\x92": "'",
    "Â\x93": '"',
    "Â\x94": '"',
    "Â\x96": "-",
    "Â\x97": "-",
    "\x91": "'",
    "\x92": "'",
    "\x93": '"',
    "\x94": '"',
    "\x96": "-",
    "\x97": "-",
}


@dataclass
class Comment:
    comment_id: str
    parent_id: str
    level: int
    author: str
    text: str
    children: list["Comment"] = field(default_factory=list)


@dataclass
class Post:
    post_id: str
    number: int
    date: datetime
    title: str
    body_html: str = ""
    comments: list[Comment] = field(default_factory=list)
    first_image: str | None = None
    filename: str = ""
    slug: str = ""
    excerpt: str = ""


def read_latin1_with_optional_bom(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("latin-1")


def repair_csv_text(value: str) -> str:
    """Repair mixed CSV fields where UTF-8 bytes were decoded as Latin-1."""
    if not value:
        return ""
    repaired = value
    for bad, good in LEGACY_MOJIBAKE_MAP.items():
        repaired = repaired.replace(bad, good)
    for bad, good in MOJIBAKE_MAP.items():
        repaired = repaired.replace(bad, good)
    repaired = html.unescape(repaired)
    repaired = repaired.replace("Â", "")
    return repaired.replace("\xa0", " ").strip()


def slugify(value: str, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = ascii_text.lower()
    ascii_text = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    ascii_text = re.sub(r"-{2,}", "-", ascii_text)
    return ascii_text[:72].strip("-") or fallback


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def short_excerpt(value: str, limit: int = 190) -> str:
    text = clean_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "..."


def read_csv_posts() -> dict[str, Post]:
    text = read_latin1_with_optional_bom(CSV_PATH)
    rows = list(csv.DictReader(text.splitlines()))
    posts: dict[str, Post] = {}
    comments_by_post: dict[str, list[Comment]] = {}

    for row in rows:
        post_id = row["ID POST"]
        if post_id not in posts:
            number = int(row["NUMERO"])
            date = datetime.strptime(row["DATA"], "%Y-%m-%d %H:%M:%S")
            title = repair_csv_text(row["TITOLO"])
            slug = slugify(title, f"post-{number:03d}")
            posts[post_id] = Post(
                post_id=post_id,
                number=number,
                date=date,
                title=title,
                slug=slug,
                filename=f"{number:03d}-{slug}.html",
            )

        if row.get("ID COMMENTO"):
            comments_by_post.setdefault(post_id, []).append(
                Comment(
                    comment_id=row["ID COMMENTO"],
                    parent_id=row.get("ID POST PADRE", "0") or "0",
                    level=int(row.get("LIVELLO COMMENTO", "0") or 0),
                    author=repair_csv_text(row.get("AUTORE COMMENTO", "")) or "anonimo",
                    text=repair_csv_text(row.get("COMMENTO", "")),
                )
            )

    for post_id, comments in comments_by_post.items():
        by_id = {comment.comment_id: comment for comment in comments}
        roots: list[Comment] = []
        for comment in comments:
            parent = by_id.get(comment.parent_id)
            if parent is not None and parent is not comment:
                parent.children.append(comment)
            else:
                roots.append(comment)
        posts[post_id].comments = roots

    return posts


def element_inner_html(element) -> str:
    parts: list[str] = []
    if element.text:
        parts.append(html.escape(element.text))
    for child in element:
        parts.append(lxml_html.tostring(child, encoding="unicode", method="html"))
    return "".join(parts)


def read_exported_article_html() -> dict[str, str]:
    document = lxml_html.fromstring(read_latin1_with_optional_bom(HTML_PATH))
    result: dict[str, str] = {}
    for card in document.xpath("//div[contains(concat(' ', normalize-space(@class), ' '), ' post-card ')]"):
        raw_id = card.get("id", "")
        if not raw_id.startswith("post_"):
            continue
        content = card.xpath(".//div[contains(concat(' ', normalize-space(@class), ' '), ' content ')]")
        if not content:
            continue
        post_id = raw_id.replace("post_", "", 1)
        # The Libero export stores real image tags as escaped text. One unescape
        # pass turns them back into HTML while preserving the surrounding markup.
        result[post_id] = html.unescape(element_inner_html(content[0]))
    return result


def image_urls_from_html(value: str) -> list[str]:
    urls = re.findall(
        r"<img\b[^>]*?\bsrc\s*=\s*([\"'])(.*?)\1",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return [url for _, url in urls if url.startswith(("http://", "https://"))]


def safe_image_filename(url: str) -> str:
    parsed = urlparse(url)
    base = unquote(Path(parsed.path).name) or "image"
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip(".-") or "image"
    if not re.search(r"\.(jpe?g|png|gif|webp)$", base, re.IGNORECASE):
        base += ".jpg"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return f"{digest}-{base}"


def download_one(url: str, target: Path) -> tuple[str, bool, str]:
    if target.exists() and target.stat().st_size > 0:
        return url, True, "cached"

    req = Request(url, headers={"User-Agent": "PaoloAlbertBlogArchive/1.0"})
    try:
        with urlopen(req, timeout=30) as response:
            data = response.read()
        if not data:
            return url, False, "empty response"
        target.write_bytes(data)
        return url, True, f"{len(data)} bytes"
    except Exception as exc:  # noqa: BLE001
        return url, False, str(exc)


def download_images(urls: Iterable[str]) -> dict[str, str]:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    unique_urls = sorted(set(urls))
    mapping = {url: f"img/{safe_image_filename(url)}" for url in unique_urls}

    failures: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {
            pool.submit(download_one, url, ROOT / mapping[url]): url for url in unique_urls
        }
        done = 0
        total = len(futures)
        for future in as_completed(futures):
            url, ok, message = future.result()
            done += 1
            if not ok:
                failures.append((url, message))
            if done % 100 == 0 or done == total:
                print(f"Downloaded/checked {done}/{total} images")

    if failures:
        print("Image download failures:", file=sys.stderr)
        for url, message in failures[:20]:
            print(f"- {url}: {message}", file=sys.stderr)
        raise SystemExit(1)

    return mapping


def rewrite_image_sources(value: str, image_map: dict[str, str], prefix: str) -> str:
    def replace(match: re.Match[str]) -> str:
        quote = match.group(1)
        src = match.group(2)
        local = image_map.get(src)
        if not local:
            return match.group(0)
        new_src = prefix + local
        return match.group(0).replace(f"{quote}{src}{quote}", f'{quote}{new_src}{quote}')

    value = re.sub(
        r"<img\b[^>]*?\bsrc\s*=\s*([\"'])(.*?)\1[^>]*>",
        replace,
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )

    def add_lazy(match: re.Match[str]) -> str:
        tag = match.group(0)
        def inject_attr(current: str, attr: str) -> str:
            if f" {attr}=" in current.lower():
                return current
            if current.endswith("/>"):
                return current[:-2].rstrip() + f' {attr} />'
            return current[:-1] + f' {attr}>'

        if " loading=" not in tag.lower():
            tag = inject_attr(tag, 'loading="lazy"')
        if " decoding=" not in tag.lower():
            tag = inject_attr(tag, 'decoding="async"')
        return tag

    return re.sub(r"<img\b[^>]*>", add_lazy, value, flags=re.IGNORECASE | re.DOTALL)


def rewrite_internal_links(value: str, posts: dict[str, Post], prefix: str) -> str:
    id_to_file = {post.post_id: post.filename for post in posts.values()}
    value = re.sub(
        r"(?<!h)ttps://blog\.libero\.it/paoloalbert/",
        "https://blog.libero.it/paoloalbert/",
        value,
        flags=re.IGNORECASE,
    )

    def replace(match: re.Match[str]) -> str:
        post_id = match.group(1)
        filename = id_to_file.get(post_id)
        if not filename:
            return match.group(0)
        return prefix + "src/" + filename if prefix else filename

    value = re.sub(
        r"https?://blog\.libero\.it/paoloalbert/(\d+)\.html",
        replace,
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"https?://blog\.libero\.it/paoloalbert/?",
        prefix + "index.html",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"([\"'])index\.html((?:commenti|view)\.php[^\"']*)",
        rf"\1{ORIGINAL_BLOG}index.html\2",
        value,
        flags=re.IGNORECASE,
    )
    return value


def render_comments(comments: list[Comment], depth: int = 0) -> str:
    if not comments:
        return ""

    parts = ['<ol class="comments-list">']
    for comment in comments:
        author = html.escape(comment.author)
        body = html.escape(comment.text)
        body = body.replace("\n", "<br>")
        role = " author-comment" if comment.author.lower() == AUTHOR else ""
        parts.append(
            f'<li class="comment depth-{min(depth, 6)}{role}">'
            f'<div class="comment-meta"><span>{author}</span></div>'
            f'<div class="comment-body">{body}</div>'
        )
        if comment.children:
            parts.append(render_comments(comment.children, depth + 1))
        parts.append("</li>")
    parts.append("</ol>")
    return "".join(parts)


def page_shell(
    *,
    title: str,
    description: str,
    body: str,
    css_href: str,
    js_href: str | None = None,
    body_class: str = "",
) -> str:
    script = f'<script src="{js_href}" defer></script>' if js_href else ""
    return textwrap.dedent(
        f"""\
        <!doctype html>
        <html lang="it">
        <head>
          <meta charset="utf-8">
          <meta name="viewport" content="width=device-width, initial-scale=1">
          <title>{html.escape(title)}</title>
          <meta name="description" content="{html.escape(description)}">
          <link rel="stylesheet" href="{css_href}">
          {script}
        </head>
        <body class="{body_class}">
        {body}
        </body>
        </html>
        """
    )


def site_header(prefix: str, active: str = "") -> str:
    home = prefix + "index.html"
    archive = prefix + "archive.html"
    original = ORIGINAL_BLOG
    archive_current = ' aria-current="page"' if active == "archive" else ""
    home_current = ' aria-current="page"' if active == "home" else ""
    return textwrap.dedent(
        f"""\
        <header class="site-header">
          <a class="brand" href="{home}"{home_current}>
            <span class="brand-mark">PA</span>
            <span>
              <strong>{SITE_TITLE}</strong>
              <small>{SITE_SUBTITLE}</small>
            </span>
          </a>
          <nav class="site-nav" aria-label="Navigazione principale">
            <a href="{home}"{home_current}>Home</a>
            <a href="{archive}"{archive_current}>Archivio</a>
            <a href="{original}">Originale Libero</a>
          </nav>
        </header>
        """
    )


def site_footer(prefix: str) -> str:
    return textwrap.dedent(
        f"""\
        <footer class="site-footer">
          <p>Archivio statico di <strong>{SITE_TITLE}</strong>, migrato da Libero Blog.</p>
          <p><a href="{prefix}index.html">Home</a> · <a href="{prefix}archive.html">Archivio</a></p>
        </footer>
        """
    )


def render_article(post: Post, posts: dict[str, Post], ordered: list[Post]) -> str:
    idx = ordered.index(post)
    prev_post = ordered[idx - 1] if idx > 0 else None
    next_post = ordered[idx + 1] if idx + 1 < len(ordered) else None

    def nav_link(label: str, target: Post | None, direction: str) -> str:
        if not target:
            return f'<span class="article-nav-link muted">{label}</span>'
        return (
            f'<a class="article-nav-link {direction}" href="{target.filename}">'
            f"<small>{label}</small><strong>{html.escape(target.title)}</strong></a>"
        )

    comment_count = count_comments(post.comments)
    comments_html = (
        f"""
        <section class="comments" id="commenti">
          <div class="section-kicker">Commenti importati</div>
          <h2>{comment_count} commenti</h2>
          {render_comments(post.comments)}
        </section>
        """
        if comment_count
        else ""
    )

    original_url = f"{ORIGINAL_BLOG}{post.post_id}.html"
    article_body = textwrap.dedent(
        f"""\
        {site_header("../")}
        <main class="article-shell">
          <article class="article">
            <header class="article-head">
              <a class="back-link" href="../archive.html">Archivio</a>
              <p class="article-meta">Post n. {post.number} · {post.date.strftime("%d/%m/%Y %H:%M")} · <a href="{original_url}">link originale</a></p>
              <h1>{html.escape(post.title)}</h1>
            </header>
            <div class="post-body">
              {post.body_html}
            </div>
          </article>
          {comments_html}
          <nav class="article-nav" aria-label="Articoli precedente e successivo">
            {nav_link("Precedente", prev_post, "prev")}
            {nav_link("Successivo", next_post, "next")}
          </nav>
        </main>
        {site_footer("../")}
        """
    )
    return page_shell(
        title=f"{post.title} · {SITE_TITLE}",
        description=post.excerpt,
        body=article_body,
        css_href="../assets/style.css",
        body_class="article-page",
    )


def count_comments(comments: list[Comment]) -> int:
    return sum(1 + count_comments(comment.children) for comment in comments)


def render_post_card(post: Post, href: str, image_prefix: str, compact: bool = False) -> str:
    image = (
        f'<img src="{image_prefix}{post.first_image}" alt="" loading="lazy" decoding="async">'
        if post.first_image
        else f'<div class="thumb-placeholder"><span>{post.number}</span></div>'
    )
    comments = count_comments(post.comments)
    class_name = "post-card compact" if compact else "post-card"
    search_text = clean_text(f"{post.title} {post.excerpt} {post.number}").lower()
    return textwrap.dedent(
        f"""\
        <article class="{class_name}" data-search="{html.escape(search_text)}">
          <a class="thumb" href="{href}" aria-hidden="true" tabindex="-1">{image}</a>
          <div class="post-card-body">
            <p class="post-card-meta">n. {post.number} · {post.date.strftime("%d/%m/%Y")}{' · ' + str(comments) + ' commenti' if comments else ''}</p>
            <h2><a href="{href}">{html.escape(post.title)}</a></h2>
            <p>{html.escape(post.excerpt)}</p>
          </div>
        </article>
        """
    )


def render_home(posts: list[Post]) -> str:
    latest = posts[:6]
    total_comments = sum(count_comments(post.comments) for post in posts)
    latest_cards = "\n".join(
        render_post_card(post, f"src/{post.filename}", "", compact=True) for post in latest
    )
    body = textwrap.dedent(
        f"""\
        {site_header("", "home")}
        <main>
          <section class="hero">
            <div class="hero-copy">
              <p class="section-kicker">Archivio 2009-2020</p>
              <h1>{SITE_TITLE}</h1>
              <p>{SITE_SUBTITLE}</p>
              <div class="hero-actions">
                <a class="button primary" href="archive.html">Sfoglia l'archivio</a>
                <a class="button" href="{ORIGINAL_BLOG}">Blog originale</a>
              </div>
            </div>
          </section>

          <section class="stats-band" aria-label="Statistiche archivio">
            <div><strong>{len(posts)}</strong><span>articoli</span></div>
            <div><strong>{total_comments}</strong><span>commenti</span></div>
            <div><strong>840</strong><span>immagini salvate</span></div>
            <div><strong>2009-2020</strong><span>periodo</span></div>
          </section>

          <section class="home-search" aria-label="Ricerca nel blog">
            <div class="section-heading">
              <div>
                <p class="section-kicker">Cerca nel blog</p>
                <h2>Trova un articolo</h2>
              </div>
              <a href="archive.html">Archivio completo</a>
            </div>
            <form class="search-box home-search-box" action="archive.html" role="search">
              <span>Cerca</span>
              <div class="search-row">
                <input type="search" id="home-search" name="q" placeholder="ramatura, selenio, Biringuccio..." autocomplete="off">
                <button class="button primary" type="submit">Cerca</button>
              </div>
            </form>
            <p class="search-status" id="home-search-status" aria-live="polite" hidden></p>
            <div class="post-grid search-results-grid" id="home-search-results" hidden></div>
          </section>

          <section class="content-section">
            <div class="section-heading">
              <p class="section-kicker">Ultimi articoli</p>
              <h2>Le pubblicazioni più recenti</h2>
              <a href="archive.html">Tutti gli articoli</a>
            </div>
            <div class="post-grid compact-grid">
              {latest_cards}
            </div>
          </section>
        </main>
        {site_footer("")}
        """
    )
    return page_shell(
        title=SITE_TITLE,
        description=SITE_SUBTITLE,
        body=body,
        css_href="assets/style.css",
        js_href="assets/site.js",
        body_class="home-page",
    )


def render_archive(posts: list[Post]) -> str:
    cards = "\n".join(render_post_card(post, f"src/{post.filename}", "") for post in posts)
    body = textwrap.dedent(
        f"""\
        {site_header("", "archive")}
        <main class="archive-shell">
          <section class="archive-intro">
            <p class="section-kicker">Archivio completo</p>
            <h1>Tutti gli articoli</h1>
            <p>Ricerca per titolo o testo introduttivo. I link interni sono stati riscritti verso questa copia statica.</p>
            <label class="search-box">
              <span>Cerca</span>
              <input type="search" id="archive-search" name="q" placeholder="ramatura, selenio, Biringuccio...">
            </label>
          </section>
          <section class="post-grid archive-grid" id="archive-grid">
            {cards}
          </section>
        </main>
        {site_footer("")}
        """
    )
    return page_shell(
        title=f"Archivio · {SITE_TITLE}",
        description=f"Archivio completo di {SITE_TITLE}",
        body=body,
        css_href="assets/style.css",
        js_href="assets/site.js",
        body_class="archive-page",
    )


def render_src_index() -> str:
    body = textwrap.dedent(
        """\
        <main class="redirect-page">
          <h1>Archivio articoli</h1>
          <p>Vai all'<a href="../archive.html">archivio completo</a>.</p>
        </main>
        """
    )
    return page_shell(
        title=f"Articoli · {SITE_TITLE}",
        description="Indice articoli",
        body=body,
        css_href="../assets/style.css",
    )


def write_css() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    (ASSETS_DIR / "style.css").write_text(
        textwrap.dedent(
            """\
            :root {
              color-scheme: dark;
              --bg: #151311;
              --bg-soft: #201c18;
              --panel: #24211d;
              --panel-2: #2e2923;
              --text: #f2eadf;
              --muted: #bdb09f;
              --line: #4b4035;
              --accent: #5fd0a3;
              --accent-2: #f0b35a;
              --accent-3: #f47f6b;
              --link: #8bd3ff;
              --shadow: 0 18px 60px rgba(0, 0, 0, .28);
            }

            * { box-sizing: border-box; }

            html { scroll-behavior: smooth; }

            body {
              margin: 0;
              min-width: 320px;
              background:
                linear-gradient(180deg, rgba(95, 208, 163, .08), transparent 340px),
                linear-gradient(135deg, rgba(240, 179, 90, .08), transparent 420px),
                var(--bg);
              color: var(--text);
              font: 17px/1.68 "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
              letter-spacing: 0;
            }

            a { color: var(--link); text-decoration-thickness: .08em; text-underline-offset: .16em; }
            a:hover { color: #c6eaff; }

            .site-header {
              position: sticky;
              top: 0;
              z-index: 20;
              display: flex;
              align-items: center;
              justify-content: space-between;
              gap: 24px;
              padding: 16px clamp(18px, 4vw, 56px);
              border-bottom: 1px solid rgba(255, 255, 255, .08);
              background: rgba(21, 19, 17, .9);
              backdrop-filter: blur(14px);
            }

            .brand {
              display: inline-flex;
              align-items: center;
              gap: 12px;
              color: var(--text);
              text-decoration: none;
              min-width: 220px;
            }

            .brand-mark {
              display: grid;
              place-items: center;
              width: 42px;
              height: 42px;
              border: 1px solid rgba(95, 208, 163, .7);
              background: #16241f;
              color: var(--accent);
              font-weight: 800;
              font-size: 14px;
            }

            .brand strong { display: block; font-size: 16px; line-height: 1.1; }
            .brand small { display: block; margin-top: 3px; color: var(--muted); font-size: 12px; line-height: 1.2; }

            .site-nav {
              display: flex;
              align-items: center;
              justify-content: flex-end;
              flex-wrap: wrap;
              gap: 6px 18px;
              font-size: 14px;
            }

            .site-nav a {
              color: var(--muted);
              text-decoration: none;
              padding: 8px 0;
            }

            .site-nav a[aria-current="page"], .site-nav a:hover { color: var(--text); }

            main { width: min(1180px, calc(100% - 36px)); margin: 0 auto; }

            .hero {
              min-height: min(760px, calc(100vh - 74px));
              position: relative;
              display: flex;
              align-items: flex-end;
              overflow: hidden;
              width: min(100vw, 100%);
              margin: 0 auto 54px;
              padding: clamp(56px, 9vh, 96px) 0 42px;
              background-image:
                linear-gradient(90deg, rgba(10, 9, 8, .82) 0%, rgba(10, 9, 8, .46) 43%, rgba(10, 9, 8, .08) 100%),
                linear-gradient(0deg, rgba(10, 9, 8, .72) 0%, rgba(10, 9, 8, .08) 46%, rgba(10, 9, 8, .42) 100%),
                url("../img/hero-blog.jpg");
              background-size: cover;
              background-position: center right;
              border-bottom: 1px solid rgba(255, 255, 255, .08);
            }

            .hero-copy {
              width: min(720px, calc(100% - 48px));
              margin-left: clamp(24px, 6vw, 74px);
              padding: clamp(20px, 3vw, 34px);
              background: rgba(12, 11, 10, .48);
              border: 1px solid rgba(255, 255, 255, .13);
              box-shadow: var(--shadow);
              backdrop-filter: blur(6px);
            }

            .hero h1 {
              margin: 0;
              max-width: 720px;
              font-family: Georgia, "Times New Roman", serif;
              font-size: clamp(44px, 5.6vw, 78px);
              line-height: .95;
              font-weight: 700;
              text-shadow: 0 4px 26px rgba(0, 0, 0, .75);
            }

            .hero-copy > p:not(.section-kicker) {
              max-width: 690px;
              color: #fff3df;
              font-size: clamp(18px, 2.2vw, 25px);
              text-shadow: 0 2px 18px rgba(0, 0, 0, .8);
            }

            .section-kicker {
              margin: 0 0 12px;
              color: var(--accent);
              font-size: 12px;
              font-weight: 800;
              letter-spacing: .12em;
              text-transform: uppercase;
            }

            .hero-actions { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 28px; }

            .button {
              display: inline-flex;
              align-items: center;
              justify-content: center;
              min-height: 44px;
              padding: 10px 16px;
              border: 1px solid var(--line);
              color: var(--text);
              background: rgba(255, 255, 255, .04);
              text-decoration: none;
              font: inherit;
              font-weight: 700;
              cursor: pointer;
            }

            .button.primary {
              border-color: rgba(95, 208, 163, .75);
              background: var(--accent);
              color: #102017;
            }

            .stats-band {
              display: grid;
              grid-template-columns: repeat(4, 1fr);
              gap: 1px;
              overflow: hidden;
              margin: 0 0 64px;
              border: 1px solid var(--line);
              background: var(--line);
            }

            .stats-band div {
              padding: 22px;
              background: rgba(36, 33, 29, .92);
            }

            .stats-band strong {
              display: block;
              font-size: clamp(24px, 4vw, 42px);
              line-height: 1;
              color: var(--accent-2);
            }

            .stats-band span {
              display: block;
              margin-top: 7px;
              color: var(--muted);
              font-size: 14px;
            }

            .home-search {
              margin: 0 0 64px;
              padding: clamp(22px, 4vw, 34px);
              border: 1px solid var(--line);
              background:
                linear-gradient(135deg, rgba(95, 208, 163, .12), rgba(244, 127, 107, .08)),
                rgba(36, 33, 29, .76);
              box-shadow: 0 12px 28px rgba(0, 0, 0, .1);
            }

            .home-search .section-heading {
              margin-bottom: 0;
            }

            .home-search-box {
              margin-top: 18px;
            }

            .search-row {
              display: flex;
              align-items: stretch;
              gap: 10px;
              width: min(100%, 760px);
            }

            .search-row input {
              flex: 1;
              width: auto;
            }

            .search-row .button {
              min-height: 48px;
            }

            .search-status {
              margin: 16px 0 0;
              color: var(--muted);
              font-size: 14px;
            }

            .search-results-grid {
              margin-top: 18px;
            }

            .content-section, .archive-shell {
              padding: 20px 0 64px;
            }

            .section-heading {
              display: flex;
              align-items: end;
              justify-content: space-between;
              gap: 24px;
              margin-bottom: 22px;
            }

            .section-heading h2, .archive-intro h1 {
              margin: 0;
              font-family: Georgia, "Times New Roman", serif;
              font-size: clamp(34px, 5vw, 58px);
              line-height: 1;
            }

            .post-grid {
              display: grid;
              grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
              gap: 16px;
            }

            .compact-grid { grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }
            .archive-grid { align-items: stretch; }

            .post-card {
              display: grid;
              grid-template-rows: 180px 1fr;
              overflow: hidden;
              border: 1px solid var(--line);
              background: rgba(36, 33, 29, .86);
              box-shadow: 0 12px 28px rgba(0, 0, 0, .12);
            }

            .post-card.compact { grid-template-rows: 150px 1fr; }

            .thumb {
              display: block;
              min-height: 0;
              background: var(--panel-2);
              text-decoration: none;
            }

            .thumb img {
              width: 100%;
              height: 100%;
              object-fit: cover;
              display: block;
            }

            .thumb-placeholder {
              display: grid;
              place-items: center;
              height: 100%;
              color: rgba(240, 179, 90, .92);
              background:
                linear-gradient(135deg, rgba(95, 208, 163, .16), rgba(244, 127, 107, .14)),
                var(--panel-2);
            }

            .thumb-placeholder span {
              font-family: Georgia, "Times New Roman", serif;
              font-size: 54px;
              font-weight: 700;
            }

            .post-card-body { padding: 18px; }
            .post-card-meta { margin: 0 0 8px; color: var(--accent-2); font-size: 12px; font-weight: 800; text-transform: uppercase; }
            .post-card h2 { margin: 0 0 10px; font-size: 21px; line-height: 1.2; }
            .post-card h2 a { color: var(--text); text-decoration: none; }
            .post-card h2 a:hover { color: var(--link); }
            .post-card p:last-child { margin: 0; color: var(--muted); font-size: 15px; line-height: 1.5; }

            .archive-intro {
              max-width: 860px;
              padding: 52px 0 28px;
            }

            .archive-intro p { color: var(--muted); }

            .search-box {
              display: grid;
              gap: 7px;
              margin-top: 24px;
            }

            .search-box span {
              color: var(--accent);
              font-size: 13px;
              font-weight: 800;
              text-transform: uppercase;
            }

            .search-box input {
              width: min(100%, 720px);
              min-height: 48px;
              padding: 11px 14px;
              border: 1px solid var(--line);
              background: #110f0d;
              color: var(--text);
              font: inherit;
            }

            .article-shell {
              width: min(940px, calc(100% - 36px));
              padding: 44px 0 74px;
            }

            .article {
              border: 1px solid var(--line);
              background: rgba(27, 24, 21, .92);
              box-shadow: var(--shadow);
            }

            .article-head {
              padding: clamp(24px, 5vw, 48px);
              border-bottom: 1px solid var(--line);
              background:
                linear-gradient(135deg, rgba(95, 208, 163, .12), transparent 46%),
                rgba(36, 33, 29, .78);
            }

            .back-link {
              color: var(--accent);
              font-size: 14px;
              font-weight: 800;
              text-decoration: none;
            }

            .article-meta {
              margin: 18px 0 10px;
              color: var(--muted);
              font-size: 14px;
            }

            .article h1 {
              margin: 0;
              font-family: Georgia, "Times New Roman", serif;
              font-size: clamp(34px, 6vw, 64px);
              line-height: 1.05;
            }

            .post-body {
              padding: clamp(24px, 5vw, 50px);
              color: #eadfce;
              overflow-wrap: anywhere;
            }

            .post-body p { margin: 0 0 1.15em; }
            .post-body br { line-height: 1.7; }
            .post-body img {
              max-width: 100%;
              height: auto;
              vertical-align: middle;
              border: 1px solid rgba(255, 255, 255, .08);
              background: rgba(255, 255, 255, .03);
            }

            .post-body table { max-width: 100%; overflow-x: auto; }
            .post-body sub, .post-body sup { line-height: 0; }

            .comments {
              margin-top: 28px;
              padding: clamp(22px, 4vw, 36px);
              border: 1px solid var(--line);
              background: rgba(36, 33, 29, .78);
            }

            .comments h2 {
              margin: 0 0 20px;
              font-family: Georgia, "Times New Roman", serif;
              font-size: 34px;
            }

            .comments-list {
              list-style: none;
              margin: 0;
              padding: 0;
            }

            .comment {
              margin-top: 12px;
              padding: 14px;
              border-left: 3px solid var(--line);
              background: rgba(255, 255, 255, .035);
            }

            .comment .comments-list { margin-left: clamp(12px, 3vw, 28px); }
            .comment-meta { color: var(--accent-2); font-weight: 800; font-size: 14px; }
            .comment-body { margin-top: 6px; color: #e6dac9; }
            .author-comment { border-left-color: var(--accent); }

            .article-nav {
              display: grid;
              grid-template-columns: 1fr 1fr;
              gap: 14px;
              margin-top: 28px;
            }

            .article-nav-link {
              min-height: 98px;
              padding: 18px;
              border: 1px solid var(--line);
              background: rgba(36, 33, 29, .78);
              color: var(--text);
              text-decoration: none;
            }

            .article-nav-link small {
              display: block;
              color: var(--accent);
              font-size: 12px;
              font-weight: 800;
              text-transform: uppercase;
            }

            .article-nav-link strong {
              display: block;
              margin-top: 8px;
              line-height: 1.25;
            }

            .article-nav-link.muted {
              display: grid;
              place-items: center;
              color: var(--muted);
            }

            .site-footer {
              width: min(1180px, calc(100% - 36px));
              margin: 0 auto;
              padding: 32px 0 46px;
              border-top: 1px solid var(--line);
              color: var(--muted);
              font-size: 14px;
            }

            .site-footer p { margin: 0 0 6px; }
            .redirect-page { padding: 64px 20px; }

            [hidden] { display: none !important; }

            @media (max-width: 820px) {
              .site-header { position: static; align-items: flex-start; flex-direction: column; }
              .brand { min-width: 0; }
              .hero {
                min-height: 620px;
                padding-top: 42px;
                background-image:
                  linear-gradient(0deg, rgba(10, 9, 8, .86) 0%, rgba(10, 9, 8, .32) 58%, rgba(10, 9, 8, .58) 100%),
                  url("../img/hero-blog.jpg");
                background-position: center;
              }
              .hero-copy {
                width: calc(100% - 24px);
                margin: 0 12px;
              }
              .stats-band { grid-template-columns: repeat(2, 1fr); }
              .section-heading { align-items: flex-start; flex-direction: column; }
              .article-nav { grid-template-columns: 1fr; }
              .post-body img[style*="float"] {
                float: none !important;
                display: block;
                margin: 12px auto !important;
              }
            }

            @media (max-width: 520px) {
              body { font-size: 16px; }
              main, .article-shell, .site-footer { width: min(100% - 24px, 1180px); }
              .site-header { padding: 14px 12px; }
              .site-nav { justify-content: flex-start; }
              .stats-band { grid-template-columns: 1fr; }
              .search-row { flex-direction: column; }
              .search-row .button { width: 100%; }
              .post-grid { grid-template-columns: 1fr; }
              .post-card { grid-template-rows: 160px 1fr; }
              .post-body, .article-head { padding: 22px; }
            }
            """
        ),
        encoding="utf-8",
    )


def write_js() -> None:
    (ASSETS_DIR / "site.js").write_text(
        textwrap.dedent(
            """\
            const normalizeText = (value) =>
              (value || "")
                .toString()
                .normalize("NFD")
                .replace(/[\\u0300-\\u036f]/g, "")
                .toLowerCase();

            const archiveInput = document.querySelector("#archive-search");
            const archiveCards = Array.from(document.querySelectorAll("[data-search]"));

            function filterArchive() {
              const query = normalizeText(archiveInput.value.trim());
              for (const card of archiveCards) {
                const haystack = normalizeText(card.dataset.search);
                card.hidden = query.length > 0 && !haystack.includes(query);
              }
            }

            if (archiveInput) {
              const params = new URLSearchParams(window.location.search);
              const initialQuery = params.get("q");
              if (initialQuery) {
                archiveInput.value = initialQuery;
              }
              archiveInput.addEventListener("input", filterArchive);
              filterArchive();
            }

            const homeInput = document.querySelector("#home-search");
            const homeResults = document.querySelector("#home-search-results");
            const homeStatus = document.querySelector("#home-search-status");
            let postsPromise;
            let homeSearchRun = 0;

            function loadPosts() {
              if (!postsPromise) {
                postsPromise = fetch("data/posts.json")
                  .then((response) => (response.ok ? response.json() : []))
                  .catch(() => []);
              }
              return postsPromise;
            }

            function createPostCard(post) {
              const article = document.createElement("article");
              article.className = "post-card compact";
              article.dataset.search = normalizeText(post.search || `${post.title} ${post.excerpt}`);

              const thumb = document.createElement("a");
              thumb.className = "thumb";
              thumb.href = post.path;
              thumb.setAttribute("aria-hidden", "true");
              thumb.tabIndex = -1;

              if (post.image) {
                const image = document.createElement("img");
                image.src = post.image;
                image.alt = "";
                image.loading = "lazy";
                image.decoding = "async";
                thumb.append(image);
              } else {
                const placeholder = document.createElement("div");
                placeholder.className = "thumb-placeholder";
                const number = document.createElement("span");
                number.textContent = post.number;
                placeholder.append(number);
                thumb.append(placeholder);
              }

              const body = document.createElement("div");
              body.className = "post-card-body";

              const meta = document.createElement("p");
              meta.className = "post-card-meta";
              meta.textContent = `n. ${post.number} · ${post.date_label}`;
              if (post.comments) {
                meta.textContent += ` · ${post.comments} commenti`;
              }

              const title = document.createElement("h2");
              const link = document.createElement("a");
              link.href = post.path;
              link.textContent = post.title;
              title.append(link);

              const excerpt = document.createElement("p");
              excerpt.textContent = post.excerpt || "";

              body.append(meta, title, excerpt);
              article.append(thumb, body);
              return article;
            }

            async function renderHomeSearch() {
              const run = ++homeSearchRun;
              const query = normalizeText(homeInput.value.trim());
              homeResults.replaceChildren();

              if (!query) {
                homeResults.hidden = true;
                homeStatus.hidden = true;
                return;
              }

              const posts = await loadPosts();
              if (run !== homeSearchRun) {
                return;
              }

              const matches = posts.filter((post) =>
                normalizeText(post.search || `${post.title} ${post.excerpt}`).includes(query)
              );
              const visibleMatches = matches.slice(0, 12);

              for (const post of visibleMatches) {
                homeResults.append(createPostCard(post));
              }

              homeResults.hidden = visibleMatches.length === 0;
              homeStatus.hidden = false;
              if (matches.length === 0) {
                homeStatus.textContent = "Nessun articolo trovato.";
              } else if (matches.length > visibleMatches.length) {
                homeStatus.textContent = `Primi ${visibleMatches.length} di ${matches.length} risultati.`;
              } else {
                homeStatus.textContent = `${matches.length} risultati.`;
              }
            }

            if (homeInput && homeResults && homeStatus) {
              homeInput.addEventListener("input", renderHomeSearch);
              homeInput.form?.addEventListener("submit", (event) => {
                if (!homeInput.value.trim()) {
                  event.preventDefault();
                }
              });
            }
            """
        ),
        encoding="utf-8",
    )


def write_support_files(posts: list[Post]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "posts.json").write_text(
        json.dumps(
            [
                {
                    "id": post.post_id,
                    "number": post.number,
                    "title": post.title,
                    "date": post.date.isoformat(),
                    "date_label": post.date.strftime("%d/%m/%Y"),
                    "path": f"src/{post.filename}",
                    "comments": count_comments(post.comments),
                    "image": post.first_image,
                    "excerpt": post.excerpt,
                    "search": clean_text(f"{post.title} {post.excerpt} {post.number}"),
                }
                for post in posts
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (ROOT / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    (ROOT / "404.html").write_text(
        page_shell(
            title=f"Pagina non trovata · {SITE_TITLE}",
            description="Pagina non trovata",
            css_href="assets/style.css",
            body=textwrap.dedent(
                """\
                <main class="redirect-page">
                  <h1>Pagina non trovata</h1>
                  <p>Vai alla <a href="index.html">home</a> o all'<a href="archive.html">archivio</a>.</p>
                </main>
                """
            ),
        ),
        encoding="utf-8",
    )
    (ROOT / ".nojekyll").write_text("", encoding="utf-8")


def prepare_output_dirs() -> None:
    for directory in (SRC_DIR, ASSETS_DIR, DATA_DIR):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)
    IMG_DIR.mkdir(parents=True, exist_ok=True)


def main() -> None:
    started = time.time()
    posts_by_id = read_csv_posts()
    exported_html = read_exported_article_html()

    for post_id, post in posts_by_id.items():
        post.body_html = exported_html.get(post_id, "")
        post.excerpt = short_excerpt(post.body_html)

    image_urls: list[str] = []
    for post in posts_by_id.values():
        urls = image_urls_from_html(post.body_html)
        image_urls.extend(urls)
        if urls:
            post.first_image = urls[0]

    print(f"Posts: {len(posts_by_id)}")
    print(f"Images referenced: {len(set(image_urls))}")

    prepare_output_dirs()
    image_map = download_images(image_urls)

    for post in posts_by_id.values():
        if post.first_image:
            post.first_image = image_map.get(post.first_image)
        post.body_html = rewrite_image_sources(post.body_html, image_map, "../")
        post.body_html = rewrite_internal_links(post.body_html, posts_by_id, "")

    ordered_asc = sorted(posts_by_id.values(), key=lambda p: p.number)
    ordered_desc = sorted(posts_by_id.values(), key=lambda p: p.date, reverse=True)

    write_css()
    write_js()

    for post in ordered_asc:
        (SRC_DIR / post.filename).write_text(
            render_article(post, posts_by_id, ordered_asc),
            encoding="utf-8",
        )
        alias = SRC_DIR / f"{post.post_id}.html"
        alias.write_text(
            textwrap.dedent(
                f"""\
                <!doctype html>
                <html lang="it">
                <head>
                  <meta charset="utf-8">
                  <meta http-equiv="refresh" content="0; url={post.filename}">
                  <link rel="canonical" href="{post.filename}">
                  <title>{html.escape(post.title)}</title>
                </head>
                <body><p><a href="{post.filename}">{html.escape(post.title)}</a></p></body>
                </html>
                """
            ),
            encoding="utf-8",
        )

    (SRC_DIR / "index.html").write_text(render_src_index(), encoding="utf-8")
    (ROOT / "index.html").write_text(render_home(ordered_desc), encoding="utf-8")
    (ROOT / "archive.html").write_text(render_archive(ordered_desc), encoding="utf-8")
    write_support_files(ordered_desc)

    print(f"Site generated in {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
