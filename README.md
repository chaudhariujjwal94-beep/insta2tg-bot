# Insta2TG Bot

Ek Telegram bot jo kisi bhi **public** Instagram account ka profile info, latest posts, reels aur stories Telegram par bhej deta hai. Repo se hi chalta hai: Termux, laptop ya VPS, kahin bhi.

## Kaise chalega (3 step)

1. Telegram me @BotFather kholo, /newbot bhejo, token copy karo
2. `pip install -r requirements.txt`
3. Token set karke chalao:
```
export BOT_TOKEN="apna_botfather_token"
python bot.py
```

Ab bot ko koi bhi public username bhejo (jaise `natgeo` ya `instagram.com/natgeo`). Profile info, latest 3 posts/reels, aur login ho to stories aa jayengi.

## Stories ke liye ek baar login (optional)

Instagram sirf logged-in session ko stories dikhata hai, isliye stories chahiye to ek baar:

```
pip install instaloader
instaloader -l apna_instagram_username
```

Password khud puchega aur local session file me save hoga, repo me kahin nahi jata. Phir bot chalate waqt:

```
export IG_USERNAME=apna_instagram_username
python bot.py
```

## Environment variables

| Variable | Zaroori? | Kaam |
|---|---|---|
| BOT_TOKEN | haan | BotFather se mila token |
| MAX_POSTS | nahi (default 3) | Ek username par kitne latest posts/reels |
| MAX_STORIES | nahi (default 5) | Ek baar me kitni stories |
| IG_USERNAME | nahi | Stories ke liye kis username ki session load karni hai |

## Limits (sach wali baat)

- Sirf PUBLIC accounts: private account ka content nahi milega.
- Instagram bina login ke requests rate-limit karta hai: ek ke baad ek bahut saare usernames mat bhejo, thoda gap rakho.
- Ye Instagram ka official API nahi hai (instaloader use karta hai), to Instagram ke badlav ke saath kabhi-kabhi update karna padta hai. Apne use ke liye hai.

## Kya bheje bot ko

Koi bhi message jisme username ho: `natgeo`, `@natgeo`, ya poora link `https://instagram.com/natgeo`. Bot username khud nikaal lega.
