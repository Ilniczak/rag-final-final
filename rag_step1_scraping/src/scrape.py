#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polite scraper for building a small (~1 MB) plain-text corpus.
- Respects robots.txt
- Uses trafilatura for robust text extraction
- Wikipedia fast-path via REST API for clean plaintext
- Optional shallow crawling within same domain
"""
import argparse
import os
import re
import sys
import time
import json
import hashlib
import logging
import random
from pathlib import Path
from urllib.parse import urlparse, urljoin, unquote
from urllib import robotparser

import requests
from bs4 import BeautifulSoup
import trafilatura

DEFAULT_UA = "RAG-Course-Scraper/1.0 (+https://example.edu; contact=student@example.com)"

def slugify(url: str) -> str:
    parsed = urlparse(url)
    base = (parsed.netloc + parsed.path).strip("/")
    base = re.sub(r"[^a-zA-Z0-9\-_.]+", "-", base)
    if not base:
        base = "page"
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"{base}-{h}"

def read_seeds(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]

def ok_by_robots(url: str, ua: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = robotparser.RobotFileParser()
    try:
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch(ua, url)
    except Exception:
        # If robots fetch fails, be conservative but allow (common practice for small academic scraping)
        return True

def fetch_wikipedia_plain(url: str, session: requests.Session, timeout: int = 15) -> tuple[str|None, str|None]:
    # Expected wiki URL format: https://en.wikipedia.org/wiki/Some_Title
    parsed = urlparse(url)
    if "wikipedia.org" not in parsed.netloc or "/wiki/" not in parsed.path:
        return None, None
    title = unquote(parsed.path.split("/wiki/")[-1])
    api_url = f"https://{parsed.netloc}/api/rest_v1/page/plain/{title}"
    r = session.get(api_url, timeout=timeout)
    if r.status_code != 200 or not r.text.strip():
        return None, None
    # Title via summary endpoint (best effort)
    try:
        summary_url = f"https://{parsed.netloc}/api/rest_v1/page/summary/{title}"
        s = session.get(summary_url, timeout=timeout)
        doc_title = s.json().get("title") if s.ok else title.replace("_", " ")
    except Exception:
        doc_title = title.replace("_", " ")
    return doc_title, r.text

def extract_with_trafilatura(url: str, html: str) -> tuple[str|None, str|None]:
    downloaded = trafilatura.extract(html, include_comments=False, target_language=None)
    if not downloaded:
        return None, None
    # Try to get title from metadata if available
    try:
        meta = trafilatura.bare_extraction(html, with_metadata=True)
        title = meta.get("title") if meta else None
    except Exception:
        title = None
    return title, downloaded

def collect_links(base_url: str, html: str, same_domain: bool) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    links = set()
    base_domain = urlparse(base_url).netloc
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        abs_url = urljoin(base_url, href)
        if same_domain and urlparse(abs_url).netloc != base_domain:
            continue
        links.add(abs_url)
    return list(links)

def save_txt(out_dir: Path, url: str, title: str|None, text: str) -> int:
    slug = slugify(url)
    path = out_dir / f"{slug}.txt"
    header = []
    header.append(f"URL: {url}")
    header.append(f"TITLE: {title or ''}".strip())
    header.append(f"CRAWLED_AT: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    header_str = "\n".join(header) + "\n\n"
    content = header_str + text.strip() + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return len(content.encode("utf-8"))

def main():
    ap = argparse.ArgumentParser(description="Polite scraper for small RAG corpus.")
    ap.add_argument("--seeds", required=True, help="Path to a file with seed URLs (one per line).")
    ap.add_argument("--out", required=True, help="Directory to save .txt files.")
    ap.add_argument("--max-total-bytes", type=int, default=1_200_000, help="Corpus size budget (bytes).")
    ap.add_argument("--delay-seconds", type=float, default=2.0, help="Polite delay between requests.")
    ap.add_argument("--timeout", type=int, default=20, help="HTTP timeout seconds.")
    ap.add_argument("--crawl", action="store_true", help="Light crawling from seeds.")
    ap.add_argument("--max-follow", type=int, default=5, help="Max extra links to follow per seed if --crawl.")
    ap.add_argument("--same-domain", action="store_true", help="When crawling, stay within the seed domain.")
    ap.add_argument("--user-agent", default=DEFAULT_UA, help="Custom User-Agent.")
    args = ap.parse_args()

    logging.basicConfig(
        filename=Path("logs") / "scrape.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s"
    )
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": args.user_agent, "Accept-Language": "en;q=0.9,pl;q=0.8"})

    seeds = read_seeds(args.seeds)
    total_bytes = 0
    seen_hashes = set()
    seen_urls = set()

    def process_url(url: str):
        nonlocal total_bytes, seen_hashes
        if url in seen_urls:
            return
        seen_urls.add(url)

        if total_bytes >= args.max_total_bytes:
            return

        if not ok_by_robots(url, args.user_agent):
            logging.info(f"ROBOTS-BLOCKED {url}")
            return

        try:
            # Wikipedia fast-path
            title, text = fetch_wikipedia_plain(url, session, timeout=args.timeout)
            if not text:
                # General fetch
                r = session.get(url, timeout=args.timeout)
                ctype = r.headers.get("Content-Type", "")
                if "text/html" not in ctype:
                    logging.info(f"SKIP-NONHTML {url} ({ctype})")
                    return
                html = r.text
                # Extract text
                title, text = extract_with_trafilatura(url, html)
                if not text or len(text.strip()) < 300:
                    logging.info(f"EXTRACTION-FAILED/SHORT {url}")
                    return

            # Deduplicate by content hash
            h = hashlib.sha1(text.strip().encode("utf-8")).hexdigest()
            if h in seen_hashes:
                logging.info(f"DUPLICATE {url}")
                return
            seen_hashes.add(h)

            # Save
            added = save_txt(out_dir, url, title, text)
            total_bytes += added
            logging.info(f"SAVED {url} -> +{added} bytes, total={total_bytes}")

        except Exception as e:
            logging.exception(f"ERROR {url}: {e}")

    for seed in seeds:
        if total_bytes >= args.max_total_bytes:
            break
        process_url(seed)
        time.sleep(args.delay_seconds + random.random())

        if args.crawl and total_bytes < args.max_total_bytes:
            # fetch HTML once for links
            try:
                r = session.get(seed, timeout=args.timeout)
                if r.ok and "text/html" in r.headers.get("Content-Type", ""):
                    links = collect_links(seed, r.text, same_domain=args.same_domain)
                    random.shuffle(links)
                    for link in links[:max(0, args.max_follow)]:
                        if total_bytes >= args.max_total_bytes:
                            break
                        process_url(link)
                        time.sleep(args.delay_seconds + random.random())
            except Exception:
                logging.exception(f"CRAWL-ERROR seed={seed}")

    print(f"Done. Wrote ~{total_bytes} bytes to {out_dir.resolve()}")

if __name__ == "__main__":
    main()
