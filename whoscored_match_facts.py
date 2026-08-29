#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WhoScored Match Facts - ריכוז "Match Facts" של כל משחקי היום.

עובר על רשימת המשחקים של תאריך מסוים ב-WhoScored, נכנס לעמוד ה-Preview של כל
משחק, שולף את מקטע "Match Facts" (וגם רצפים/Streaks אם קיימים), ומרכז הכל
לדוח אחד: טקסט, Markdown או JSON. אופציונלית שולח לטלגרם.

דוגמאות:
    python whoscored_match_facts.py
    python whoscored_match_facts.py --date 2026-08-29 --leagues "premier-league,laliga"
    python whoscored_match_facts.py --format json --out facts.json
    python whoscored_match_facts.py --telegram

דרישות:
    pip install -r requirements-football.txt
    python -m playwright install chromium

הערה: WhoScored מוגן ב-Incapsula ומצריך דפדפן אמיתי, לכן הכלי מבוסס Playwright
ולא בקשות HTTP פשוטות. הרצה משרתי ענן (כולל GitHub Actions) עלולה להיחסם -
במקרה כזה הריצו מקומית.
"""

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime, date

BASE = "https://www.whoscored.com"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

TELEGRAM_BOT_TOKEN = os.environ.get("IREN_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("IREN_TELEGRAM_CHAT_ID", "")

# כותרות המקטעים בעמוד ה-Preview. משמשות לחיתוך הטקסט בין כותרת לכותרת.
SECTION_HEADERS = [
    "match facts",
    "streaks",
    "tables",
    "team characters",
    "team characteristics",
    "previous meetings",
    "previous 6 meetings",
    "head to head",
    "strengths",
    "weaknesses",
    "styles of play",
    "team statistics",
    "form",
    "standings",
    "match preview",
    "key facts",
]

# ביטויים שמזהים משפט-עובדה גם אם הכותרת בעמוד השתנתה (fallback).
FACT_PATTERNS = [
    r"\bfailed to win\b", r"\bfailed to score\b", r"\bhave won\b", r"\bhave lost\b",
    r"\bhave drawn\b", r"\bhave not\b", r"\bhaven't\b", r"\bwinless\b", r"\bunbeaten\b",
    r"\bclean sheet", r"\bscored in\b", r"\bconceded\b", r"\bin a row\b",
    r"\blast \d+\b", r"\bconsecutive\b", r"\bhome matches\b", r"\baway matches\b",
]
FACT_RE = re.compile("|".join(FACT_PATTERNS), re.I)


# ---------------------------------------------------------------- utilities

def log(msg):
    print(msg, flush=True)


def clean(text):
    """מנקה רווחים כפולים ורווחים מיוחדים שמגיעים מה-DOM."""
    return re.sub(r"\s+", " ", (text or "").replace(" ", " ")).strip()


def parse_date(value):
    if not value:
        return date.today()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise SystemExit(f"תאריך לא תקין: {value} (פורמט: YYYY-MM-DD)")


def preview_url(href):
    """הופך קישור משחק (live/show/preview) לקישור עמוד ה-Preview."""
    if not href:
        return None
    if href.startswith("/"):
        href = BASE + href
    return re.sub(r"/matches/(\d+)/(live|show|preview|matchreport)\b",
                  r"/matches/\1/preview", href, count=1)


def match_id_of(href):
    m = re.search(r"/matches/(\d+)", href or "")
    return m.group(1) if m else None


# ------------------------------------------------------- fixtures discovery

FIXTURES_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  const anchors = document.querySelectorAll('a[href*="/matches/"]');
  for (const a of anchors) {
    const href = a.getAttribute('href') || '';
    const m = href.match(/\/matches\/(\d+)\//);
    if (!m || seen.has(m[1])) continue;
    seen.add(m[1]);

    // שם התחרות מתוך ה-slug: /matches/123/live/england-premier-league-2026-2027-a-b
    const slug = href.split('/').slice(4).join('/') || '';

    // מטפסים למעלה עד שורת המשחק שמכילה גם שעת פתיחה
    const TIME = /(?:^|[^\d:])([0-2]?\d:[0-5]\d)(?!\d)/;
    let row = a, rowText = (a.innerText || '').trim(), hops = 0;
    while (row && hops < 4) {
      const t = (row.innerText || '').trim();
      if (t.length > 400) break;
      rowText = t;
      if (TIME.test(t) && t.length >= 12) break;
      row = row.parentElement; hops++;
    }
    const timeMatch = rowText.match(TIME);

    out.push({
      match_id: m[1],
      href: href,
      slug: slug,
      row_text: rowText.split('\n').map(s => s.trim()).filter(Boolean).slice(0, 6).join(' | '),
      kickoff: timeMatch ? timeMatch[1] : '',
    });
  }
  return out;
}
"""


