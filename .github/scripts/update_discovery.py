"""Refresh sitemap dates, screenshot entries and Atom feeds from published pages."""

from datetime import datetime, timezone
from html import unescape
import re
import subprocess
import xml.etree.ElementTree as ET

from check_site import ATOM, IMAGE, NS, ROOT, SITE, Page, local_target


HUB = "https://pubsubhubbub.appspot.com/"
SITEMAP = ROOT / "sitemap.xml"
FEEDS = {
    "fr": ("actualites.atom", "Guides et versions de Fauteuil", "Dernières pages et guides de Fauteuil pour macOS"),
    "en": ("en/updates.atom", "Fauteuil guides and releases", "Recent Fauteuil pages and guides for macOS"),
}


def text(node, namespace, name, value):
    ET.SubElement(node, f"{{{namespace}}}{name}").text = value


def modified_date(file):
    name = str(file.relative_to(ROOT))
    committed = subprocess.check_output(
        ["git", "log", "-1", "--format=%cI", "--", name], cwd=ROOT, text=True
    ).strip()
    unchanged = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", name], cwd=ROOT, check=False
    ).returncode == 0
    if committed and unchanged:
        return datetime.fromisoformat(committed)
    return datetime.now(timezone.utc)


def write_sitemap():
    ET.register_namespace("", NS["s"])
    ET.register_namespace("xhtml", NS["x"])
    ET.register_namespace("image", IMAGE)
    sitemap = ET.parse(SITEMAP)
    entries = []
    for node in sitemap.findall("s:url", NS):
        url = node.findtext("s:loc", namespaces=NS)
        file = local_target(url)
        entry = ET.Element(f"{{{NS['s']}}}lastmod")
        modified = modified_date(file)
        entry.text = modified.date().isoformat()
        for child in list(node):
            if child.tag in (entry.tag, f"{{{IMAGE}}}image"):
                node.remove(child)
        node.insert(1, entry)
        if url in (SITE, SITE + "en/"):
            for name in ("bibliotheque.jpg", "playlists.jpg"):
                item = ET.SubElement(node, f"{{{IMAGE}}}image")
                text(item, IMAGE, "loc", SITE + "assets/" + name)
        else:
            markup = file.read_text(encoding="utf-8")
            page = Page(markup)
            lang = page.select("html")[0]["lang"]
            title = unescape(re.search(r"<title>([^<]+)</title>", markup)[1])
            summary = page.select("meta", name="description")[0]["content"]
            entries.append((lang, modified, url, title, summary))
    ET.indent(sitemap, space="  ")
    sitemap.write(SITEMAP, encoding="utf-8", xml_declaration=True)
    return entries


def write_feeds(entries):
    ET.register_namespace("", ATOM)
    for lang, (path, title, summary) in FEEDS.items():
        url = SITE + path
        home = SITE if lang == "fr" else SITE + "en/"
        feed = ET.Element(f"{{{ATOM}}}feed", {"xml:lang": lang})
        items = [(date, link, name, excerpt) for code, date, link, name, excerpt in entries if code == lang]
        items.sort(reverse=True)
        text(feed, ATOM, "title", title)
        text(feed, ATOM, "subtitle", summary)
        text(feed, ATOM, "id", url)
        text(feed, ATOM, "updated", items[0][0].isoformat(timespec="seconds"))
        for rel, href in (("self", url), ("hub", HUB), ("alternate", home)):
            ET.SubElement(feed, f"{{{ATOM}}}link", {"rel": rel, "href": href})
        author = ET.SubElement(feed, f"{{{ATOM}}}author")
        text(author, ATOM, "name", "Fauteuil")
        for date, link, name, excerpt in items:
            entry = ET.SubElement(feed, f"{{{ATOM}}}entry")
            text(entry, ATOM, "id", link)
            text(entry, ATOM, "title", name)
            text(entry, ATOM, "summary", excerpt)
            text(entry, ATOM, "updated", date.isoformat(timespec="seconds"))
            ET.SubElement(entry, f"{{{ATOM}}}link", {"rel": "alternate", "href": link})
        tree = ET.ElementTree(feed)
        ET.indent(tree, space="  ")
        tree.write(ROOT / path, encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    write_feeds(write_sitemap())
