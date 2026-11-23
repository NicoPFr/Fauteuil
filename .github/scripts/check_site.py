"""Check Fauteuil's GitHub Pages site locally or after deployment."""

import argparse
from collections import deque
from datetime import date, datetime
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urljoin, urlsplit
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
SITE = "https://nicopfr.github.io/Fauteuil/"
LANGUAGES = {"fr", "en", "x-default"}
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9", "x": "http://www.w3.org/1999/xhtml"}
IMAGE = "http://www.google.com/schemas/sitemap-image/1.1"
ATOM = "http://www.w3.org/2005/Atom"
MAX_ASSET_BYTES = 160 * 1024


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.elements = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def select(self, tag, **filters):
        return [attrs for name, attrs in self.elements
                if name == tag and all(attrs.get(key) == value for key, value in filters.items())]

    @property
    def ids(self):
        return {attrs["id"] for _, attrs in self.elements if "id" in attrs}


def require(condition, context, message):
    if not condition:
        raise ValueError(f"{context}: {message}")


def local_target(url):
    prefix = urlsplit(SITE).path
    path = unquote(urlsplit(url).path).removeprefix(prefix)
    file = ROOT / path
    return file / "index.html" if not path or path.endswith("/") else file


def load(url, live):
    if live:
        return subprocess.check_output(
            ["curl", "-qfsS", "--max-time", "20", url], text=True
        )
    return local_target(url).read_text(encoding="utf-8")


def check(live):
    sitemap = ET.fromstring(load(SITE + "sitemap.xml", live))
    pairs = {}
    for entry in sitemap.findall("s:url", NS):
        url = entry.findtext("s:loc", namespaces=NS)
        require(url and url.startswith(SITE) and url not in pairs, url, "invalid sitemap URL")
        links = entry.findall("x:link", NS)
        pairs[url] = {link.attrib["hreflang"]: link.attrib["href"] for link in links}
        require(len(links) == 3 and set(pairs[url]) == LANGUAGES, url, "missing language alternate")

    pages_on_disk = set(ROOT.glob("**/index.html"))
    require(pages_on_disk == {local_target(url) for url in pairs}, "sitemap", "HTML pages and sitemap differ")
    html = {url: load(url, live) for url in pairs}
    pages = {url: Page(text) for url, text in html.items()}
    titles, descriptions = set(), set()
    reachable = {url: set() for url in pairs}
    assets = set()
    for url, page in pages.items():
        check_page(url, page, html[url], pairs, titles, descriptions)
        for tag, field in (("a", "href"), ("img", "src"), ("link", "href")):
            for attrs in page.select(tag):
                link = attrs.get(field)
                require(bool(link), url, f"missing {field} on {tag}")
                target = urljoin(url, link)
                require(urlsplit(target).scheme == "https", url, f"insecure link: {link}")
                if not target.startswith(SITE):
                    continue
                document, _, fragment = target.partition("#")
                require(local_target(document).is_file(), url, f"broken link: {target}")
                if tag == "a" and document in pages:
                    reachable[url].add(document)
                    if fragment:
                        require(unquote(fragment) in pages[document].ids, url, f"missing anchor: {target}")
                if document not in pages:
                    assets.add(document)
    visit_navigation(reachable)
    check_discovery(sitemap, pages, assets, live)

    page404 = Page((ROOT / "404.html").read_text(encoding="utf-8"))
    require(any("noindex" in meta["content"] for meta in page404.select("meta", name="robots")),
            "404.html", "error page should be excluded from indexing")
    blocks = [item for item in (ROOT / "assets").iterdir() if item.is_file()]
    size = sum(file.stat().st_size for file in blocks)
    require(size <= MAX_ASSET_BYTES, "assets", f"asset budget exceeded ({size} bytes)")
    if live:
        for url in sorted(assets):
            subprocess.run(["curl", "-qfsS", "--max-time", "20", "-o", "/dev/null", url], check=True)
    print(f"{len(pages)} pages, {len(assets)} linked assets and a {size:,}-byte asset budget verified.")


