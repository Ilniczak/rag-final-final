# RAG Project — Step 1: Data Scraping

This repo contains **Step 1** of your RAG pipeline: polite web/API scraping to build a ~1 MB .txt corpus.

## What’s here
- `src/scrape.py` — main scraper with robots.txt checks, polite delays, and Wikipedia API fast‑path.
- `seeds.txt` — example seed URLs (replace with your own).
- `data/raw/` — destination for plain‑text files.
- `logs/scrape.log` — simple log file with statuses.
- `requirements.txt` — minimal deps for Step 1.
- `.env.example` — optional config via env vars.

## Quick start
1) Create a venv and install deps:
```bash
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```
2) Put your URLs into `seeds.txt` (one per line).
3) Run scraping with a 1.2 MB corpus budget and 2s delay:
```bash
python src/scrape.py --seeds seeds.txt --out data/raw --max-total-bytes 1200000 --delay-seconds 2
```
4) Optional: enable light crawling of same‑domain links (up to 5 per seed):
```bash
python src/scrape.py --seeds seeds.txt --out data/raw --crawl --max-follow 5 --same-domain
```

## Notes
- Text is extracted with **trafilatura**. If extraction fails, raw HTML is skipped.
- Wikipedia URLs are fetched via the official REST API for clean plaintext.
- The script respects `robots.txt` and adds a custom User‑Agent. Be nice.
- You’re aiming for roughly **1 MB total** across `.txt` files for the course rubric.
