#!/usr/bin/env python3
"""Insta2TG - Telegram bot, public Instagram profiles: profile info, posts,
reels, stories (bina login best-effort + optional session), inline buttons ke saath."""

import asyncio
import json
import os
import tempfile

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

IG_LOCK = asyncio.Lock()

APP_ID = "936619743392459"
UA = ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")


def clean_username(text: str) -> str:
    t = text.strip().lstrip("@").split("?")[0].strip()
    if not t:
        return ""
    return t.split()[0].rstrip("/").split("/")[-1].lower()


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
    s.get("https://www.instagram.com/", timeout=30)
    s.headers.update({"x-ig-app-id": APP_ID})
    r = s.get("https://www.instagram.com/api/v1/users/web_profile_info/?username=" + username, timeout=30)
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
    r = s.post("https://www.instagram.com/api/v1/feed/reels_media/",
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
        raise last_err


def user_menu(username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Latest posts", callback_data="posts:" + username),
         InlineKeyboardButton("Reels", callback_data="reels:" + username)],
        [InlineKeyboardButton("Stories", callback_data="stories:" + username),
         InlineKeyboardButton("Profile", callback_data="profile:" + username)],
    ])


async def show_profile(bot, chat_id, username: str):
    profile = await fetch_profile(username)
    info = ("@" + profile.username + " - " + profile.full_name + "\n"
            "Followers: {:,} | Following: {:,}\n"
            "Posts: {:,}\n".format(profile.followers, profile.followees, profile.mediacount))
    if profile.biography:
        info += profile.biography[:300] + "\n"
    if profile.is_private:
        info += "Ye account private hai, content nahi dikhega."
    await bot.send_message(chat_id=chat_id, text=info, reply_markup=user_menu(profile.username))


async def send_posts(bot, chat_id, username: str):
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
    try:
        profile = await fetch_profile(username)
        posts = await ig_call(first_posts, profile, MAX_POSTS)
    except Exception:
        await bot.send_message(chat_id=chat_id, text="Instagram ne request rok di, thodi der baad try karo.")
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
        await bot.send_message(chat_id=chat_id, text="Instagram ne request rok di, thodi der baad try karo.")
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
            text="Abhi stories nahi mil rahi. Ya account ki stories chal hi nahi rahi, ya Instagram ne rok diya. "
                 "Thodi der baad try karo, ya README wala login session setup kar do.")
        return
    for i, (is_video, url) in enumerate(items):
        try:
            await send_media(bot, chat_id, is_video, url, "Story" if i == 0 else "")
        except Exception:
            pass


async def start(update: Update, context):
    await update.message.reply_text(
        "Kisi bhi PUBLIC Instagram account ka username bhejo.\n"
        "Profile khulegi aur niche buttons milenge, tap karne par posts, "
        "reels aur stories aa jayenge.")


async def on_button(update: Update, context):
    q = update.callback_query
    await q.answer()
    action, username = q.data.split(":", 1)
    chat_id = q.message.chat_id
    if action == "posts":
        await send_posts(context.bot, chat_id, username)
    elif action == "reels":
        await send_reels(context.bot, chat_id, username)
    elif action == "stories":
        await send_stories(context.bot, chat_id, username)
    elif action == "profile":
        try:
            await show_profile(context.bot, chat_id, username)
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text="Profile nahi khuli, thodi der baad try karo.")


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
        await update.message.reply_text("Instagram ne request rok di, 1-2 min baad try karna.")


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN set karo: export BOT_TOKEN='123456:ABC...'")

    app = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    print("[i] Bot chal raha hai. Band karne ke liye Ctrl+C.")
    app.run_polling()


if __name__ == "__main__":
    main()