def slug_to_competition(slug):
    """england-premier-league-2026-2027-liverpool-nottingham-forest -> England Premier League."""
    if not slug:
        return "Unknown"
    part = slug.split("/")[-1]
    part = re.sub(r"-\d{4}-\d{4}.*$", "", part)  # חותך מהעונה והלאה
    part = re.sub(r"-\d{4}.*$", "", part)
    return " ".join(w.capitalize() for w in part.split("-")) or "Unknown"


def slug_to_teams(slug):
    """מנסה לחלץ 'Liverpool vs Nottingham Forest' מתוך ה-slug (best effort)."""
    if not slug:
        return ""
    part = slug.split("/")[-1]
    m = re.search(r"-\d{4}-\d{4}-(.+)$", part) or re.search(r"-\d{4}-(.+)$", part)
    if not m:
        return ""
    return " ".join(w.capitalize() for w in m.group(1).split("-"))


def diagnose_page(page, response, dump_dir=None, tag="page"):
    """מדפיס למה עמוד לא הניב תוצאות, ושומר את ה-HTML לבדיקה."""
    status = response.status if response else "?"
    try:
        title = page.title()
    except Exception:
        title = "?"
    try:
        body = clean(page.inner_text("body"))
    except Exception:
        body = ""
    log(f"   ↳ status={status} title={title!r}")
    if body:
        log(f"   ↳ טקסט העמוד: {body[:600]}")
    else:
        log("   ↳ העמוד ריק מטקסט (כנראה challenge של Incapsula)")
    if dump_dir:
        os.makedirs(dump_dir, exist_ok=True)
        try:
            with open(os.path.join(dump_dir, f"{tag}.html"), "w", encoding="utf-8") as fh:
                fh.write(page.content())
            log(f"   ↳ נשמר HTML מלא: {dump_dir}/{tag}.html")
        except Exception as exc:
            log(f"   ↳ שמירת HTML נכשלה: {exc}")


def collect_fixtures(page, day, verbose=False, dump_dir=None):
    """טוען את עמוד המשחקים של התאריך ומחזיר רשימת משחקים."""
    urls = [
        f"{BASE}/livescores?d={day:%Y%m%d}",
        f"{BASE}/matches?d={day:%Y%m%d}",
        f"{BASE}/livescores",
    ]
    for idx, url in enumerate(urls, 1):
        log(f"📅 טוען רשימת משחקים: {url}")
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            log(f"   ⚠️  טעינה נכשלה: {exc}")
            continue
        try:
            page.wait_for_selector('a[href*="/matches/"]', timeout=25000)
        except Exception:
            log("   ⚠️  לא נמצאו קישורי משחקים בעמוד (ייתכן חסימת בוט)")
            diagnose_page(page, response, dump_dir, tag=f"fixtures-{idx}")
            continue
        page.wait_for_timeout(2500)
        raw = page.evaluate(FIXTURES_JS)
        fixtures = []
        for item in raw:
            fixtures.append({
                "match_id": item["match_id"],
                "url": preview_url(item["href"]),
                "competition": slug_to_competition(item["slug"]),
                "teams": slug_to_teams(item["slug"]),
                "kickoff": item.get("kickoff", ""),
                "row_text": item.get("row_text", ""),
                "slug": item["slug"],
            })
        if fixtures:
            log(f"   ✅ נמצאו {len(fixtures)} משחקים")
            return fixtures
    return []


