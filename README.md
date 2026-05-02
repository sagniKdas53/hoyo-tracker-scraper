# Hoyo Tracker Scraper

This folder contains a Python scraper for HoYoverse game codes and calendars.

It is intended to live as its own standalone repository.

## What it does

The scraper collects:

- Genshin Impact codes, events, banners, and challenges
- Honkai: Star Rail codes, events, banners, and challenges

It uses:

- `api.ennead.cc` as the primary source for codes and calendar data
- `crimsonwitch.com` as a secondary source to enrich and backfill code metadata

The script normalizes the source data into a stable schema and writes machine-
readable plus human-readable artifacts to `output/`.

## Files

- `scrape_hoyo_tracker.py`: main scraper
- `test_scrape_hoyo_tracker.py`: stdlib unit tests for key parsing/merge logic
- `output/latest.json`: canonical combined output for downstream use
- `output/latest_all.json`: most recent unfiltered run
- `output/latest_active_only.json`: most recent active-only run
- `output/codes.csv`: flattened code rows
- `output/events.csv`: flattened event rows
- `output/banners.csv`: flattened banner rows
- `output/challenges.csv`: flattened challenge rows
- `output/provenance.json`: extraction metadata and caveats
- `output/summary.md`: quick human-readable summary

## Usage

```bash
cd /home/sagnik/Projects/games/hoyo-tracker-scraper
python3 scrape_hoyo_tracker.py
```

Exclude expired items:

```bash
python3 scrape_hoyo_tracker.py --active-only
```

Keep only code records and emit timestamps in a different timezone:

```bash
python3 scrape_hoyo_tracker.py --include codes --timezone Asia/Kolkata
```

Limit to one game:

```bash
python3 scrape_hoyo_tracker.py --games genshin
```

The same options can be supplied through environment variables:

```bash
export HOYO_TRACKER_GAMES=genshin,starrail
export HOYO_TRACKER_TIMEZONE=Asia/Kolkata
export HOYO_TRACKER_INCLUDE=all
export HOYO_TRACKER_ACTIVE_ONLY=true
python3 scrape_hoyo_tracker.py
```

Run tests:

```bash
python3 -m unittest test_scrape_hoyo_tracker.py
```

## Output contract

Future agents should prefer:

- `output/latest_active_only.json` for clean current tracking
- `output/latest_all.json` for the full source snapshot

`output/latest.json` always points to the most recent run, regardless of mode.

Top-level shape:

```json
{
  "scraped_at_utc": "ISO-8601 timestamp",
  "sources": {},
  "filters": {
    "games": ["genshin", "starrail"],
    "include": "all",
    "active_only": false,
    "timezone": "UTC"
  },
  "counts": {
    "codes": 0,
    "events": 0,
    "banners": 0,
    "challenges": 0,
    "total": 0
  },
  "unfiltered_counts": {
    "codes": 0,
    "events": 0,
    "banners": 0,
    "challenges": 0,
    "total": 0
  },
  "games": {
    "genshin": {
      "codes": [],
      "events": [],
      "banners": [],
      "challenges": []
    }
  }
}
```

## Notes

- `api.ennead.cc` and `crimsonwitch.com` are unofficial sources.
- The Crimson Witch code page is a Next.js app whose `initialCodes` payload is
  parsed from embedded `self.__next_f.push(...)` content.
- Genshin redemption links use the English redeem page:
  `https://genshin.hoyoverse.com/en/gift?code=...`
- Honkai: Star Rail redemption links use:
  `https://hsr.hoyoverse.com/gift?code=...`
