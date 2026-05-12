# -*- coding: utf-8 -*-
"""
IREN Close Check - בדיקת סגירה ערב
====================================
רץ כל יום ב-23:30 ישראל (אחרי סגירת NASDAQ).
- מושך את מחיר הסגירה היומי בפועל
- משווה לתחזית של הבוקר
- שולח דוח לטלגרם: צדקנו / טעינו
- מעדכן את ה-JSON וה-LOG עם התוצאה
"""

import os
import sys
import io
import json
from datetime import datetime
from pathlib import Path

try:
    import yfinance as yf
    import requests
except ImportError:
    print("חסרות ספריות: pip install yfinance requests")
    sys.exit(1)

# UTF-8 ב-Windows
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# הגדרות
TICKER = "IREN"
SCRIPT_DIR = Path(__file__).parent
LOG_DIR = SCRIPT_DIR  # cloud: same dir
LOG_FILE = LOG_DIR / "LOG.md"
DATA_FILE = LOG_DIR / "predictions_data.json"

TELEGRAM_BOT_TOKEN = os.environ.get("IREN_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("IREN_TELEGRAM_CHAT_ID", "")


def get_today_close():
    """משוך מחיר סגירה של היום."""
    iren = yf.Ticker(TICKER)
    hist = iren.history(period="5d")
    if hist.empty:
        return None, None
    today = hist.iloc[-1]
    yesterday = hist.iloc[-2] if len(hist) >= 2 else None

    close = float(today['Close'])
    prev_close = float(yesterday['Close']) if yesterday is not None else close
    change_pct = (close - prev_close) / prev_close * 100 if prev_close else 0
    return close, change_pct


def get_today_prediction():
    """משוך את התחזית של הבוקר מקובץ ה-JSON."""
    if not DATA_FILE.exists():
        return None
    try:
        history = json.loads(DATA_FILE.read_text(encoding='utf-8'))
        today = datetime.now().strftime("%Y-%m-%d")
        # חיפוש התחזית של היום (האחרונה אם יש כמה)
        today_predictions = [p for p in history if p.get('date') == today and 'actual_close' not in p]
        if today_predictions:
            return today_predictions[-1]
        return None
    except Exception as e:
        print(f"שגיאה בקריאת היסטוריה: {e}")
        return None


def evaluate(prediction, actual_change_pct):
    """האם צדקנו? לפי כיוון התחזית."""
    score = prediction['total_score']
    direction = prediction['direction']

    # כיוון התחזית
    if score >= 3:
        predicted_dir = "up"
    elif score <= -3:
        predicted_dir = "down"
    else:
        predicted_dir = "neutral"

    # כיוון בפועל
    if actual_change_pct > 0.5:
        actual_dir = "up"
    elif actual_change_pct < -0.5:
        actual_dir = "down"
    else:
        actual_dir = "neutral"

    # התאמה
    if predicted_dir == "neutral":
        # תחזית ניטרלית - לא נספור הצלחה/כישלון
        verdict = "ניטרלי"
        emoji = "⚪"
    elif predicted_dir == actual_dir:
        verdict = "צדקנו!"
        emoji = "✅"
    else:
        verdict = "טעינו"
        emoji = "❌"

    return verdict, emoji, predicted_dir, actual_dir


def update_json(prediction, close, change_pct, verdict):
    """עדכון רשומת התחזית ב-JSON עם התוצאה בפועל."""
    if not DATA_FILE.exists():
        return
    try:
        history = json.loads(DATA_FILE.read_text(encoding='utf-8'))
        for record in history:
            if record.get('timestamp') == prediction.get('timestamp'):
                record['actual_close'] = close
                record['actual_change_pct'] = change_pct
                record['verdict'] = verdict
                record['close_check_time'] = datetime.now().isoformat()
                break
        DATA_FILE.write_text(
            json.dumps(history, indent=2, ensure_ascii=False, default=str),
            encoding='utf-8'
        )
        print(f"💾 עודכן {DATA_FILE.name}")
    except Exception as e:
        print(f"שגיאה בעדכון JSON: {e}")


def append_to_log(report):
    """הוספת תוצאה ל-LOG."""
    if not LOG_FILE.exists():
        return
    try:
        content = LOG_FILE.read_text(encoding='utf-8')
        new_section = f"\n\n## 🌙 בדיקת סגירה - {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n```\n{report}\n```\n"
        LOG_FILE.write_text(content + new_section, encoding='utf-8')
        print(f"📝 הוסף ל-{LOG_FILE.name}")
    except Exception as e:
        print(f"שגיאה ב-LOG: {e}")


def send_telegram(text):
    """שליחה לטלגרם."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("ℹ️ טלגרם לא מוגדר")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10
        )
        if r.status_code == 200:
            print("📲 נשלח לטלגרם")
            return True
        else:
            print(f"שגיאה: {r.status_code}")
    except Exception as e:
        print(f"שגיאה: {e}")
    return False


def main():
    print(f"\n{'='*60}")
    print(f"  IREN Close Check - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    # 1. מחיר סגירה
    close, change_pct = get_today_close()
    if close is None:
        print("❌ לא ניתן למשוך מחיר")
        sys.exit(1)
    print(f"💰 סגירה: ${close:.2f} ({change_pct:+.2f}%)")

    # 2. תחזית הבוקר
    prediction = get_today_prediction()
    if prediction is None:
        msg = f"""🌙 IREN Close Check - {datetime.now().strftime('%Y-%m-%d')}

💰 סגירה: ${close:.2f} ({change_pct:+.2f}%)

⚠️ אין תחזית בוקר להיום ב-JSON.
ייתכן שהסקריפט של הבוקר לא רץ?"""
        print(msg)
        send_telegram(msg)
        sys.exit(0)

    # 3. הערכה
    verdict, emoji, pred_dir, actual_dir = evaluate(prediction, change_pct)
    print(f"{emoji} {verdict}")

    # 4. דוח
    score = prediction['total_score']
    sign = "+" if score > 0 else ""
    direction_he = {"up": "עלייה", "down": "ירידה", "neutral": "ניטרלי"}

    report = f"""🌙 IREN סיכום סגירה - {datetime.now().strftime('%Y-%m-%d')}

💰 סגירה היום: ${close:.2f}
📊 שינוי: {change_pct:+.2f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📋 התחזית מהבוקר:
   • ציון: {sign}{score} / 16
   • כיוון: {direction_he.get(pred_dir, pred_dir)}
   • ביטחון: {prediction.get('confidence', '?')}

📈 בפועל:
   • כיוון: {direction_he.get(actual_dir, actual_dir)}
   • שינוי: {change_pct:+.2f}%

{emoji} תוצאה: {verdict}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️ ניסוי בלבד - לא המלצת השקעה"""

    print("\n" + report + "\n")

    # 5. שמירה ושליחה
    update_json(prediction, close, change_pct, verdict)
    append_to_log(report)
    send_telegram(report)


if __name__ == "__main__":
    main()
