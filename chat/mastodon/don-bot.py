#!/usr/bin/env python3
"""
don-bot.py

Chat with your persyn on Mastodon.
"""
# pylint: disable=import-error, wrong-import-position, wrong-import-order, invalid-name
import datetime
import json
import random
import sys
import tempfile
import uuid

from pathlib import Path
from hashlib import sha256

from bs4 import BeautifulSoup
from mastodon import Mastodon, MastodonError, MastodonMalformedEventError, StreamListener

import requests

# Add persyn root to sys.path
sys.path.insert(0, str((Path(__file__) / '../../../').resolve()))

# Color logging
from utils.color_logging import log

# Bot config
from utils.config import load_config

# Reminders
from interaction.reminders import Reminders

# Mastodon support for image posting
from chat.mastodon.login import mastodon

# Common chat library
from chat.common import Chat

persyn_config = load_config()

# Chat library
chat = Chat(persyn_config, service='discord')

# Coroutine reminders
reminders = Reminders()

# def it_me(author_id):
#     ''' Return True if the given id is one of ours '''
#     return author_id in [app.user.id, persyn_config.chat.discord.webhook_id]

# def say_something_later(ctx, when, what=None):
#     ''' Continue the train of thought later. When is in seconds. If what, just say it. '''
#     channel = get_channel(ctx)
#     reminders.cancel(channel)

#     if what:
#         reminders.add(channel, when, ctx.channel.send, args=what)
#     else:
#         # Yadda yadda yadda
#         ctx.content = "..."
#         reminders.add(channel, when, on_message, args=ctx)

# def synthesize_image(ctx, prompt, engine="stable-diffusion", style=None):
#     ''' It's not AI art. It's _image synthesis_ '''
#     channel = get_channel(ctx)
#     chat.take_a_photo(channel, prompt, engine=engine, style=style)
#     say_something_later(ctx, when=3, what=":camera_with_flash:")

#     ents = chat.get_entities(prompt)
#     if ents:
#         chat.inject_idea(channel, ents)

# def fetch_and_post_to_masto(url, toot):
#     ''' Download the image at URL and post it to Mastodon '''
#     if not mastodon:
#         log.error("🎺 Mastodon not configured, check your yaml config.")
#         return

#     media_ids = []
#     try:
#         with tempfile.TemporaryDirectory() as tmpdir:
#             response = requests.get(url, timeout=10)
#             response.raise_for_status()
#             fname = f"{tmpdir}/{uuid.uuid4()}.{url[-3:]}"
#             with open(fname, "wb") as f:
#                 for chunk in response.iter_content():
#                     f.write(chunk)
#             caption = chat.get_caption(url)
#             media_ids.append(mastodon.media_post(fname, description=caption).id)

#             resp = mastodon.status_post(
#                 toot,
#                 media_ids=media_ids,
#                 idempotency_key=sha256(url.encode()).hexdigest()
#             )
#             if not resp or 'url' not in resp:
#                 raise RuntimeError(resp)
#             log.info(f"🎺 Posted {url}: {resp['url']}")

#     except RuntimeError as err:
#         log.error(f"🎺 Could not post {url}: {err}")


# async def schedule_reply(ctx):
#     ''' Gather a reply and say it when ready '''
#     channel = get_channel(ctx)

#     log.warning("⏰ schedule_reply")

#     (the_reply, goals_achieved) = chat.get_reply(channel, ctx.content, ctx.author.name, ctx.author.id)
#     await ctx.channel.send(the_reply)

#     for goal in goals_achieved:
#         await ctx.channel.send(f"🏆 _achievement unlocked: {goal}_")

#     chat.summarize_later(channel, reminders)

#     if the_reply.endswith('…') or the_reply.endswith('...'):
#         say_something_later(
#             ctx,
#             when=1
#         )
#         return

#     # 5% chance of random interjection later
#     if random.random() < 0.05:
#         say_something_later(
#             ctx,
#             when=random.randint(2, 5)
#         )

# async def handle_attachments(ctx):
#     ''' Caption photos posted to the channel '''
#     channel = get_channel(ctx)
#     for attachment in ctx.attachments:
#         caption = chat.get_caption(attachment.url)

#         if caption:
#             prefix = random.choice(["I see", "It looks like", "Looks like", "Might be", "I think it's"])
#             await ctx.channel.send(f"{prefix} {caption}")

#             chat.inject_idea(channel, f"{ctx.author.name} posted a photo of {caption}")

#             msg = ctx.content
#             if not msg.strip():
#                 msg = f"{ctx.author.name} posted a photo of {caption}"

#             reply, goals_achieved = chat.get_reply(channel, msg, ctx.author.name, ctx.author.id)

#             await ctx.channel.send(reply)

#             for goal in goals_achieved:
#                 await ctx.channel.send(f"🏆 _achievement unlocked: {goal}_")
#         else:
#             await ctx.channel.send(
#                 random.choice([
#                     "I'm not sure.",
#                     ":face_with_monocle:",
#                     ":face_with_spiral_eyes:",
#                     "What the...?",
#                     "Um.",
#                     "No idea.",
#                     "Beats me."
#                 ])
#             )

