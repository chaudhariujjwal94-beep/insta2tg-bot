#!/usr/bin/env python3
"""Insta2TG - fast Telegram bot jo public Instagram profiles ka
profile info, posts, reels aur stories bhejta hai."""

import asyncio
import os
import tempfile

import instaloader
import requests
from telegram import Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, MessageHandler, filters

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MAX_POSTS = int(os.environ.get("MAX_POSTS", "3"))
MAX_STORIES = int(os.environ.get("MAX_STORIES", "5"))

L = instaloader.Instaloader(
    quiet=True,
    download_comments=False,
    save_metadata=False,
    compress_json=False,
    post_metadata_txt_pattern="",
    filename_pattern="",
)

IG_USER = os.environ.get("IG_USERNAME", "")
if IG_USER:
    try:
        L.load_session_from_file(IG_USER)
        print("[i] Instagram session load ho gayi: " + IG_USER)
    except FileNotFoundError:
        print("[!] Session file nahi mili. Stories ke liye pehle: instaloader -l " + IG_USER)

IG_LOCK = asyncio.Lock()


def clean_username(text: str) -> str:
    t = text.strip().lstrip("@").split("?")[0].strip()
    if not t:
        return ""
    return t.split()[0].rstrip("/").split("/")[-1].lower()


def caption_of(post) -> str:
    kind = "Reel" if (post.is_video and post.mediacount == 1) else "Post"
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


def story_items(profile, n):
    out = []
    for story in profile.get_stories():
        for item in story.get_items():
            out.append((item.is_video, item.video_url if item.is_video else item.display_url))
            if len(out) >= n:
                return out
    return out


def download_media(url: str, suffix: str) -> str:
    r = requests.get(url, stream=True, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        for chunk in r.iter_content(1 << 16):
            f.write(chunk)
    return path


async def send_media(update: Update, is_video: bool, url: str, caption: str = ""):
    # pehle seedha URL, Telegram khud utha leta hai, download ka wait nahi
    try:
        if is_video:
            await update.message.reply_video(video=url, caption=caption,
                                             read_timeout=60, write_timeout=60)
        else:
            await update.message.reply_photo(photo=url, caption=caption,
                                             read_timeout=60, write_timeout=60)
        return
    except TelegramError:
        pass  # URL fail hua, download karke bhejo
    path = await asyncio.to_thread(download_media, url, ".mp4" if is_video else ".jpg")
    try:
        with open(path, "rb") as fh:
            if is_video:
                await update.message.reply_video(video=fh, caption=caption)
            else:
                await update.message.reply_photo(photo=fh, caption=caption)
    finally:
        os.remove(path)


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


async def start(update: Update, context):
    await update.message.reply_text(
        "Kisi bhi PUBLIC Instagram account ka username bhejo.\n"
        "Main bhejunga: profile info, latest posts, reels aur stories "
        "(stories ke liye ek baar login session chahiye, README dekho)."
    )


async def handle(update: Update, context):
    username = clean_username(update.message.text or "")
    if not username:
        await update.message.reply_text("Username bhejo, jaise natgeo")
        return

    await update.message.reply_text("@" + username + " dekh raha hu, media aata hi hoga")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    try:
        profile = await fetch_profile(username)
    except instaloader.exceptions.ProfileNotExistsException:
        await update.message.reply_text("Ye username exist nahi karta.")
        return
    except Exception:
        await update.message.reply_text("Instagram ne request rok di, 1-2 min baad try karna.")
        return

    info = ("@" + profile.username + " - " + profile.full_name + "\n"
            "Followers: {:,} | Following: {:,}\n"
            "Posts: {:,}\n".format(profile.followers, profile.followees, profile.mediacount))
    if profile.biography:
        info += profile.biography[:300] + "\n"
    await update.message.reply_text(info)

    if profile.is_private:
        await update.message.reply_text("Ye account private hai, iska content nahi dikhta.")
        return

    try:
        posts = await asyncio.to_thread(first_posts, profile, MAX_POSTS)
    except Exception:
        posts = []
    for post in posts:
        try:
            items = await asyncio.to_thread(media_items, post)
        except Exception:
            continue
        for i, (is_video, url) in enumerate(items):
            try:
                await send_media(update, is_video, url, caption_of(post) if i == 0 else "")
            except Exception:
                pass  # ek media fail ho to baaki chalte rahenge

    need_login = False
    try:
        items = await asyncio.to_thread(story_items, profile, MAX_STORIES)
    except instaloader.exceptions.ConnectionException:
        need_login = True
        items = []
    except Exception:
        items = []
    for i, (is_video, url) in enumerate(items):
        try:
            await send_media(update, is_video, url, "Story" if i == 0 else "")
        except Exception:
            pass
    if need_login:
        await update.message.reply_text("Stories ke liye bot ko ek baar Instagram login chahiye, README me setup hai.")


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN set karo: export BOT_TOKEN='123456:ABC...'")

    app = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    print("[i] Bot chal raha hai. Band karne ke liye Ctrl+C.")
    app.run_polling()


if __name__ == "__main__":
    main()
