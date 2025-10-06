# (c) 2025 ViraUp (viraup.com) - All rights reserved. | Nox RP Giveaway Bot
# Author: Mohammad (Nox) | ViraUp
#
# Requirements:
#   pip install -U "discord.py>=2.3"
#   Python 3.10+
#
# Behavior Summary:
# - Only replies to a specific target message in a specific channel start/refresh the countdown.
# - Non-reply messages in that channel are deleted (admins exempt).
# - The active participant cannot post in the channel during their countdown (their messages get auto-deleted).
# - New valid reply cancels previous participant, deletes previous countdown message, and restarts the timer.
# - Quiet hours: between QUIET_START and QUIET_END, members with QUIET_ROLE_IDS cannot send messages (their messages get deleted).
# - When the countdown reaches zero with no new reply, the last participant is announced as Winner and the channel is locked permanently.

import os
import asyncio
import datetime as dt
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
load_dotenv()
# ---------------- CONFIG (env-driven) ----------------
GUILD_ID              = int(os.getenv("GUILD_ID", "0"))                   # Optional: set for faster slash sync
CHANNEL_ID            = int(os.getenv("CHANNEL_ID", "0"))                 # Required
TARGET_MESSAGE_ID     = int(os.getenv("TARGET_MESSAGE_ID", "0"))          # Required
ADMIN_ROLE_IDS        = {int(x) for x in os.getenv("ADMIN_ROLE_IDS", "").split(",") if x.strip().isdigit()}
QUIET_ROLE_IDS        = {int(x) for x in os.getenv("QUIET_ROLE_IDS", "").split(",") if x.strip().isdigit()}

COUNTDOWN_SECONDS     = int(os.getenv("COUNTDOWN_SECONDS", "60"))         # e.g., 60
TICK_RATE             = float(os.getenv("TICK_RATE", "1.0"))              # seconds between UI updates
TIMEZONE              = os.getenv("TIMEZONE", "Europe/London")            # display only (not required)

# Quiet window (24h HH:MM). If start<end: same day window; if start>end: crosses midnight.
QUIET_START           = os.getenv("QUIET_START", "00:00")
QUIET_END             = os.getenv("QUIET_END", "09:00")

BOT_TOKEN             = os.getenv("DISCORD_BOT_TOKEN", "")
ALERT_AT_SECONDS     = int(os.getenv("ALERT_AT_SECONDS", "10"))

# ---------------- Messages (EN - Nox RP) ----------------
BRAND = "Nox RP"
MSG_PREFIX = f"**{BRAND} Giveaway** —"

def msg_countdown(user: discord.Member, seconds_left: int) -> str:
    return (
        f"{MSG_PREFIX} countdown running for {user.mention}.\n"
        f"⏳ **{seconds_left}s** remaining... Reply to the target message to take over!"
    )

def msg_taken_over(new_user: discord.Member) -> str:
    return f"{MSG_PREFIX} new participant: {new_user.mention}. Countdown restarted."

def msg_deleted_non_reply() -> str:
    return f"{MSG_PREFIX} please reply to the pinned target message to participate."

def msg_quiet_hours() -> str:
    return f"{MSG_PREFIX} channel is in quiet hours. Please try again later."

def msg_winner(user: discord.Member) -> str:
    return f"🏆 {MSG_PREFIX} winner: **{user.display_name}**! The channel is now locked."

# ---------------- Helpers ----------------
def _parse_hhmm(s: str) -> dt.time:
    hh, mm = s.strip().split(":")
    return dt.time(int(hh), int(mm), 0)

Q_START = _parse_hhmm(QUIET_START)
Q_END   = _parse_hhmm(QUIET_END)

def in_quiet_hours(now: Optional[dt.datetime] = None) -> bool:
    now = now or dt.datetime.utcnow()
    t = now.time()
    if Q_START < Q_END:
        return Q_START <= t < Q_END
    else:
        # crosses midnight (e.g., 23:00 -> 07:00)
        return t >= Q_START or t < Q_END

