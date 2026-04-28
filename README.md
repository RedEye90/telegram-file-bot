# 🤖 Telegram File Bot — Deploy Guide

## Yeh bot kya karta hai?
- **Tu** (admin) APK/ZIP files upload karta hai
- **Users** /start ya /list se files dekh ke download karte hain
- **24/7** Railway.app pe free mein chalta hai

---

## Step 1 — BotFather se token lo

1. Telegram pe `@BotFather` open karo
2. `/newbot` bhejo
3. Naam do (jaise: `MyFilesBot`)
4. Username do (jaise: `myfiles_bot`) — end mein `bot` hona chahiye
5. **Token copy karo** — kuch aisa dikhega:
   ```
   1234567890:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

---

## Step 2 — Apna Telegram User ID pata karo

1. `@userinfobot` pe `/start` bhejo
2. **Id** wali number copy karo (jaise: `987654321`)
   — Yahi tera `ADMIN_ID` hai

---

## Step 3 — GitHub pe code daalo

```bash
git init
git add .
git commit -m "first commit"
```

GitHub pe naya repo banao → phir:
```bash
git remote add origin https://github.com/TERA_USERNAME/TERA_REPO.git
git push -u origin main
```

---

## Step 4 — Railway pe deploy karo (FREE)

1. [railway.app](https://railway.app) pe signup karo (GitHub se)
2. **New Project** → **Deploy from GitHub repo**
3. Apna repo select karo
4. **Variables** tab mein jaao aur yeh add karo:

   | Key | Value |
   |-----|-------|
   | `BOT_TOKEN` | (BotFather se mila token) |
   | `ADMIN_ID`  | (tera Telegram user ID number) |

5. **Deploy** karo — bas! 🎉

Railway automatically `requirements.txt` install karega aur bot start karega.

---

## Bot Commands

### Users ke liye:
| Command | Kya karta hai |
|---------|---------------|
| `/start` | Files ki list + download buttons |
| `/list`  | Same — files dekhne ke liye |
| `/help`  | Help message |

### Admin (sirf tere liye):
| Command | Kya karta hai |
|---------|---------------|
| File bhejo | Bot store kar leta hai automatically |
| `/delete <file_id>` | Koi file hatao |
| `/list` | Sab stored files dekho |

---

## File Upload kaise karein (Admin)

1. Bot ko Telegram pe open karo
2. Seedha APK ya ZIP file bhejo — koi command nahi chahiye
3. Bot confirm karega: ✅ file store ho gayi
4. Ab koi bhi user `/list` se download kar sakta hai

---

## ⚠️ Important Notes

- **File_id system** — Bot Telegram ke servers pe files store karta hai, apne paas nahi. Isliye Railway restart pe bhi files safe rehti hain **jab tak bot wahi file_id use kare**. Lekin restart pe in-memory store clear ho jaata hai — production mein SQLite ya database use karo (next upgrade).
- **File size limit** — Telegram bots 50MB tak files receive kar sakte hain, 50MB tak bhej sakte hain.
- **Free tier** — Railway pe 500 hrs/month free milte hain (ek bot ke liye kaafi hai).

---

## Upgrade Ideas (baad mein)
- SQLite se permanent storage (restart pe data na jaaye)
- Password protection for certain files
- Download counter (kitne logon ne download kiya)
- Categories (Games / Apps / Tools)
