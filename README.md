# Insta2TG Bot

Telegram bot jo kisi bhi **public** Instagram account ka profile, latest posts, reels aur stories Telegram par bhej deta hai. Termux, laptop, VPC sab pe chal jata hai.

## Start kaise kare

1. Telegram me @BotFather kholo, /newbot se bot banao aur token copy karo.
2. Install: `pip install -r requirements.txt`
3. Token set karke chalao:
   ```
   export BOT_TOKEN="apna_botfather_token"
   python bot.py
   ```

## Bot ke commands

- `/posts <username>` - latest posts
- `/reels <username>` - latest reels
- `/stories <username>` - stories
- `/profile <username>` - profile info
- `/storysetup` - stories pakka dikhane ka login setup
- `/start` - pura menu aur help

Ya seedha username bhejo (jaise `natgeo` ya poora link), profile ke niche buttons aa jayenge: Latest posts, Reels, Stories, Profile.

## Stories pakka dikhane ke liye ek baar login (optional)

Instagram stories sirf logged-in session ko deta hai, isliye:

```
pip install instaloader
instaloader -l apna_instagram_username
export IG_USERNAME=apna_instagram_username
python bot.py
```

Password sirf local session file me jaata hai, repo me kahin nahi (gitignore me excluded hai).

## Dhyan rakhne wali baat

- Sirf PUBLIC accounts: private ka kuch nahi dikhega.
- Instagram datacenter/VPN ke IP aksar rok deta hai. Ghar ke IP pe (Termux/laptop) chalao to kaafi reliable rehta hai.
- Rate-limit: ek ke baad ek usernames mat do, thoda gap rakho.
- Ye Instagram ka official API nahi hai, apne hi use ke liye hai.

## Env variables

- `BOT_TOKEN` (zaroori) - BotFather se mila token
- `MAX_POSTS` (default 3), `MAX_REELS` (default 3), `MAX_STORIES` (default 5)
- `IG_USERNAME` - stories ke liye session file kis user ki load ho
