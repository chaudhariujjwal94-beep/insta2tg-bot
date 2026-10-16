#!/usr/bin/env python3
"""Insta2TG - Telegram bot, public Instagram profiles: profile, posts, reels,
stories (bina-login best-effort + optional session), start menu + commands."""

import asyncio
import json
import os
import re
import tempfile
import types

import instaloader
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MAX_POSTS = int(os.environ.get("MAX_POSTS", "3"))
MAX_REELS = int(os.environ.get("MAX_REELS", "3"))
MAX_STORIES = int(os.environ.get("MAX_STORIES", "5"))
REPO_URL = "https://github.com/chaudhariujjwal94-beep/insta2tg-bot"
WORKER_URL = os.environ.get("IG_WORKER_URL", "https://insta-proxy.chaudhariujjwal94.workers.dev")
CRAWLBASE_TOKEN = os.environ.get("CRAWLBASE_TOKEN", "")
if not CRAWLBASE_TOKEN:
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "crawlbase_token.txt")) as _fh:
            CRAWLBASE_TOKEN = _fh.read().strip()
    except OSError:
        CRAWLBASE_TOKEN = ""

L = instaloader.Instaloader(
    quiet=True, download_comments=False, save_metadata=False,
    compress_json=False, post_metadata_txt_pattern="", filename_pattern="",
)

IG_USER = os.environ.get("IG_USERNAME", "")
if IG_USER:
    try:
        L.load_session_from_file(IG_USER)
        print("[i] Instagram session load ho gayi: " + IG_USER)
    except FileNotFoundError:
        print("[!] Session file nahi mili. Stories ke liye: instaloader -l " + IG_USER)

PROXY_URL = os.environ.get("PROXY_URL", "")
if PROXY_URL:
    L.context.proxies = {"http": PROXY_URL, "https": PROXY_URL}
    print("[i] Proxy lag gaya")

IG_LOCK = asyncio.Lock()

APP_ID = "936619743392459"
UA = ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")

HELP_TEXT = (
    "Insta2TG bot\n"
    "Kisi bhi PUBLIC Instagram ke liye:\n"
    "/posts <username> - latest posts\n"
    "/reels <username> - latest reels\n"
    "/stories <username> - stories\n"
    "/profile <username> - profile info\n"
    "/storysetup - stories pakka dikhane ka login setup\n"
    "Ya seedha username bhejo, profile ke niche buttons aa jayenge."
)

STORY_SETUP_TEXT = (
    "Stories pakka dikhane ke liye ek baar login session banao:\n"
    "1) pip install instaloader\n"
    "2) instaloader -l apna_instagram_username\n"
    "3) export IG_USERNAME=apna_instagram_username\n"
    "4) python bot.py\n"
    "Password sirf local session file me jaata hai, repo me kahin nahi."
)

BLOCKED_TEXT = ("Instagram ne request rok di. Iska karan aksar IP hota hai, "
                "datacenter ya VPN ke IP Instagram block karta hai. "
                "Ghar ke IP (Termux/laptop) pe chalao ya thodi der baad try karo.")


def clean_username(text: str) -> str:
    t = text.strip().lstrip("@").split("?")[0].strip()
    if not t:
        return ""
    return t.split()[0].rstrip("/").split("/")[-1].lower()


def username_from_args(args) -> str:
    return clean_username(" ".join(args)) if args else ""


def caption_of(post) -> str:
    kind = "Reel" if (post.is_video and getattr(post, "product_type", "") == "clips") else "Post"
    when = post.date_local.strftime("%d %b %Y") if post.date_local else ""
    head = ("@" + post.owner_username + " " + kind + " " + when).strip()
    cap = (post.caption or "").strip()
    if cap:
        return head + "\n" + cap[:900]
    return head


def media_items(post):
    items = []
    if post.mediacount > 1:
        for node in post.get_sidecar_nodes():
            items.append((node.is_video, node.video_url if node.is_video else node.display_url))
    elif post.is_video:
        items.append((True, post.video_url))
    else:
        items.append((False, post.display_url))
    return items[:10]


def first_posts(profile, n):
    out = []
    for i, post in enumerate(profile.get_posts()):
        if i >= n:
            break
        out.append(post)
    return out


def pick_reels(profile, want):
    out = []
    scanned = 0
    for post in profile.get_posts():
        scanned += 1
        if scanned > 40:
            break
        if post.is_video and getattr(post, "product_type", "") == "clips":
            out.append(post)
            if len(out) >= want:
                break
    return out


def list_users(username, which, n=20):
    import itertools
    prof = instaloader.Profile.from_username(L.context, username)
    gen = prof.get_followers() if which == "followers" else prof.get_followees()
    return [u.username for u in itertools.islice(gen, n)]