# ---------------------------------------------------------- facts extraction

FACTS_PANEL_JS = r"""
() => {
  const PAG = /(?:^|\s)(\d+)\s+of\s+(\d+)(?:\s|$)/;
  const all = Array.from(document.querySelectorAll('*'));
  const header = all.find(e => e.children.length === 0 &&
                               e.textContent.trim().toLowerCase() === 'match facts');
  if (!header) return null;

  // מטפסים מהכותרת עד האלמנט שמכיל גם את מונה העמודים ("3 of 4")
  let panel = header.parentElement, m = null;
  for (let i = 0; i < 8 && panel; i++) {
    m = (panel.innerText || '').match(PAG);
    if (m) break;
    panel = panel.parentElement;
  }
  if (!panel) panel = header.parentElement;
  if (!panel) return null;

  document.querySelectorAll('[data-ws-next]').forEach(e => e.removeAttribute('data-ws-next'));

  let hasNext = false;
  if (m) {
    // כפתור "הבא" = הלחיץ הראשון מימין למונה העמודים
    const pagEl = Array.from(panel.querySelectorAll('*')).filter(e =>
      e.children.length === 0 && PAG.test(e.textContent.trim()) &&
      e.textContent.trim().length < 14).pop();
    if (pagEl) {
      const box = pagEl.getBoundingClientRect();
      const right = Array.from(panel.querySelectorAll('button,a,[role="button"]'))
        .map(e => ({ e: e, b: e.getBoundingClientRect() }))
        .filter(o => o.b.width > 0 && o.b.left >= box.right - 2)
        .sort((x, y) => x.b.left - y.b.left);
      if (right.length) { right[0].e.setAttribute('data-ws-next', '1'); hasNext = true; }
    }
  }
  return { text: panel.innerText || '', page: m ? +m[1] : 1, total: m ? +m[2] : 1,
           hasNext: hasNext };
}
"""

ODDS_RE = re.compile(r"^(\d+/\d+|\d+(?:\.\d+)?|EVS)$", re.I)
PAGINATION_RE = re.compile(r"^\d+\s+of\s+\d+$", re.I)
# חצי הניווט של הקרוסלה (« ‹ › ») - שורות בלי אות או ספרה
GLYPH_RE = re.compile(r"^[^0-9A-Za-z\u0590-\u05FF]+$")


def parse_fact_cards(text):
    """מפרק את טקסט הפאנל לכרטיסים: משפט-עובדה + ההימור והיחס שצמודים אליו."""
    cards = []
    current = None
    for raw in (text or "").split("\n"):
        line = clean(raw)
        if not line or PAGINATION_RE.match(line) or GLYPH_RE.match(line):
            continue
        if line.lower() in ("match facts", "offers", "top players"):
            continue
        # משפט-עובדה: שורה ארוכה, לרוב מסתיימת בשם התחרות בסוגריים
        if len(line) >= 45:
            current = {"fact": line, "details": []}
            cards.append(current)
        elif current is not None and len(current["details"]) < 4:
            current["details"].append(line)

    for card in cards:
        details = card.pop("details")
        odds = ""
        if details and ODDS_RE.match(details[-1]):
            odds = details.pop()
        card["bet"] = " · ".join(details)
        card["odds"] = odds
    return cards

SECTIONS_JS = r"""
(headers) => {
  const text = document.body.innerText || '';
  const lines = text.split('\n').map(s => s.replace(/ /g, ' ').trim());
  const isHeader = (l) => headers.includes(l.toLowerCase());
  const sections = {};
  let current = null;
  for (const line of lines) {
    if (!line) continue;
    if (isHeader(line)) {
      current = line.toLowerCase();
      if (!sections[current]) sections[current] = [];
      continue;
    }
    if (current) sections[current].push(line);
  }
  return { sections: sections, all_lines: lines.filter(Boolean) };
}
"""

