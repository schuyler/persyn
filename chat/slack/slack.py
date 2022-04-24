#!/usr/bin/env python3
"""
slack.py

A Slack chat plugin for Persyn. Sends Slack events to interact.py.
"""
import os
import random
import re
import tempfile

import threading as th

import requests
import tweepy

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk.errors import SlackApiError

# Color logging
from color_logging import ColorLog

log = ColorLog()

# These are all defined in config/*.conf
BOT_NAME = os.environ["BOT_NAME"]
BOT_ID = os.environ["BOT_ID"]

IMAGE_ENGINES = ["v-diffusion-pytorch-cfg", "v-diffusion-pytorch-clip"] # "vqgan", "stylegan2"
IMAGE_MODELS = {
    "stylegan2": ["ffhq", "waifu", "cat"], #, "car", "church", "horse"
    "v-diffusion-pytorch-cfg": ["cc12m_1_cfg"],
    "v-diffusion-pytorch-clip": ["yfcc_2", "cc12m_1"],
    "latent-diffusion": ["default"]
}

# Twitter
twitter_auth = tweepy.OAuthHandler(os.environ['TWITTER_CONSUMER_KEY'], os.environ['TWITTER_CONSUMER_SECRET'])
twitter_auth.set_access_token(os.environ['TWITTER_ACCESS_TOKEN'], os.environ['TWITTER_ACCESS_TOKEN_SECRET'])

twitter = tweepy.API(twitter_auth)

BASEURL = os.environ.get('BASEURL', None)

# Slack bolt App
app = App(token=os.environ['SLACK_BOT_TOKEN'])
# Saved to the service field in ltm
SLACK_SERVICE = app.client.auth_test().data['url']

# Reminders: container for delayed response threads
reminders = {}

# Username cache
known_users = {}

# TODO: callback thread to poll(?) interact, or inbound API call for push notifications

def new_channel(channel):
    ''' Initialize a new channel. '''
    reminders[channel] = {
        'rejoinder': th.Timer(0, log.warning, ["New channel rejoinder:", channel]),
        'summarizer': th.Timer(1, log.warning, ["New channel summarizer:", channel]),
        'count': 0
    }
    reminders[channel]['rejoinder'].start()
    reminders[channel]['summarizer'].start()

def get_display_name(user_id):
    """ Return the user's first name if available, otherwise the display name """
    if user_id not in known_users:
        users_info = app.client.users_info(user=user_id)['user']
        try:
            known_users[user_id] = users_info['profile']['first_name'] or users_info['profile']['display_name']
        except KeyError:
            known_users[user_id] = users_info['profile']['display_name'] or user_id

    return known_users[user_id]

def substitute_names(text):
    """ Substitute all <@XYZ> in text with the equivalent display name. """
    for user_id in set(re.findall(r'<@(\w+)>', text)):
        text = re.sub(f'<@{user_id}>', get_display_name(user_id), text)
    return text

def take_a_photo(channel, prompt, engine=None, model=None):
    ''' Pick an image engine and generate a photo '''
    if not engine:
        engine = random.choice(IMAGE_ENGINES)

    req = {
        "engine": engine,
        "channel": channel,
        "prompt": prompt,
        "model": model or random.choice(IMAGE_MODELS[engine])
    }
    reply = requests.post(f"{os.environ['DREAM_SERVER_URL']}/generate/", params=req)
    if reply.ok:
        log.warning(f"{os.environ['DREAM_SERVER_URL']}/generate/", f"{prompt}: {reply.status_code}")
    else:
        log.error(f"{os.environ['DREAM_SERVER_URL']}/generate/", f"{prompt}: {reply.status_code} {reply.json()}")
    return reply.ok

def get_reply(channel, msg, speaker_name, speaker_id):
    ''' Ask interact for an appropriate response. '''
    if msg != '...':
        log.info(f"[{channel}] {speaker_name}: {msg}")

    req = {
        "service": SLACK_SERVICE,
        "channel": channel,
        "msg": msg,
        "speaker_name": speaker_name,
        "speaker_id": speaker_id
    }
    try:
        response = requests.post(f"{os.environ['INTERACT_SERVER_URL']}/reply/", params=req)
        response.raise_for_status()
    except requests.exceptions.RequestException as err:
        log.critical(f"🤖 Could not post /reply/ to interact: {err}")
        return ":shrug:"

    reply = response.json()['reply']
    log.warning(f"[{channel}] {BOT_NAME}: {reply}")
    return reply

