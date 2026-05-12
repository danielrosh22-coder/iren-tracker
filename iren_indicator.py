# -*- coding: utf-8 -*-
"""
IREN Daily Indicator - גרסה 1.1
================================
סקריפט אוטומטי שמושך נתונים, מחשב 8 פקטורים, מייצר תחזית יומית,
שומר ל-LOG ושולח לטלגרם (אופציונלי).

הרצה ידנית:
    python iren_indicator.py

הרצה אוטומטית:
    Windows Task Scheduler - ראה SETUP.md
"""

import os
import sys
import json
import io
from datetime import datetime, timedelta
from pathlib import Path

# כפיית UTF-8 בקונסול (חשוב לעברית ב-Windows)
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

try:
    import yfinance as yf
    import pandas as pd
    import requests
except ImportError as e:
    print(f"❌ חסרות ספריות. הרץ: pip install -r requirements.txt")
    print(f"   שגיאה: {e}")
    sys.exit(1)


# ============================================================
# הגדרות (אפשר לשנות)
# ============================================================

TICKER = "IREN"
PEERS = ["CRWV", "NBIS", "MARA", "RIOT", "CLSK"]
BTC_TICKER = "BTC-USD"

# נתיבים - בענן הכל באותה תיקייה
SCRIPT_DIR = Path(__file__).parent
LOG_DIR = SCRIPT_DIR  # cloud: same dir as scripts
LOG_FILE = LOG_DIR / "LOG.md"
DATA_FILE = LOG_DIR / "predictions_data.json"