def tagged_posts(username, n=3):
    prof = instaloader.Profile.from_username(L.context, username)
    out = []
    for i, post in enumerate(prof.get_tagged_posts()):
        if i >= n:
            break
        out.append(post)
    return out


def ig_story_items(username, want):
    prof = instaloader.Profile.from_username(L.context, username)
    out = []
    for story in prof.get_stories():
        for item in story.get_items():
            out.append((item.is_video, item.video_url if item.is_video else item.display_url))
            if len(out) >= want:
                return out
    return out


def anon_story_items(username, want):
    s = requests.Session()
    s.headers.update({"user-agent": UA, "accept": "*/*", "x-requested-with": "XMLHttpRequest"})
    s.proxies.update({"http": PROXY_URL, "https": PROXY_URL})
    s.get(WORKER_URL + "/", timeout=30)
    s.headers.update({"x-ig-app-id": APP_ID})
    r = s.get(WORKER_URL + "/api/v1/users/web_profile_info/?username=" + username, timeout=30)
    try:
        j = r.json()
    except ValueError:
        raise RuntimeError("profile JSON nahi mila")
    user = (j.get("data") or {}).get("user")
    if not user:
        raise RuntimeError("profile endpoint fail")
    uid = user["id"]
    s.headers.update({
        "x-csrftoken": s.cookies.get("csrftoken") or "",
        "content-type": "application/x-www-form-urlencoded",
    })
    r = s.post(WORKER_URL + "/api/v1/feed/reels_media/",
               data={"user_ids": json.dumps([uid]), "source": "reels_media"}, timeout=30)
    try:
        j = r.json()
    except ValueError:
        raise RuntimeError("stories JSON nahi mila")
    items = []
    for reel in (j.get("reels") or {}).values():
        for it in reel.get("items", []):
            if it.get("media_type") == 2:
                versions = it.get("video_versions") or []
                url = versions[0].get("url") if versions else None
                if url:
                    items.append((True, url))
            else:
                cands = ((it.get("image_versions2") or {}).get("candidates")) or []
                url = cands[0].get("url") if cands else None
                if url:
                    items.append((False, url))
            if len(items) >= want:
                return items
    return items


def download_media(url: str, suffix: str) -> str:
    r = requests.get(url, stream=True, timeout=60, headers={"User-Agent": UA})
    r.raise_for_status()
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        for chunk in r.iter_content(1 << 16):
            f.write(chunk)
    return path


async def send_media(bot, chat_id, is_video: bool, url: str, caption: str = ""):
    try:
        if is_video:
            await bot.send_video(chat_id=chat_id, video=url, caption=caption,
                                 read_timeout=60, write_timeout=60)
        else:
            await bot.send_photo(chat_id=chat_id, photo=url, caption=caption,
                                 read_timeout=60, write_timeout=60)
        return
    except TelegramError:
        pass
    path = await asyncio.to_thread(download_media, url, ".mp4" if is_video else ".jpg")
    try:
        with open(path, "rb") as fh:
            if is_video:
                await bot.send_video(chat_id=chat_id, video=fh, caption=caption)
            else:
                await bot.send_photo(chat_id=chat_id, photo=fh, caption=caption)
    finally:
        os.remove(path)


async def ig_call(fn, *args):
    async with IG_LOCK:
        return await asyncio.to_thread(fn, *args)


def crawl_profile(username: str):
    """Crawlbase fallback: public profile page se basic info."""
    if not CRAWLBASE_TOKEN:
        raise RuntimeError("crawlbase token missing")
    r = requests.get("https://api.crawlbase.com/", params={
        "token": CRAWLBASE_TOKEN,
        "url": "https://www.instagram.com/" + username + "/",
    }, timeout=120)
    if r.status_code != 200:
        raise RuntimeError("crawlbase status %s" % r.status_code)
    h = r.text

    def og(prop):
        m = re.search(r'property="%s" content="([^"]*)"' % prop, h)
        return m.group(1) if m else ""

    title = og("og:title")
    desc = og("og:description")
    m = re.search(r'([\d.,]+[KkMmBb]?)\s*Followers,\s*([\d.,]+[KkMmBb]?)\s*Following,\s*([\d.,]+[KkMmBb]?)\s*Posts', desc)
    if not m or "(@" not in title:
        raise RuntimeError("profile data nahi mila (private ya block)")

    def num(sval):
        sval = sval.replace(",", "")
        mult = 1
        if sval and sval[-1] in "kK":
            mult, sval = 1000, sval[:-1]
        elif sval and sval[-1] in "mM":
            mult, sval = 1000000, sval[:-1]
        try:
            return int(float(sval) * mult)
        except ValueError:
            return 0

    return types.SimpleNamespace(
        username=username,
        full_name=title.split("(@")[0].strip().rstrip("\u2022").strip(),
        followers=num(m.group(1)),
        followees=num(m.group(2)),
        mediacount=num(m.group(3)),
        biography="",
        is_private=False,
    )