def is_admin(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(r.id in ADMIN_ROLE_IDS for r in member.roles)

def has_quiet_role(member: discord.Member) -> bool:
    return any(r.id in QUIET_ROLE_IDS for r in member.roles)

# ---------------- Bot Setup ----------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Runtime state
active_user_id: Optional[int] = None
active_until: Optional[dt.datetime] = None
active_countdown_msg: Optional[discord.Message] = None
active_source_msg_id: Optional[int] = None  # the user's replied message id (should be TARGET_MESSAGE_ID, sanity)
countdown_task: Optional[asyncio.Task] = None
channel_locked_forever: bool = False

async def lock_channel_permanently(channel: discord.TextChannel):
    global channel_locked_forever
    overwrites = channel.overwrites
    overwrites[channel.guild.default_role] = discord.PermissionOverwrite(send_messages=False)
    await channel.edit(overwrites=overwrites, reason=f"{BRAND} Giveaway: locked after winner declared")
    channel_locked_forever = True

async def clear_active():
    global active_user_id, active_until, active_countdown_msg, active_source_msg_id, countdown_task
    active_user_id = None
    active_until = None
    active_source_msg_id = None
    if active_countdown_msg:
        with contextlib.suppress(discord.NotFound, discord.Forbidden):
            await active_countdown_msg.delete()
    active_countdown_msg = None
    if countdown_task and not countdown_task.done():
        countdown_task.cancel()
    countdown_task = None

import contextlib

async def start_countdown(channel: discord.TextChannel, participant: discord.Member, reply_to: discord.Message):
    global active_user_id, active_until, active_countdown_msg, active_source_msg_id, countdown_task

    # Cancel previous
    if countdown_task and not countdown_task.done():
        countdown_task.cancel()
    if active_countdown_msg:
        with contextlib.suppress(discord.NotFound, discord.Forbidden):
            await active_countdown_msg.delete()

    active_user_id = participant.id
    active_source_msg_id = reply_to.id
    active_until = dt.datetime.utcnow() + dt.timedelta(seconds=COUNTDOWN_SECONDS)

    active_countdown_msg = await reply_to.reply(msg_countdown(participant, COUNTDOWN_SECONDS), mention_author=False)

    async def run_countdown():
        nonlocal participant, channel
        try:
            while True:
                await asyncio.sleep(TICK_RATE)
                if channel_locked_forever:
                    return
                if active_user_id != participant.id:
                    return  # taken over
                now = dt.datetime.utcnow()
                remaining = int((active_until - now).total_seconds()) if active_until else 0
                if remaining == ALERT_AT_SECONDS:
                    alert_msg = f"⚠️ {MSG_PREFIX} only **{ALERT_AT_SECONDS} seconds** left! @here"
                    with contextlib.suppress(discord.Forbidden):
                        await channel.send(alert_msg)
                if remaining <= 0:
                    # Declare winner and lock channel
                    await channel.send(msg_winner(participant))
                    await lock_channel_permanently(channel)
                    return
                # Update countdown message
                with contextlib.suppress(discord.HTTPException, discord.Forbidden):
                    await active_countdown_msg.edit(content=msg_countdown(participant, remaining))
        except asyncio.CancelledError:
            return

    countdown_task = asyncio.create_task(run_countdown())

# ---------------- Event Handlers ----------------
@bot.event
async def on_ready():
    try:
        if GUILD_ID:
            guild = bot.get_guild(GUILD_ID)
            if guild:
                await bot.tree.sync(guild=guild)
        else:
            await bot.tree.sync()
    except Exception:
        pass
    print(f"[{BRAND}] Giveaway bot is online as {bot.user}.")

@bot.event
async def on_message(message: discord.Message):
    global active_user_id, active_until, active_countdown_msg, channel_locked_forever

    # Ignore bot/self
    if message.author.bot:
        return

    # Only target channel
    if message.channel.id != CHANNEL_ID:
        return

    # If permanently locked, delete any message from non-admins
    if channel_locked_forever and not is_admin(message.author):
        with contextlib.suppress(discord.Forbidden, discord.NotFound):
            await message.delete()
        return

    # Admins are exempt from all restrictions (but still can interact)
    admin = is_admin(message.author)

    # Quiet hours: delete from members having quiet roles (admins exempt)
    if not admin and in_quiet_hours() and has_quiet_role(message.author):
        with contextlib.suppress(discord.Forbidden, discord.NotFound):
            await message.delete()
        with contextlib.suppress(discord.Forbidden):
            await message.author.send(msg_quiet_hours())
        return

    # Must be a REPLY to the configured target message
    is_valid_reply = (
        message.reference is not None and
        message.reference.message_id == TARGET_MESSAGE_ID
    )

    if not is_valid_reply:
        # Delete non-replies (admins exempt)
        if not admin:
            with contextlib.suppress(discord.Forbidden, discord.NotFound):
                await message.delete()
            # Optionally nudge (avoid DM spam by replying ephemerally—Discord bots can't true-ephemeral in text channels)
            with contextlib.suppress(discord.Forbidden):
                warn = await message.channel.send(msg_deleted_non_reply(), delete_after=5)
        return

    # If current participant tries to speak during their own countdown, delete their message
    if active_user_id == message.author.id and active_until and dt.datetime.utcnow() < active_until:
        if not admin:
            with contextlib.suppress(discord.Forbidden, discord.NotFound):
                await message.delete()
        return

    # Start/transfer countdown to this user
    # Fetch the target message to reply under (ensures object exists)
    try:
        base_msg = await message.channel.fetch_message(TARGET_MESSAGE_ID)
    except discord.NotFound:
        # If target missing, ignore gracefully
        return

    await start_countdown(message.channel, message.author, base_msg)
    # Optional short confirmation
    with contextlib.suppress(discord.Forbidden):
        note = await message.reply(msg_taken_over(message.author), mention_author=False)
        await asyncio.sleep(2)
        with contextlib.suppress(discord.Forbidden, discord.NotFound):
            await note.delete()

# ---------------- Admin Slash: /unlock (optional safeguard) ----------------
# Keeps things simple: we DON'T reopen automatically after winner.
# But admins can unlock manually if they ever need to.
@bot.tree.command(name="unlock", description="(Admin) Unlock the giveaway channel manually.")
@app_commands.checks.has_permissions(administrator=True)
async def unlock(interaction: discord.Interaction):
    global channel_locked_forever
    if interaction.channel.id != CHANNEL_ID:
        await interaction.response.send_message("Use this in the giveaway channel.", ephemeral=True)
        return
    overwrites = interaction.channel.overwrites
    overwrites[interaction.guild.default_role] = discord.PermissionOverwrite(send_messages=True)
    await interaction.channel.edit(overwrites=overwrites, reason=f"{BRAND} Admin unlock")
    channel_locked_forever = False
    await interaction.response.send_message(f"{MSG_PREFIX} channel unlocked by admin.", ephemeral=True)

# ---------------- Main ----------------
def _validate_env():
    missing = []
    if not BOT_TOKEN:
        missing.append("DISCORD_BOT_TOKEN")
    if not CHANNEL_ID:
        missing.append("CHANNEL_ID")
    if not TARGET_MESSAGE_ID:
        missing.append("TARGET_MESSAGE_ID")
    if missing:
        raise SystemExit(f"Missing required env vars: {', '.join(missing)}")

if __name__ == "__main__":
    _validate_env()
    bot.run(BOT_TOKEN)