# טלגרם (אופציונלי - מ-environment variables)
TELEGRAM_BOT_TOKEN = os.environ.get("IREN_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("IREN_TELEGRAM_CHAT_ID", "")


# ============================================================
# חישוב אינדיקטורים טכניים
# ============================================================

def calculate_rsi(prices, period=14):
    """חישוב RSI לפי הנוסחה הסטנדרטית."""
    delta = prices.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not rsi.empty else 50.0


def calculate_ma(prices, period):
    """ממוצע נע פשוט."""
    return float(prices.rolling(window=period).mean().iloc[-1])


def get_pct_change(hist, days_back=1):
    """אחוז שינוי מ-N ימים אחורה."""
    if len(hist) < days_back + 1:
        return 0.0
    current = hist['Close'].iloc[-1]
    previous = hist['Close'].iloc[-1 - days_back]
    return (current - previous) / previous * 100


# ============================================================
# 8 הפקטורים
# ============================================================

def factor_1_btc(btc_hist):
    """כיוון BTC ב-24 שעות אחרונות."""
    change = get_pct_change(btc_hist, 1)
    if change > 2: score = 2
    elif change > 0: score = 1
    elif change > -1: score = 0
    elif change > -2: score = -1
    else: score = -2
    return score, f"BTC שינוי: {change:+.2f}%"


def factor_2_momentum(iren_hist):
    """מומנטום מסחר אחרון - היפוכי (ירידה חזקה = bounce)."""
    change = get_pct_change(iren_hist, 1)
    if change > 3: score = -1
    elif change > 0: score = 1
    elif change > -3: score = -1
    else: score = 1  # ירידה >3% = bounce צפוי
    return score, f"מומנטום אתמול: {change:+.2f}%"


def factor_3_rsi(iren_hist):
    """RSI 14 ימים."""
    rsi = calculate_rsi(iren_hist['Close'])
    if rsi < 30: score = 2
    elif rsi < 40: score = 1
    elif rsi < 60: score = 0
    elif rsi < 70: score = -1
    else: score = -2
    return score, f"RSI: {rsi:.1f}"


def factor_4_moving_averages(iren_hist):
    """עמדה מול MA20 ו-MA50."""
    current = float(iren_hist['Close'].iloc[-1])
    ma20 = calculate_ma(iren_hist['Close'], 20)
    ma50 = calculate_ma(iren_hist['Close'], 50)
    above_20 = current > ma20
    above_50 = current > ma50

    if above_20 and above_50: score = 2
    elif above_20 or above_50: score = 1
    else: score = -2

    detail = f"מחיר {current:.2f} | MA20 {ma20:.2f} | MA50 {ma50:.2f}"
    return score, detail


def factor_5_news(ticker_obj):
    """חדשות וסנטימנט - ניתוח keyword בסיסי."""
    try:
        news = ticker_obj.news[:10]  # 10 חדשות אחרונות
    except Exception:
        return 0, "לא ניתן למשוך חדשות"

    if not news:
        return 0, "אין חדשות"

    positive_kw = ["upgrade", "beat", "strong", "record", "growth", "raise",
                   "surge", "rally", "deal", "contract", "expand", "boost"]
    negative_kw = ["downgrade", "cut", "miss", "fall", "concern", "lawsuit",
                   "drop", "decline", "warning", "halt", "delay", "loss"]

    pos_count = 0
    neg_count = 0
    for item in news:
        title = (item.get('title') or item.get('content', {}).get('title', '')).lower()
        for kw in positive_kw:
            if kw in title: pos_count += 1
        for kw in negative_kw:
            if kw in title: neg_count += 1

    net = pos_count - neg_count
    if net >= 3: score = 2
    elif net >= 1: score = 1
    elif net == 0: score = 0
    elif net >= -2: score = -1
    else: score = -2

    return score, f"חדשות: {pos_count}+ / {neg_count}- (net {net:+d})"


def factor_6_peers():
    """ביצועי peers ב-24 שעות אחרונות."""
    changes = []
    failed = []
    for peer in PEERS:
        try:
            hist = yf.Ticker(peer).history(period="5d")
            if len(hist) >= 2:
                changes.append(get_pct_change(hist, 1))
        except Exception:
            failed.append(peer)

    if not changes:
        return 0, "לא הצלחתי למשוך peers"

    avg = sum(changes) / len(changes)
    if avg > 2: score = 2
    elif avg > 0.5: score = 1
    elif avg > -0.5: score = 0
    elif avg > -2: score = -1
    else: score = -2

    detail = f"ממוצע {len(changes)} peers: {avg:+.2f}%"
    return score, detail


def factor_7_analyst(ticker_obj):
    """Analyst actions ב-3 ימים אחרונים."""
    try:
        recs = ticker_obj.recommendations
        if recs is None or recs.empty:
            return 0, "אין נתוני אנליסטים"
    except Exception:
        return 0, "לא ניתן למשוך אנליסטים"

    # yfinance מחזיר recommendations עם תקופות (0m, -1m, -2m, -3m)
    # אנחנו רוצים שינוי בחודש האחרון בלבד
    try:
        latest = recs.iloc[0] if len(recs) > 0 else None
        prev = recs.iloc[1] if len(recs) > 1 else None
        if latest is None:
            return 0, "אין נתוני אנליסטים אחרונים"

        # ספירת buy vs hold vs sell
        buys_now = int(latest.get('strongBuy', 0)) + int(latest.get('buy', 0))
        sells_now = int(latest.get('sell', 0)) + int(latest.get('strongSell', 0))
        net_now = buys_now - sells_now

        if prev is not None:
            buys_prev = int(prev.get('strongBuy', 0)) + int(prev.get('buy', 0))
            sells_prev = int(prev.get('sell', 0)) + int(prev.get('strongSell', 0))
            net_prev = buys_prev - sells_prev
            change = net_now - net_prev
        else:
            change = 0

        if change >= 2: score = 2
        elif change >= 1: score = 1
        elif change == 0: score = 0
        elif change >= -1: score = -1
        else: score = -2

        return score, f"אנליסטים: {buys_now} Buy / {sells_now} Sell (שינוי: {change:+d})"
    except Exception as e:
        return 0, f"שגיאה בנתוני אנליסטים: {e}"


def factor_8_earnings(ticker_obj):
    """קרבה לדוח רבעוני."""
    import datetime as dt_module
    try:
        cal = ticker_obj.calendar
        if cal is None:
            return 0, "אין נתוני דוח"

        # cal יכול להיות dict או DataFrame או אחר
        earnings_date = None
        if isinstance(cal, dict):
            earnings_date = cal.get('Earnings Date')
        elif hasattr(cal, 'loc'):
            try:
                earnings_date = cal.loc['Earnings Date'].iloc[0]
            except Exception:
                pass

        if earnings_date is None:
            return 0, "אין תאריך דוח"

        # אם זו רשימה - קח את הראשון
        if isinstance(earnings_date, (list, tuple)) and earnings_date:
            earnings_date = earnings_date[0]

        # המרה ל-datetime
        if isinstance(earnings_date, dt_module.date) and not isinstance(earnings_date, datetime):
            earnings_date = datetime.combine(earnings_date, dt_module.time())
        elif hasattr(earnings_date, 'to_pydatetime'):
            earnings_date = earnings_date.to_pydatetime()
        elif isinstance(earnings_date, str):
            try:
                earnings_date = datetime.fromisoformat(earnings_date)
            except Exception:
                return 0, f"לא ניתן לפרסר תאריך: {earnings_date}"

        if not isinstance(earnings_date, datetime):
            return 0, f"סוג תאריך לא צפוי: {type(earnings_date).__name__}"

        # הסרת timezone אם יש
        if earnings_date.tzinfo is not None:
            earnings_date = earnings_date.replace(tzinfo=None)

        today = datetime.now()
        days = (earnings_date - today).days

        if 0 <= days <= 2: score = 0  # יום הדוח - תנודתיות, להמנע
        elif 3 <= days <= 5: score = 1  # run-up window
        elif 6 <= days <= 7: score = 0  # מוקדם מדי
        elif -5 <= days < 0: score = 0  # אחרי דוח
        else: score = 0

        return score, f"דוח בעוד {days} ימים ({earnings_date.strftime('%Y-%m-%d')})"
    except Exception as e:
        return 0, f"שגיאה בנתוני דוח: {type(e).__name__}: {e}"


# ============================================================
# פירוש הציון
# ============================================================

def interpret_score(total):
    """פירוש הציון הכולל לתחזית."""
    if total >= 10:
        return "🟢🟢", "עלייה חזקה מאוד", "גבוהה מאוד"
    elif total >= 6:
        return "🟢", "עלייה חזקה", "גבוהה"
    elif total >= 3:
        return "🟢", "עלייה מתונה", "בינונית"
    elif total >= -2:
        return "⚪", "ניטרלי - להמנע", "נמוכה"
    elif total >= -5:
        return "🔴", "ירידה מתונה", "בינונית"
    elif total >= -9:
        return "🔴", "ירידה חזקה", "גבוהה"
    else:
        return "🔴🔴", "ירידה חזקה מאוד", "גבוהה מאוד"


# ============================================================
# ניתוח מלא
# ============================================================

def run_analysis():
    """מריץ את כל הניתוח ומחזיר dict עם התוצאות."""
    print(f"\n{'='*60}")
    print(f"  IREN Daily Indicator v1.1")
    print(f"  זמן: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    print("📡 מושך נתונים...")
    iren = yf.Ticker(TICKER)
    btc = yf.Ticker(BTC_TICKER)

    iren_hist = iren.history(period="3mo")
    btc_hist = btc.history(period="5d")

    if iren_hist.empty:
        print("❌ לא ניתן למשוך נתוני IREN")
        return None

    current_price = float(iren_hist['Close'].iloc[-1])
    print(f"✅ IREN current: ${current_price:.2f}\n")

    print("🔢 מחשב 8 פקטורים...\n")

    factors = []
    factors.append(("1. כיוון BTC",       *factor_1_btc(btc_hist)))
    factors.append(("2. מומנטום אתמול",   *factor_2_momentum(iren_hist)))
    factors.append(("3. RSI",             *factor_3_rsi(iren_hist)))
    factors.append(("4. ממוצעים נעים",    *factor_4_moving_averages(iren_hist)))
    factors.append(("5. חדשות/סנטימנט",   *factor_5_news(iren)))
    factors.append(("6. ביצועי Peers",    *factor_6_peers()))
    factors.append(("7. Analyst Actions", *factor_7_analyst(iren)))
    factors.append(("8. קרבת דוח",        *factor_8_earnings(iren)))

    total_score = sum(f[1] for f in factors)
    emoji, direction, confidence = interpret_score(total_score)

    return {
        "timestamp": datetime.now().isoformat(),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "iren_price": current_price,
        "factors": [
            {"name": f[0], "score": f[1], "detail": f[2]} for f in factors
        ],
        "total_score": total_score,
        "emoji": emoji,
        "direction": direction,
        "confidence": confidence,
    }


# ============================================================
# פורמט דוח
# ============================================================

def format_report(result):
    """דוח טקסטואלי יפה."""
    lines = []
    lines.append(f"📊 IREN Daily Indicator - {result['date']}")
    lines.append(f"💰 מחיר נוכחי: ${result['iren_price']:.2f}")
    lines.append(f"")
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    for f in result['factors']:
        sign = "+" if f['score'] > 0 else ""
        lines.append(f"  {f['name']}")
        lines.append(f"    {sign}{f['score']} | {f['detail']}")
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"")
    sign = "+" if result['total_score'] > 0 else ""
    lines.append(f"📈 ציון כולל: {sign}{result['total_score']} / 16")
    lines.append(f"{result['emoji']} תחזית: {result['direction']}")
    lines.append(f"🎯 ביטחון: {result['confidence']}")
    lines.append(f"")
    lines.append(f"⚠️ ניסוי בלבד - לא המלצת השקעה")

    return "\n".join(lines)


# ============================================================
# שמירת נתונים
# ============================================================

def save_to_json(result):
    """שמירת הנתונים לקובץ JSON היסטורי."""
    history = []
    if DATA_FILE.exists():
        try:
            history = json.loads(DATA_FILE.read_text(encoding='utf-8'))
        except Exception:
            history = []
    history.append(result)
    DATA_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False, default=str),
        encoding='utf-8'
    )
    print(f"💾 נשמר ל-{DATA_FILE.name}")