class TheListener(StreamListener):

    def __init__(self):
        self.service = "mastodon"
        self.channel = mastodon.me().url
        # Note: Restart if your bot follows new people.
        self.followers = [follower.id for follower in mastodon.account_followers(id=mastodon.me().id)]

    def get_text(self, msg):
        return BeautifulSoup(msg).text.strip().replace(f'@{mastodon.me().username} ','')

     # def on_update(self, update):
     #      print(f"Got update: {update}")

     # def on_conversation(self, conversation):
     #      print(f"Got conversation: {conversation}")

    def on_notification(self, notification):
        ''' Handle notifications '''
        log.info(f"📫 Notification: {notification.status.id}")

        if notification.status.account.id not in self.followers:
            log.warning("📪 Ignoring notification from non-follower:", notification.status.account.acct)
            return

        msg = self.get_text(notification.status.content)
        log.info(f"📬 {notification.status.account.acct}:", msg)

    def handle_heartbeat(self):
        print("💓")

# async def dispatch(ctx):
#     ''' Handle commands '''
#     channel = get_channel(ctx)

#     if ctx.attachments:
#         await handle_attachments(ctx)

#     elif ctx.content.startswith('🎨'):
#         await ctx.channel.send(f"OK, {ctx.author.name}.")
#         synthesize_image(ctx, ctx.content[1:].strip(), engine="stable-diffusion")

#     elif ctx.content.startswith('🪄'):
#         await ctx.channel.send(f"OK, {ctx.author.name}.")
#         prompt = ctx.content[1:].strip()
#         style = chat.prompt_parrot(prompt)
#         log.warning(f"🦜 {style}")
#         synthesize_image(ctx, prompt, engine="stable-diffusion", style=style)

#     elif ctx.content == '🤳':
#         await ctx.channel.send(
#             f"OK, {ctx.author.name}.\n_{persyn_config.id.name} takes out a camera and smiles awkwardly_."
#         )
#         say_something_later(
#             ctx,
#             when=9,
#             what=":cheese_wedge: *CHEESE!* :cheese_wedge:"
#         )
#         chat.take_a_photo(
#             get_channel(ctx),
#             f"A selfie for {ctx.author.name}",
#             engine="stylegan2",
#             model=random.choice(["ffhq", "waifu"])
#         )

#     elif ctx.content == 'help':
#         await ctx.channel.send(f"""*Commands:*
#   `...`: Let {persyn_config.id.name} keep talking without interrupting
#   `summary`: Explain it all to me very briefly.
#   `status`: Say exactly what is on {persyn_config.id.name}'s mind.
#   `nouns`: Some things worth thinking about.
#   `reflect`: {persyn_config.id.name}'s opinion of those things.
#   `daydream`: Let {persyn_config.id.name}'s mind wander on the convo.
#   `goals`: See {persyn_config.id.name}'s current goals

#   *Image generation:*
#   :art: _prompt_ : Generate a picture of _prompt_ using stable-diffusion
#   :magic_wand: _prompt_ : Generate a *fancy* picture of _prompt_ using stable-diffusion
#   :selfie: Take a selfie
# """)

#     elif ctx.content == 'status':
#         status = ("\n".join([f"> {line.strip()}" for line in chat.get_status(channel).split("\n")])).rstrip("> \n")
#         if len(status) < 2000:
#             await ctx.channel.send(status.strip())
#         else:
#             # 2000 character limit for messages
#             reply = ""
#             for line in status.split("\n"):
#                 if len(reply) + len(line) < 1999:
#                     reply = reply + line + "\n"
#                 else:
#                     await ctx.channel.send(reply)
#                     reply = line + "\n"
#             if reply:
#                 await ctx.channel.send(reply)

#     elif ctx.content == 'summary':
#         await ctx.channel.send("💭 " + chat.get_summary(channel, save=False, include_keywords=False, photo=True))

#     elif ctx.content == 'summary!':
#         await ctx.channel.send("💭 " + chat.get_summary(channel, save=True, include_keywords=True, photo=False))

#     elif ctx.content == 'nouns':
#         await ctx.channel.send("> " + ", ".join(chat.get_nouns(chat.get_status(channel))))

#     else:
#         reminders.add(channel, 0, schedule_reply, f'reply-{uuid.uuid4()}', args=[ctx])

# async def on_message(ctx):
#     ''' Default message handler. '''
#     channel = get_channel(ctx)

#     # Don't talk to yourself.
#     if it_me(ctx.author.id):
#         return

#     # Interrupt any rejoinder in progress
#     reminders.cancel(channel)

#     if ctx.author.bot:
#         log.warning(f'🤖 BOT DETECTED: {ctx.author.name} ({ctx.author.id})')
#         # 95% chance to just ignore them
#         if random.random() < 0.95:
#             return

#     # Handle commands and schedule a reply (if any)
#     await dispatch(ctx)



try:
    mastodon = Mastodon(
        access_token = persyn_config.chat.mastodon.secret,
        api_base_url = persyn_config.chat.mastodon.instance
    )
except (MastodonError, AttributeError):
    raise SystemExit("Invalid credentials, run masto-login.py and try again.")

log.info(f"🎺 Logged in as: {mastodon.me().url}")

listener = TheListener()

while True:
    try:
        mastodon.stream_user(listener)
    except MastodonMalformedEventError:
        log.critical("MastodonMalformedEventError, continuing.")