def get_summary(channel, save=False):
    ''' Ask interact for a channel summary. '''
    req = {
        "service": SLACK_SERVICE,
        "channel": channel,
        "save": save
    }
    try:
        reply = requests.post(f"{os.environ['INTERACT_SERVER_URL']}/summary/", params=req)
        reply.raise_for_status()
    except requests.exceptions.RequestException as err:
        log.critical(f"🤖 Could not post /summary/ to interact: {err}")
        return ":shrug:"

    summary = reply.json()['summary']
    log.warning(f"∑ {reply.json()['summary']}")

    if summary:
        take_a_photo(channel, summary)
        return summary

    return ":shrug:"

def get_status(channel):
    ''' Ask interact for status. '''
    req = {
        "service": SLACK_SERVICE,
        "channel": channel,
    }
    try:
        reply = requests.post(f"{os.environ['INTERACT_SERVER_URL']}/status/", params=req)
        reply.raise_for_status()
    except requests.exceptions.RequestException as err:
        log.critical(f"🤖 Could not post /status/ to interact: {err}")
        return ":shrug:"

    return reply.json()['status']

def forget_it(channel):
    ''' There is no antimemetics division. '''
    req = {
        "service": SLACK_SERVICE,
        "channel": channel,
    }
    try:
        response = requests.post(f"{os.environ['INTERACT_SERVER_URL']}/amnesia/", params=req)
        response.raise_for_status()
    except requests.exceptions.RequestException as err:
        log.critical(f"🤖 Could not forget_it(): {err}")
        return ":shrug:"

    return ":exploding_head:"

@app.message(re.compile(r"^help$", re.I))
def help_me(say, context): # pylint: disable=unused-argument
    ''' TODO: These should really be / commands. '''
    say(f"""Commands:
  `...`: Let {BOT_NAME} keep talking without interrupting
  `summary`: Explain it all to me in a single sentence.
  `status`: Say exactly what is on {BOT_NAME}'s mind.
  :camera: : Generate a picture summarizing this conversation
  :eye: _prompt_ : Generate a picture of _prompt_ using latent-diffusion
  :camera: _prompt_ : Generate a picture of _prompt_ using v-diffusion-pytorch-cfg
  :paperclip: _prompt_ : Generate a picture of _prompt_ using clip guided diffusion
  :selfie: Take a selfie
""")

@app.message(re.compile(r"^:camera:(.+)$"))
def picture(say, context): # pylint: disable=unused-argument
    ''' Take a picture, it'll last longer '''
    speaker_id = context['user_id']
    speaker_name = get_display_name(speaker_id)
    channel = context['channel_id']
    prompt = context['matches'][0].strip()

    say(f"OK, {speaker_name}.\n_{BOT_NAME} takes out a camera and frames the scene_")
    say_something_later(
        say,
        channel,
        context,
        when=60,
        what=f"*{BOT_NAME} takes a picture of _{prompt}_* It will take a few minutes to develop."
    )
    take_a_photo(channel, prompt, engine="v-diffusion-pytorch-cfg")

    msg = f'I wonder what "{prompt}" looks like.'
    the_reply = get_reply(channel, msg, speaker_name, speaker_id)

    if the_reply == ":shrug:":
        return

    say(the_reply)
    summarize_later(channel)

@app.message(re.compile(r"^:paperclip:(.+)$"))
def clip_picture(say, context): # pylint: disable=unused-argument
    ''' Take a picture with CLIP, it'll last longer '''
    speaker_id = context['user_id']
    speaker_name = get_display_name(speaker_id)
    channel = context['channel_id']
    prompt = context['matches'][0].strip()

    say(f"OK, {speaker_name}.\n_{BOT_NAME} takes out an old fashioned camera and frames the scene_")
    say_something_later(
        say,
        channel,
        context,
        when=60,
        what=f"*{BOT_NAME} takes a picture of _{prompt}_* It will take a few minutes to develop."
    )
    take_a_photo(channel, prompt, engine="v-diffusion-pytorch-clip")

    msg = f'I wonder what "{prompt}" looks like.'
    the_reply = get_reply(channel, msg, speaker_name, speaker_id)

    if the_reply == ":shrug:":
        return

    say(the_reply)
    summarize_later(channel)

@app.message(re.compile(r"^:selfie:$"))
def selfie(say, context): # pylint: disable=unused-argument
    ''' Take a picture, it'll last longer '''
    them = get_display_name(context['user_id'])
    channel = context['channel_id']

    say(f"OK, {them}.\n_{BOT_NAME} takes out a camera and smiles awkwardly_.")
    say_something_later(
        say,
        channel,
        context,
        when=8,
        what=":cheese_wedge: *CHEESE!* :cheese_wedge:"
    )
    take_a_photo(
        channel,
        context['matches'][0].strip(),
        engine="stylegan2",
        model=random.choice(["ffhq", "waifu", "cat"])
    )