def append_to_log(result, report_text):
    """הוספה ל-LOG.md."""
    if not LOG_FILE.exists():
        return
    try:
        content = LOG_FILE.read_text(encoding='utf-8')
        new_section = f"\n\n---\n\n## עדכון אוטומטי - {result['date']} {datetime.now().strftime('%H:%M')}\n\n```\n{report_text}\n```\n"
        LOG_FILE.write_text(content + new_section, encoding='utf-8')
        print(f"📝 הוסף ל-{LOG_FILE.name}")
    except Exception as e:
        print(f"⚠️ שגיאה בכתיבה ל-LOG: {e}")


# ============================================================
# טלגרם
# ============================================================

def send_telegram(text):
    """שליחה לטלגרם (אופציונלי)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("ℹ️  טלגרם לא מוגדר (סט IREN_TELEGRAM_BOT_TOKEN ו-IREN_TELEGRAM_CHAT_ID)")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        r = requests.post(url, data=data, timeout=10)
        if r.status_code == 200:
            print("📲 נשלח לטלגרם")
            return True
        else:
            print(f"❌ שגיאת טלגרם: {r.status_code}")
            return False
    except Exception as e:
        print(f"❌ שגיאת טלגרם: {e}")
        return False


# ============================================================
# main
# ============================================================

def main():
    result = run_analysis()
    if result is None:
        sys.exit(1)

    report = format_report(result)
    print("\n" + report + "\n")

    save_to_json(result)
    append_to_log(result, report)
    send_telegram(report)


if __name__ == "__main__":
    main()