async def fetch_profile(username: str):
    async with IG_LOCK:
        last_err = None
        for attempt in range(2):
            try:
                return await asyncio.to_thread(instaloader.Profile.from_username, L.context, username)
            except instaloader.exceptions.ProfileNotExistsException:
                raise
            except Exception as e:
                last_err = e
                await asyncio.sleep(4)
        try:
            return await asyncio.to_thread(crawl_profile, username)
        except Exception as ce:
            last_err = ce
        raise last_err


def user_menu(username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001F504 Refresh Info", callback_data="refresh:" + username),
         InlineKeyboardButton("\U0001F4F7 Stories", callback_data="stories:" + username)],
        [InlineKeyboardButton("\U0001F4F0 Posts", callback_data="posts:" + username),
         InlineKeyboardButton("\U0001F3AC Reels", callback_data="reels:" + username)],
        [InlineKeyboardButton("\U0001F465 Followers", callback_data="followers:" + username),
         InlineKeyboardButton("\u27A1\uFE0F Following", callback_data="following:" + username)],
        [InlineKeyboardButton("\u2728 Highlights", callback_data="highlights:" + username),
         InlineKeyboardButton("\U0001F3F7\uFE0F Tagged in Posts", callback_data="tagged:" + username)],
        [InlineKeyboardButton("\U0001F648 Hide from History", callback_data="hide:" + username)],
        [InlineKeyboardButton("\U0001F514 Subscribe to new Stories", callback_data="subscribe:" + username)],
    ])


def start_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Help", callback_data="help"),
         InlineKeyboardButton("Story login setup", callback_data="storysetup")],
        [InlineKeyboardButton("Repo", callback_data="repo")],
    ])


async def show_profile(bot, chat_id, username: str):
    profile = await fetch_profile(username)
    info = "@" + profile.username + " | " + profile.full_name + "\n\n"
    if profile.biography:
        info += profile.biography.strip()[:300] + "\n\n"
    info += "Posts: {:,}\nFollowing: {:,}\nFollowers: {:,}".format(
        profile.mediacount, profile.followees, profile.followers)
    if profile.is_private:
        info += "Ye account private hai, content nahi dikhega."
    await bot.send_message(chat_id=chat_id, text=info, reply_markup=user_menu(profile.username))


async def send_posts(bot, chat_id, username: str):
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
    try:
        profile = await fetch_profile(username)
        posts = await ig_call(first_posts, profile, MAX_POSTS)
    except Exception:
        await bot.send_message(chat_id=chat_id, text=BLOCKED_TEXT)
        return
    if not posts:
        await bot.send_message(chat_id=chat_id, text="Koi post nahi mila.")
        return
    for post in posts:
        try:
            items = await ig_call(media_items, post)
        except Exception:
            continue
        for i, (is_video, url) in enumerate(items):
            try:
                await send_media(bot, chat_id, is_video, url, caption_of(post) if i == 0 else "")
            except Exception:
                pass


async def send_reels(bot, chat_id, username: str):
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
    try:
        profile = await fetch_profile(username)
        reels = await ig_call(pick_reels, profile, MAX_REELS)
    except Exception:
        await bot.send_message(chat_id=chat_id, text=BLOCKED_TEXT)
        return
    if not reels:
        await bot.send_message(chat_id=chat_id, text="Recent posts me koi reel nahi mili.")
        return
    for post in reels:
        try:
            await send_media(bot, chat_id, True, post.video_url, caption_of(post))
        except Exception:
            pass


async def send_stories(bot, chat_id, username: str):
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
    items = []
    try:
        items = await asyncio.to_thread(anon_story_items, username, MAX_STORIES)
    except Exception:
        items = []
    if not items and IG_USER:
        try:
            items = await ig_call(ig_story_items, username, MAX_STORIES)
        except Exception:
            items = []
    if not items:
        await bot.send_message(
            chat_id=chat_id,
            text="Abhi stories nahi mil rahi. Ya stories chal hi nahi rahi, ya Instagram ne rok diya. "
                 "Pakka chahiye to /storysetup wala login session bana lo.")
        return
    for i, (is_video, url) in enumerate(items):
        try:
            await send_media(bot, chat_id, is_video, url, "Story" if i == 0 else "")
        except Exception:
            pass


async def start(update: Update, context):
    await update.message.reply_text(HELP_TEXT, reply_markup=start_menu())