@app.message(re.compile(r"^:camera:$"))
def photo_summary(say, context): # pylint: disable=unused-argument
    ''' Take a photo of this conversation '''
    them = get_display_name(context['user_id'])
    channel = context['channel_id']

    say(f"OK, {them}.\n_{BOT_NAME} takes out a shiny new camera and frames the scene_")
    say_something_later(
        say,
        channel,
        context,
        when=20,
        what=f"*click* _{BOT_NAME} shakes it like a polaroid picture_"
    )
    take_a_photo(channel, get_summary(channel), engine="latent-diffusion")

@app.message(re.compile(r"^:paperclip:$"))
def photo_clip_summary(say, context): # pylint: disable=unused-argument
    ''' Take a CLIP photo of this conversation '''
    them = get_display_name(context['user_id'])
    channel = context['channel_id']

    say(f"OK, {them}.\n_{BOT_NAME} takes out an old fashioned camera and frames the scene_")
    say_something_later(
        say,
        channel,
        context,
        when=60,
        what=f"*click* _{BOT_NAME} shakes it like a polaroid picture_"
    )
    take_a_photo(channel, get_summary(channel), engine="v-diffusion-pytorch-clip")

@app.message(re.compile(r"^:eye:$"))
def photo_ld_summary(say, context): # pylint: disable=unused-argument
    ''' Take a CLIP photo of this conversation '''
    them = get_display_name(context['user_id'])
    channel = context['channel_id']

    say(f"OK, {them}.\n_{BOT_NAME} takes out a shiny new camera and frames the scene_")
    say_something_later(
        say,
        channel,
        context,
        when=20,
        what=f"*click* _{BOT_NAME} shakes it like a polaroid picture_"
    )
    take_a_photo(channel, get_summary(channel), engine="latent-diffusion")

@app.message(re.compile(r"^:eye:(.+)$"))
def ld_picture(say, context): # pylint: disable=unused-argument
    ''' Take a picture with CompVis latent diffusion, it'll be better '''
    speaker_id = context['user_id']
    speaker_name = get_display_name(speaker_id)
    channel = context['channel_id']
    prompt = context['matches'][0].strip()

    say(f"OK, {speaker_name}.\n_{BOT_NAME} takes out a shiny new camera and frames the scene_")
    say_something_later(
        say,
        channel,
        context,
        when=20,
        what=f"*{BOT_NAME} takes a picture of _{prompt}_*."
    )
    take_a_photo(channel, prompt, engine="latent-diffusion")

    msg = f'I wonder what "{prompt}" looks like.'
    the_reply = get_reply(channel, msg, speaker_name, speaker_id)

    if the_reply == ":shrug:":
        return

    say(the_reply)
    summarize_later(channel)

@app.message(re.compile(r"^summary(\!)?$", re.I))
def summarize(say, context):
    ''' Say a condensed summary of this channel '''
    save = bool(context['matches'][0])
    channel = context['channel_id']
    say("💭 " + get_summary(channel, save))

@app.message(re.compile(r"^status$", re.I))
def status(say, context):
    ''' Say a condensed summary of this channel '''
    channel = context['channel_id']
    say("\n".join([f"> {line}" for line in get_status(channel).split("\n")]))

def say_something_later(say, channel, context, when, what=None):
    ''' Continue the train of thought later. When is in seconds. If what, just say it. '''
    if channel not in reminders:
        new_channel(channel)

    reminders[channel]['rejoinder'].cancel()

    if what:
        reminders[channel]['rejoinder'] = th.Timer(when, say, [what])
    else:
        # Only 3 autoreplies max
        if reminders[channel]['count'] >= 3:
            reminders[channel]['count'] = 0
            return

        reminders[channel]['count'] = reminders[channel]['count'] + 1

        # yadda yadda yadda
        yadda = {
            'channel_id': channel,
            'user_id': context['user_id'],
            'matches': ['...']
        }
        reminders[channel]['rejoinder'] = th.Timer(when, catch_all, [say, yadda])

    reminders[channel]['rejoinder'].start()

def summarize_later(channel, when=600):
    '''
    Summarize the train of thought later. When is in seconds.

    Every time this thread executes, a new convo summary is saved. Only one
    can run at a time.
    '''
    if channel not in reminders:
        new_channel(channel)

    reminders[channel]['summarizer'].cancel()
    reminders[channel]['summarizer'] = th.Timer(when, get_summary, [channel, True])
    reminders[channel]['summarizer'].start()