# רעש שלא רוצים בתוך המקטעים
NOISE_RE = re.compile(
    r"^(home|fixtures|news|statistics|live scores|teams|players|forum|bet|odds|"
    r"gambleaware|18\+|advertisement|sign in|register|cookie|accept|summary|preview|"
    r"match history|standings|form)$",
    re.I,
)


def is_fact_line(line, teams_hint=""):
    if len(line) < 20 or len(line) > 300:
        return False
    if NOISE_RE.match(line):
        return False
    if line.count("|") > 1:
        return False
    if FACT_RE.search(line):
        return True
    # משפט שמזכיר אחת מהקבוצות ונראה כמו משפט שלם
    tokens = [t for t in re.split(r"[\s]+", teams_hint) if len(t) > 3]
    return bool(tokens) and any(t.lower() in line.lower() for t in tokens) and line.endswith(".")


def extract_facts(page, url, teams_hint="", verbose=False, dump_dir=None, max_pages=12):
    """נכנס לעמוד ה-Preview ואוסף את כל עמודי הקרוסלה של Match Facts."""
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    try:
        page.wait_for_selector("text=Match Facts", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(1000)

    cards, seen = [], set()
    total_pages, pages_read = 1, 0

    for _ in range(max_pages):
        panel = page.evaluate(FACTS_PANEL_JS)
        if not panel:
            break
        total_pages = panel.get("total", 1) or 1
        pages_read += 1
        for card in parse_fact_cards(panel.get("text", "")):
            if card["fact"] not in seen:
                seen.add(card["fact"])
                cards.append(card)
        if panel.get("page", 1) >= total_pages or not panel.get("hasNext"):
            break
        before = panel.get("page", 1)
        try:
            page.click("[data-ws-next]", timeout=5000)
        except Exception as exc:
            if verbose:
                log(f"   ↳ מעבר לעמוד הבא נכשל: {exc}")
            break
        # ממתינים שמונה העמודים יתקדם בפועל
        moved = False
        for _ in range(20):
            page.wait_for_timeout(250)
            probe = page.evaluate(FACTS_PANEL_JS)
            if probe and probe.get("page", before) != before:
                moved = True
                break
        if not moved:
            break

    facts = [c["fact"] for c in cards]

    # גיבוי: מבנה עמוד אחר (למשל הגרסה הישנה) - חיתוך לפי כותרות מקטעים
    data = page.evaluate(SECTIONS_JS, SECTION_HEADERS)
    sections = data.get("sections", {})
    all_lines = data.get("all_lines", [])
    if not facts:
        for key in ("match facts", "key facts", "match preview"):
            for line in sections.get(key, []):
                line = clean(line)
                if line and not NOISE_RE.match(line) and len(line) >= 15:
                    facts.append(line)
            if facts:
                break
    used_fallback = False
    if not facts:
        used_fallback = True
        for line in all_lines:
            line = clean(line)
            if is_fact_line(line, teams_hint) and line not in facts:
                facts.append(line)
        facts = facts[:8]
        cards = [{"fact": f, "bet": "", "odds": ""} for f in facts]

    streaks = [clean(l) for l in sections.get("streaks", []) if clean(l)]

    if dump_dir and not facts:
        os.makedirs(dump_dir, exist_ok=True)
        path = os.path.join(dump_dir, f"{match_id_of(url) or 'page'}.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(all_lines))
        if verbose:
            log(f"   🐛 לא נמצאו עובדות - נשמר dump: {path}")

    return {"facts": facts, "cards": cards, "streaks": streaks,
            "fallback": used_fallback, "pages": pages_read, "pages_total": total_pages}


# ------------------------------------------------------------------ scraping

def scrape(day, leagues=None, limit=0, headful=False, verbose=False,
           delay=(2.0, 4.0), dump_dir=None, timeout_per_match=60):
    from playwright.sync_api import sync_playwright

    results = []
    # מאפשר להצביע על בינארי כרום קיים במקום להוריד דפדפן (למשל בסביבות CI)
    launch_kwargs = {
        "headless": not headful,
        "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    }
    chrome_path = os.environ.get("WS_CHROME_PATH", "")
    if chrome_path:
        launch_kwargs["executable_path"] = chrome_path

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            user_agent=DEFAULT_UA,
            viewport={"width": 1440, "height": 900},
            locale="en-GB",
            timezone_id="Asia/Jerusalem",
        )
        # מסתיר את הדגל שמסגיר אוטומציה
        context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        page = context.new_page()
        page.set_default_timeout(timeout_per_match * 1000)

        try:
            fixtures = collect_fixtures(page, day, verbose=verbose, dump_dir=dump_dir)
            if not fixtures:
                log("❌ לא נמצאו משחקים. ייתכן שהאתר חסם את הבקשה - נסו --headful.")
                return []

            if leagues:
                wanted = [w.strip().lower() for w in leagues if w.strip()]
                fixtures = [
                    f for f in fixtures
                    if any(w in (f["slug"] + " " + f["competition"]).lower() for w in wanted)
                ]
                log(f"🔎 אחרי סינון ליגות: {len(fixtures)} משחקים")

            if limit:
                fixtures = fixtures[:limit]

            for i, fx in enumerate(fixtures, 1):
                label = fx["teams"] or fx["row_text"] or fx["match_id"]
                log(f"[{i}/{len(fixtures)}] {fx['competition']} · {label}")
                try:
                    got = extract_facts(page, fx["url"], teams_hint=fx["teams"],
                                        verbose=verbose, dump_dir=dump_dir)
                except Exception as exc:
                    log(f"   ⚠️  שגיאה: {exc}")
                    got = {"facts": [], "cards": [], "streaks": [],
                           "fallback": False, "error": str(exc)}
                fx.update(got)
                results.append(fx)
                if got.get("facts"):
                    pages = got.get("pages_total", 1)
                    log(f"   ✅ {len(got['facts'])} עובדות ({pages} עמודי קרוסלה)")
                else:
                    log("   ➖ אין Match Facts")
                if i < len(fixtures):
                    time.sleep(random.uniform(*delay))
        finally:
            context.close()
            browser.close()
    return results


# ------------------------------------------------------------------- output

def group_by_competition(results):
    grouped = {}
    for r in results:
        grouped.setdefault(r["competition"], []).append(r)
    return dict(sorted(grouped.items()))


def render_text(results, day, include_streaks=False, only_with_facts=True):
    lines = [f"⚽ Match Facts · {day:%d/%m/%Y}", "=" * 40, ""]
    total = 0
    for comp, matches in group_by_competition(results).items():
        shown = [m for m in matches if m.get("facts")] if only_with_facts else matches
        if not shown:
            continue
        lines.append(f"🏆 {comp}")
        for m in shown:
            title = m["teams"] or m["row_text"] or f"Match {m['match_id']}"
            ko = f" ({m['kickoff']})" if m.get("kickoff") else ""
            lines.append(f"  • {title}{ko}")
            for card in m.get("cards") or [{"fact": f, "bet": "", "odds": ""}
                                           for f in m.get("facts", [])]:
                lines.append(f"      - {card['fact']}")
                if card.get("bet") or card.get("odds"):
                    lines.append(f"        ↳ {card.get('bet', '')} @ {card.get('odds', '-')}")
                total += 1
            if include_streaks and m.get("streaks"):
                lines.append(f"      רצפים: {' | '.join(m['streaks'][:6])}")
            lines.append(f"      {m['url']}")
        lines.append("")
    lines.append(f"סה\"כ {total} עובדות מתוך {len(results)} משחקים.")
    return "\n".join(lines)


def render_markdown(results, day, include_streaks=False, only_with_facts=True):
    lines = [f"# Match Facts — {day:%Y-%m-%d}", ""]
    for comp, matches in group_by_competition(results).items():
        shown = [m for m in matches if m.get("facts")] if only_with_facts else matches
        if not shown:
            continue
        lines.append(f"## {comp}")
        for m in shown:
            title = m["teams"] or m["row_text"] or f"Match {m['match_id']}"
            ko = f" — {m['kickoff']}" if m.get("kickoff") else ""
            lines.append(f"### [{title}]({m['url']}){ko}")
            for card in m.get("cards") or [{"fact": f, "bet": "", "odds": ""}
                                           for f in m.get("facts", [])]:
                bet = f" — **{card['bet']} @ {card['odds']}**" if card.get("odds") else ""
                lines.append(f"- {card['fact']}{bet}")
            if include_streaks and m.get("streaks"):
                lines.append(f"- _Streaks:_ {' | '.join(m['streaks'][:6])}")
            lines.append("")
    return "\n".join(lines)


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("ℹ️  טלגרם לא מוגדר (IREN_TELEGRAM_BOT_TOKEN / IREN_TELEGRAM_CHAT_ID)")
        return False
    import requests
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    ok = True
    # טלגרם מוגבל ל-4096 תווים להודעה
    chunks, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > 3800:
            chunks.append(buf)
            buf = ""
        buf += line + "\n"
    if buf.strip():
        chunks.append(buf)
    for chunk in chunks:
        try:
            r = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": chunk}, timeout=15)
            if r.status_code != 200:
                log(f"⚠️  טלגרם החזיר {r.status_code}: {r.text[:200]}")
                ok = False
        except Exception as exc:
            log(f"⚠️  שליחת טלגרם נכשלה: {exc}")
            ok = False
        time.sleep(0.5)
    return ok


def main():
    ap = argparse.ArgumentParser(description="ריכוז Match Facts מ-WhoScored לכל משחקי היום")
    ap.add_argument("--date", help="תאריך YYYY-MM-DD (ברירת מחדל: היום)")
    ap.add_argument("--leagues", default="",
                    help="סינון לפי מילים בשם הליגה, מופרד בפסיקים (למשל: premier-league,laliga)")
    ap.add_argument("--limit", type=int, default=0, help="מקסימום משחקים (0 = הכל)")
    ap.add_argument("--format", choices=["text", "markdown", "json"], default="text")
    ap.add_argument("--out", help="קובץ פלט (ברירת מחדל: הדפסה למסך בלבד)")
    ap.add_argument("--json-out", help="לשמור בנוסף גם JSON גולמי לנתיב הזה")
    ap.add_argument("--telegram", action="store_true", help="לשלוח את הדוח לטלגרם")
    ap.add_argument("--headful", action="store_true", help="דפדפן גלוי (עוזר מול חסימות בוט)")
    ap.add_argument("--include-streaks", action="store_true", help="לצרף גם מקטע Streaks")
    ap.add_argument("--all-matches", action="store_true", help="להציג גם משחקים בלי עובדות")
    ap.add_argument("--dump-dir", help="תיקייה לשמירת טקסט עמודים שנכשלו (דיבאג)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    day = parse_date(args.date)
    leagues = args.leagues.split(",") if args.leagues else None

    results = scrape(
        day,
        leagues=leagues,
        limit=args.limit,
        headful=args.headful,
        verbose=args.verbose,
        dump_dir=args.dump_dir,
    )
    if not results:
        return 1

    only_with_facts = not args.all_matches
    if args.format == "json":
        payload = {"date": day.isoformat(), "generated_at": datetime.now().isoformat(),
                   "matches": results}
        output = json.dumps(payload, ensure_ascii=False, indent=2)
    elif args.format == "markdown":
        output = render_markdown(results, day, args.include_streaks, only_with_facts)
    else:
        output = render_text(results, day, args.include_streaks, only_with_facts)

    print()
    print(output)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(output)
        log(f"\n💾 נשמר: {args.out}")

    if args.json_out:
        payload = {"date": day.isoformat(), "generated_at": datetime.now().isoformat(),
                   "matches": results}
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        log(f"💾 נשמר: {args.json_out}")

    if args.telegram:
        text = output if args.format != "json" else render_text(
            results, day, args.include_streaks, only_with_facts)
        send_telegram(text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