async def cmd_posts(update: Update, context):
    username = username_from_args(context.args)
    if not username:
        await update.message.reply_text("Aise likho, /posts natgeo")
        return
    await send_posts(context.bot, update.effective_chat.id, username)


async def cmd_reels(update: Update, context):
    username = username_from_args(context.args)
    if not username:
        await update.message.reply_text("Aise likho, /reels natgeo")
        return
    await send_reels(context.bot, update.effective_chat.id, username)


async def cmd_stories(update: Update, context):
    username = username_from_args(context.args)
    if not username:
        await update.message.reply_text("Aise likho, /stories natgeo")
        return
    await send_stories(context.bot, update.effective_chat.id, username)


async def cmd_profile(update: Update, context):
    username = username_from_args(context.args)
    if not username:
        await update.message.reply_text("Aise likho, /profile natgeo")
        return
    try:
        await show_profile(context.bot, update.effective_chat.id, username)
    except instaloader.exceptions.ProfileNotExistsException:
        await update.message.reply_text("Ye username exist nahi karta.")
    except Exception:
        await update.message.reply_text(BLOCKED_TEXT)


async def cmd_storysetup(update: Update, context):
    await update.message.reply_text(STORY_SETUP_TEXT)


async def on_button(update: Update, context):
    q = update.callback_query
    await q.answer()
    data = q.data
    chat_id = q.message.chat_id
    if data == "help":
        await context.bot.send_message(chat_id=chat_id, text=HELP_TEXT)
        return
    if data == "storysetup":
        await context.bot.send_message(chat_id=chat_id, text=STORY_SETUP_TEXT)
        return
    if data == "repo":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Repo kholo", url=REPO_URL)]])
        await context.bot.send_message(chat_id=chat_id, text="Poora code yahan hai", reply_markup=kb)
        return
    action, username = data.split(":", 1)
    if action in ("posts", "refresh", "profile"):
        await send_posts(context.bot, chat_id, username) if action == "posts" else None
        if action in ("refresh", "profile"):
            try:
                await show_profile(context.bot, chat_id, username)
            except Exception:
                await context.bot.send_message(chat_id=chat_id, text=BLOCKED_TEXT)
    elif action == "reels":
        await send_reels(context.bot, chat_id, username)
    elif action == "stories":
        await send_stories(context.bot, chat_id, username)
    elif action in ("followers", "following"):
        which = "followers" if action == "followers" else "following"
        try:
            names = await ig_call(list_users, username, which)
        except Exception:
            names = []
        if not names:
            await context.bot.send_message(chat_id=chat_id, text="List nahi mil paayi, Instagram ne rok diya.")
            return
        text = ("@" + username + " ke " + which + " (pehle " + str(len(names)) + "):\n"
                + ", ".join("@" + n for n in names))
        await context.bot.send_message(chat_id=chat_id, text=text)
    elif action == "tagged":
        try:
            posts = await ig_call(tagged_posts, username)
        except Exception:
            posts = []
        if not posts:
            await context.bot.send_message(chat_id=chat_id, text="Koi tagged post nahi mila.")
            return
        for post in posts:
            try:
                await send_media(context.bot, chat_id, post.is_video, post.video_url if post.is_video else post.display_url, caption_of(post))
            except Exception:
                pass
    elif action == "highlights":
        await context.bot.send_message(chat_id=chat_id, text="Highlights ke liye login session chahiye, wo abhi setup nahi hua hai.")
    elif action == "hide":
        await context.bot.send_message(chat_id=chat_id, text="Ye feature Instagram ki API nahi deta, isliye nahi ban sakta.")
    elif action == "subscribe":
        await context.bot.send_message(chat_id=chat_id, text="Abhi subscribe feature nahi hai, baad me add karunga.")


async def handle(update: Update, context):
    username = clean_username(update.message.text or "")
    if not username:
        await update.message.reply_text("Username bhejo, jaise natgeo")
        return
    await update.message.reply_text("@" + username + " dekh raha hu")
    try:
        await show_profile(context.bot, update.effective_chat.id, username)
    except instaloader.exceptions.ProfileNotExistsException:
        await update.message.reply_text("Ye username exist nahi karta.")
    except Exception:
        await update.message.reply_text(BLOCKED_TEXT)


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN set karo: export BOT_TOKEN='123456:ABC...'")

    app = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("posts", cmd_posts))
    app.add_handler(CommandHandler("reels", cmd_reels))
    app.add_handler(CommandHandler("stories", cmd_stories))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("storysetup", cmd_storysetup))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    print("[i] Bot chal raha hai. Band karne ke liye Ctrl+C.")
    app.run_polling()


if __name__ == "__main__":
    main()
