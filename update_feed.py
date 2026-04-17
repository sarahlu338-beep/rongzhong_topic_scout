import json
import re
import html
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


USER_AGENT = "Mozilla/5.0"
TIMEOUT = 20

NS = {
    "atom": "http://www.w3.org/2005/Atom",
}


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="ignore")


def fetch_xml(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def get_text(node, tag, default=""):
    child = node.find(tag, NS) if node is not None else None
    if child is not None and child.text:
        return child.text.strip()
    return default


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_meta(html_text: str, key: str) -> str:
    patterns = [
        rf'<meta[^>]+property=["\']{re.escape(key)}["\'][^>]+content=["\'](.*?)["\']',
        rf'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']{re.escape(key)}["\']',
        rf'<meta[^>]+name=["\']{re.escape(key)}["\'][^>]+content=["\'](.*?)["\']',
        rf'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']{re.escape(key)}["\']',
    ]
    for pattern in patterns:
        m = re.search(pattern, html_text, re.IGNORECASE | re.DOTALL)
        if m:
            return clean_text(m.group(1))
    return ""


def extract_title_tag(html_text: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
    if m:
        return clean_text(m.group(1))
    return ""


def parse_datetime_from_meta(html_text: str) -> str:
    candidates = [
        extract_meta(html_text, "article:published_time"),
        extract_meta(html_text, "og:published_time"),
        extract_meta(html_text, "publishdate"),
        extract_meta(html_text, "pubdate"),
        extract_meta(html_text, "date"),
    ]
    for value in candidates:
        if value:
            return value[:32]
    return ""


def enrich_product_hunt_summary(link: str) -> str:
    try:
        html_text = fetch_text(link)
        summary = extract_meta(html_text, "og:description") or extract_meta(html_text, "description")
        return summary[:800]
    except Exception:
        return ""


def parse_webpage(url: str, fallback_title: str = ""):
    try:
        html_text = fetch_text(url)

        title = (
            extract_meta(html_text, "og:title")
            or extract_meta(html_text, "twitter:title")
            or extract_title_tag(html_text)
            or fallback_title
        )

        summary = (
            extract_meta(html_text, "og:description")
            or extract_meta(html_text, "description")
        )

        published_at = parse_datetime_from_meta(html_text)

        return {
            "title": title[:300],
            "published_at": published_at,
            "link": url,
            "summary": summary[:800]
        }
    except Exception as e:
        return {
            "title": fallback_title,
            "published_at": "",
            "link": url,
            "summary": f"WEBPAGE_ERROR: {str(e)}"
        }


def parse_yc_launches_page(url: str):
    return parse_webpage(url, fallback_title="YC Launches")


def parse_rss_feed(url: str):
    try:
        content = fetch_xml(url)
        root = ET.fromstring(content)

        channel = root.find("channel")
        if channel is not None:
            item = channel.find("item")
            if item is not None:
                title = item.findtext("title", "").strip()
                link = item.findtext("link", url).strip()
                published_at = item.findtext("pubDate", "").strip()
                summary = (
                    item.findtext("description", "").strip()
                    or item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded", "").strip()
                )
                return {
                    "title": title,
                    "published_at": published_at,
                    "link": link,
                    "summary": clean_text(summary)[:800]
                }

        entry = root.find("atom:entry", NS)
        if entry is not None:
            title = get_text(entry, "atom:title")
            published_at = get_text(entry, "atom:published") or get_text(entry, "atom:updated")
            summary = get_text(entry, "atom:summary")

            if not summary:
                content_node = entry.find("atom:content", NS)
                if content_node is not None and content_node.text:
                    summary = content_node.text.strip()

            link = url
            for link_node in entry.findall("atom:link", NS):
                href = link_node.attrib.get("href")
                rel = link_node.attrib.get("rel", "")
                if href and (rel == "alternate" or rel == ""):
                    link = href
                    break

            return {
                "title": title,
                "published_at": published_at,
                "link": link,
                "summary": clean_text(summary)[:800]
            }

    except Exception as e:
        return {
            "title": "",
            "published_at": "",
            "link": url,
            "summary": f"FETCH_ERROR: {str(e)}"
        }

    return {
        "title": "",
        "published_at": "",
        "link": url,
        "summary": "NO_ITEM_FOUND"
    }


def parse_source(source: dict):
    source_type = source.get("type", "")
    source_name = source.get("name", "")
    source_url = source.get("url", "")

    if source_name == "YC Launches":
        return parse_yc_launches_page(source_url)

    if source_type == "rss":
        parsed = parse_rss_feed(source_url)
        if source_name == "Product Hunt" and parsed["link"] and not parsed["summary"]:
            parsed["summary"] = enrich_product_hunt_summary(parsed["link"])
        return parsed

    if source_type == "webpage":
        return parse_webpage(source_url, fallback_title=source_name)

    return {
        "title": "",
        "published_at": "",
        "link": source_url,
        "summary": f"UNSUPPORTED_SOURCE_TYPE: {source_type}"
    }


def main():
    with open("sources.json", "r", encoding="utf-8") as f:
        sources = json.load(f)

    items = []

    for source in sources.get("websites", []):
        parsed = parse_source(source)

        items.append({
            "source": source["name"],
            "type": "website",
            "title": parsed["title"],
            "published_at": parsed["published_at"],
            "link": parsed["link"],
            "summary": parsed["summary"]
        })

    output = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": items
    }

    with open("daily_feed.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
