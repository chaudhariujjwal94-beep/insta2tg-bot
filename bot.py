#!/usr/bin/env python3
"""Insta2TG - Telegram bot jo kisi bhi PUBLIC Instagram profile ka
profile info, latest posts, reels aur stories Telegram par bhejta hai."""

import os
import tempfile

import instaloader
import requests
from telegram import Update
from telegram.constants import ChatAction
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
        print("[!] Session file nahi mili. Stories ke liye pehle chalao: instaloader -l " + IG_USER)


def clean_username(text: str) -> str:
    t = text.strip().lstrip("@").split("?")[0].strip()
    if not t:
        return ""
    t = t.split()[0].rstrip("/").split("/")[-1]
    return t.lower()


def caption_of(post) -> str:
    kind = "Reel" if (post.is_video and post.mediacount == 1) else "Post"
    when = post.date_local.strftime("%d %b %Y") if post.date_local else ""
    head = ("@" + post.owner_username + " " + kind + " " + when).strip()
    cap = (post.caption or "").strip()
    if cap:
        return head + "\n" + cap[:900]
    return head


def download_media(url: str, suffix: str) -> str:
    r = requests.get(url, stream=True, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        for chunk in r.iter_content(1 << 16):
            f.write(chunk)
    return path


async def send_media(update: Update, is_video: bool, url: str, caption: str = ""):
    suffix = ".mp4" if is_video else ".jpg"
    path = download_media(url, suffix)
    try:
        with open(path, "rb") as fh:
            if is_video:
                await update.message.reply_video(video=fh, caption=caption)
            else:
                await update.message.reply_photo(photo=fh, caption=caption)
    finally:
        os.remove(path)


async def send_post(update: Update, post):
    cap = caption_of(post)
    if post.mediacount > 1:
        nodes = list(post.get_sidecar_nodes())[:10]
        for i, node in enumerate(nodes):
            await send_media(update, node.is_video,
                             node.video_url if node.is_video else node.display_url,
                             cap if i == 0 else "")
    elif post.is_video:
        await send_media(update, True, post.video_url, cap)
    else:
        await send_media(update, False, post.display_url, cap)


async def start(update: Update, context):
    await update.message.reply_text(
        "Kisi bhi PUBLIC Instagram account ka username bhejo.\n"
        "Main bhejunga: profile info, latest posts, reels aur stories "
        "(stories ke liye ek baar login session chahiye, README dekho)."
    )


async def handle(update: Update, context):
    username = clean_username(update.message.text or "")
    if not username:
        await update.message.reply_text("Username bhejo, jaise: natgeo")
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        profile = instaloader.Profile.from_username(L.context, username)
    except instaloader.exceptions.ProfileNotExistsException:
        await update.message.reply_text("Ye username exist nahi karta.")
        return
    except Exception:
        await update.message.reply_text("Instagram ne request rok di (rate limit). Thodi der baad try karo.")
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
        sent = 0
        for post in profile.get_posts():
            if sent >= MAX_POSTS:
                break
            await send_post(update, post)
            sent += 1
    except Exception:
        await update.message.reply_text("Posts laate waqt Instagram ne block kar diya. Thodi der baad try karo.")

    try:
        count = 0
        for story in profile.get_stories():
            for item in story.get_items():
                if count >= MAX_STORIES:
                    break
                await send_media(update, item.is_video,
                                 item.video_url if item.is_video else item.display_url,
                                 "Story")
                count += 1
    except instaloader.exceptions.ConnectionException:
        await update.message.reply_text("Stories ke liye bot ko ek baar Instagram login chahiye. README me 2 min ka setup hai.")
    except Exception:
        pass


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN set karo: export BOT_TOKEN='123456:ABC...'")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    print("[i] Bot chal raha hai. Band karne ke liye Ctrl+C.")
    app.run_polling()


if __name__ == "__main__":
    main()