def check_discovery(sitemap, pages, assets, live):
    for row in sitemap.findall("s:url", NS):
        url = row.findtext("s:loc", namespaces=NS)
        stamps = row.findall("s:lastmod", NS)
        require(len(stamps) == 1 and date.fromisoformat(stamps[0].text), url, "missing modification date")
        for image in row.findall(f".//{{{IMAGE}}}loc"):
            target = image.text
            require(target.startswith(SITE) and local_target(target).is_file(), url, "missing screenshot")
            assets.add(target)

    atom = {"atom": ATOM}
    for lang, home in (("fr", SITE), ("en", SITE + "en/")):
        links = pages[home].select("link", rel="alternate", type="application/atom+xml")
        require(len(links) == 1, home, "missing Atom feed link")
        url = urljoin(home, links[0]["href"])
        feed = ET.fromstring(load(url, live))
        require(feed.tag == f"{{{ATOM}}}feed" and feed.findtext("atom:id", namespaces=atom) == url,
                url, "unexpected feed identifier")
        require(any(link.attrib == {"rel": "self", "href": url} for link in feed.findall("atom:link", atom)),
                url, "missing feed self link")
        items = feed.findall("atom:entry", atom)
        entries = {entry.findtext("atom:id", namespaces=atom) for entry in items}
        expected = {url for url, page in pages.items() if page.select("html")[0]["lang"] == lang and url != home}
        require(entries == expected and len(items) == len(entries), url, "feed entries differ from the site")
        updates = [datetime.fromisoformat(item.findtext("atom:updated", namespaces=atom)) for item in items]
        require(updates and datetime.fromisoformat(feed.findtext("atom:updated", namespaces=atom)) == max(updates),
                url, "incorrect feed update date")


def check_page(url, page, text, pairs, titles, descriptions):
    doc = page.select("html")
    require(len(doc) == 1, url, "missing HTML element")
    language = doc[0].get("lang")
    require(language in {"fr", "en"} and pairs[url][language] == url, url, "wrong HTML language")
    require(len(page.select("h1")) == 1, url, "expected a single H1")
    require(page.select("link", rel="canonical") == [{"rel": "canonical", "href": url}],
            url, "expected one self-referencing canonical")
    tags = page.select("link", rel="alternate")
    alternates = {tag["hreflang"]: tag["href"] for tag in tags if "hreflang" in tag}
    require(alternates == pairs[url], url, "page and sitemap language links differ")
    require(alternates["x-default"] == alternates["fr"], url, "wrong default language")
    require(all(pairs.get(candidate) == alternates for candidate in alternates.values()),
            url, "language links are not reciprocal")
    require(not any("noindex" in tag.get("content", "").lower()
                    for tag in page.select("meta", name="robots")), url, "unexpected noindex")

    title = re.search(r"<title>([^<]+)</title>", text)
    require(title is not None and title[1].strip() and title[1] not in titles, url, "missing or duplicate title")
    titles.add(title[1])
    desc = page.select("meta", name="description")
    require(len(desc) == 1 and desc[0].get("content") and desc[0]["content"] not in descriptions,
            url, "missing or duplicate description")
    descriptions.add(desc[0]["content"])
    require(any(tag.get("content") == url for tag in page.select("meta", property="og:url")), url, "wrong share URL")
    require(all("alt" in tag and int(tag.get("width", 0)) > 0 and int(tag.get("height", 0)) > 0
                for tag in page.select("img")), url, "images need alternative text and dimensions")

    payload = re.search(r'<script type="application/ld[+]json">(.+?)</script>', text, re.S)
    require(payload is not None, url, "missing JSON-LD")
    attributes = json.loads(payload[1])
    if url in (SITE, SITE + "en/"):
        require(attributes.get("@type") == "SoftwareApplication" and attributes.get("url") == url,
                url, "invalid software JSON-LD")
    else:
        elements = attributes.get("itemListElement", [])
        require(attributes.get("@type") == "BreadcrumbList" and elements and elements[-1].get("item") == url,
                url, "invalid breadcrumb JSON-LD")


def visit_navigation(reachable):
    remaining = set(reachable)
    pending = deque((SITE, SITE + "en/"))
    while pending:
        url = pending.popleft()
        if url in remaining:
            remaining.remove(url)
            pending.extend(reachable[url] & remaining)
    require(not remaining, "navigation", f"orphan pages: {', '.join(sorted(remaining))}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="check the deployed site")
    check(parser.parse_args().live)
