# IREN Daily Indicator - בוט מסחר אוטומטי

בוט שמנתח את מניית **IREN** (NASDAQ:IREN) פעמיים ביום ושולח התראות לטלגרם.

## 🤖 מה זה עושה?

**שני-שישי, אוטומטית:**
- **16:00 ישראל** - תחזית בוקר עם 8 פקטורים (לפני פתיחת NASDAQ)
- **23:30 ישראל** - בדיקת סגירה והשוואה לתחזית

הכל רץ ב-**GitHub Actions** - לא תלוי במחשב מקומי.

## 📊 8 הפקטורים

1. **כיוון BTC** - קורלציה למינינג ביטקוין
2. **מומנטום אתמול** - היפוכי (ירידה גדולה = bounce צפוי)
3. **RSI** - אינדקס "עייפות" של המניה
4. **ממוצעים נעים** (MA20, MA50)
5. **חדשות וסנטימנט** - ניתוח keyword של חדשות
6. **ביצועי Peers** - CRWV, NBIS, MARA, RIOT, CLSK
7. **Analyst Actions** - שינויים בהמלצות
8. **קרבת דוח רבעוני**

טווח ציון: -16 עד +16
- מעל +6 = עלייה חזקה
- מעל +3 = עלייה מתונה
- בין -2 ל-+2 = ניטרלי
- מתחת ל-3- = ירידה מתונה
- מתחת ל-6- = ירידה חזקה

## 🚀 הקמה ראשונית (חד-פעמי, 5 דקות)

### 1. יצירת רפו חדש ב-GitHub
- היכנס/י ל-[GitHub](https://github.com/new)
- שם: `iren-tracker` (או כל שם)
- בחר/י **Private** (פרטי - מומלץ)
- אל תוסיף/י README/license אוטומטית

### 2. העלאת הקבצים
**אופציה א' - דרך הדפדפן (קל יותר):**
1. ברפו החדש לחץ/י "uploading an existing file"
2. גרור/גררי את כל התוכן של תיקיית `github_deploy` (כולל תיקיית `.github` הנסתרת)
3. לחץ/י "Commit changes"

**אופציה ב' - דרך git (אם מתאים):**
```bash
cd github_deploy
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/iren-tracker.git
git push -u origin main
```

### 3. הגדרת Telegram Secrets (קריטי!)
ברפו של GitHub:
1. **Settings** → **Secrets and variables** → **Actions**
2. לחץ/י **New repository secret**, צור/י:

   | Name | Value |
   |------|-------|
   | `IREN_TELEGRAM_BOT_TOKEN` | ה-TOKEN של הבוט שלך |
   | `IREN_TELEGRAM_CHAT_ID` | ה-Chat ID שלך |

   (אלה אותם ערכים שהגדרנו ב-Windows Environment Variables)

### 4. הפעלה ראשונה (בדיקה)
1. **Actions** (בתפריט עליון של הרפו)
2. בחר/י "**IREN Morning Prediction**"
3. **Run workflow** → **Run workflow**
4. תוך 1-2 דקות תקבל/י הודעה בטלגרם 🎉

### 5. זהו! המערכת רצה
מעכשיו זה אוטומטי. כל יום ב-16:00 ו-23:30 ישראל - תקבל/י הודעות.

## 📁 מבנה הקבצים

```
.
├── .github/
│   └── workflows/
│       ├── iren_morning.yml      # תחזית 16:00
│       └── iren_evening.yml      # בדיקת סגירה 23:30
├── iren_indicator.py             # הסקריפט הראשי - תחזית
├── iren_close_check.py           # סקריפט סגירה
├── requirements.txt              # ספריות פייתון
├── LOG.md                        # יומן (מתעדכן אוטומטית)
├── predictions_data.json         # היסטוריה (מתעדכן אוטומטית)
└── README.md
```

## ⚠️ הערות חשובות

- **GitHub Actions cron אינו 100% מדויק** - יכולים להיות עיכובים של עד 15 דקות בשעות עומס
- **הרצות חינמיות** - 2000 דקות לחודש בחשבון חינמי = פי 50 ממה שצריך
- **המידע ציבורי** - אם הרפו ציבורי, ה-LOG וה-JSON ייראו לכולם. שמור/י על Private!
- **דוח רבעוני** - ביום הדוח (תוך 1-2 ימים) הסקריפט מחזיר ציון 0 = "להמנע"

## 🔧 פתרון בעיות

### לא קיבלתי הודעה אחרי Run workflow
1. בדוק/י את הלוגים: **Actions** → לחיצה על ההרצה → קרא/י את הצעדים
2. ודא/י שה-Secrets מוגדרים נכון (Settings → Secrets)
3. בדוק/י שהבוט לא חסום בטלגרם

### Action נכשל
- אם yfinance מקבל rate limit - חכה/י 30 דק' ונסה/י שוב
- אם Network error - GitHub Actions לפעמים נופל זמנית, נסה/י שוב

### לשנות שעות
ערכ/י את `.github/workflows/iren_morning.yml` או `iren_evening.yml`:
- שורת ה-cron בפורמט UTC
- 13:00 UTC = 16:00 ישראל (IDT, אפריל-אוקטובר)
- בחורף (אוק'-מרץ) Israel = UTC+2, אז 14:00 UTC = 16:00 ישראל

## 🎯 ניסוי

זה ניסוי בלבד למתודולוגיית **8 פקטורים**. **לא** המלצת השקעה.