@app.message(re.compile(r"(.*)", re.I))
def catch_all(say, context):
    ''' Default message handler. Prompt GPT and randomly arm a Timer for later reply. '''
    channel = context['channel_id']

    if channel not in reminders:
        new_channel(channel)

    # Interrupt any rejoinder in progress
    reminders[channel]['rejoinder'].cancel()

    speaker_id = context['user_id']
    speaker_name = get_display_name(speaker_id)
    msg = substitute_names(' '.join(context['matches'])).strip()

    the_reply = get_reply(channel, msg, speaker_name, speaker_id)

    if the_reply == ":shrug:":
        return

    say(the_reply)
    summarize_later(channel)

    if the_reply.endswith('…') or the_reply.endswith('...'):
        say_something_later(
            say,
            channel,
            context,
            when=1
        )
        return

    # 5% chance of random interjection later
    if random.random() < 0.05:
        say_something_later(
            say,
            channel,
            context,
            when=random.randint(2, 5)
        )


@app.event("app_mention")
def handle_app_mention_events(body, client, say): # pylint: disable=unused-argument
    ''' Reply to @mentions '''
    channel = body['event']['channel']
    speaker_id = body['event']['user']
    speaker_name = get_display_name(speaker_id)
    msg = substitute_names(body['event']['text'])

    if channel not in reminders:
        new_channel(channel)

    say(get_reply(channel, msg, speaker_name, speaker_id))

@app.event("reaction_added")
def handle_reaction_added_events(body, logger): # pylint: disable=unused-argument
    '''
    Handle reactions: post images to Twitter.
    '''
    channel = body['event']['item']['channel']
    try:
        result = app.client.conversations_history(
            channel=channel,
            inclusive=True,
            oldest=body['event']['item']['ts'],
            limit=1
        )

        for msg in result.get('messages', []):
            # only post on the first reaction
            if 'reactions' in msg and len(msg['reactions']) == 1:
                if msg['reactions'][0]['name'] in ['-1', 'hankey', 'no_entry', 'no_entry_sign', 'hand']:
                    if 'blocks' in msg and 'image_url' in msg['blocks'][0]:
                        log.warning("🐦 Not posting:", {msg['reactions'][0]['name']})
                        return
                    log.warning("🤯 All is forgotten.")
                    forget_it(channel)
                    return
                try:
                    req = { "service": SLACK_SERVICE, "channel": channel }
                    response = requests.post(f"{os.environ['INTERACT_SERVER_URL']}/amnesia/", params=req)
                    response.raise_for_status()
                except requests.exceptions.RequestException as err:
                    log.critical(f"🤖 Could not post /amnesia/ to interact: {err}")
                    return

                if 'blocks' in msg and 'image_url' in msg['blocks'][0]:
                    if not BASEURL:
                        log.error("Twitter posting is not enabled in the config.")
                        return

                    blk = msg['blocks'][0]
                    if not blk['image_url'].startswith(BASEURL):
                        log.warning("🐦 Not my image, so not posting it to Twitter.")
                        return
                    try:
                        with tempfile.TemporaryDirectory() as tmpdir:
                            resp = requests.get(blk['image_url'])
                            resp.raise_for_status()
                            fname = f"{tmpdir}/{blk['image_url'].split('/')[-1]}"
                            with open(fname, "wb") as f:
                                for chunk in resp.iter_content():
                                    f.write(chunk)
                            media = twitter.media_upload(fname)
                            if len(blk['alt_text']) > 277:
                                caption = blk['alt_text'][:277] + '...'
                            else:
                                caption = blk['alt_text']
                            twitter.update_status(caption, media_ids=[media.media_id])
                        log.info(f"🐦 Uploaded {blk['image_url']}")
                    except requests.exceptions.RequestException as err:
                        log.error(f"🐦 Could not post {blk['image_url']}: {err}")
                else:
                    log.error(f"🐦 Unhandled reaction {msg['reactions'][0]['name']} to: {msg['text']}")

    except SlackApiError as err:
        log.error(f"Error: {err}")

@app.event("reaction_removed")
def handle_reaction_removed_events(body, logger): # pylint: disable=unused-argument
    ''' Skip for now '''
    log.info("Reaction removed event")

# @app.command("/echo")
# def repeat_text(ack, respond, command):
#     # Acknowledge command request
#     ack()
#     respond(f"_{command['text']}_")


if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    try:
        handler.start()
    # Exit gracefully on ^C (so the wrapper script while loop continues)
    except KeyboardInterrupt as kbderr:
        raise SystemExit(0) from kbderr
