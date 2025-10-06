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
import contextlib
import datetime as dt
import json
import sqlite3
import threading
from typing import Dict, Optional, Set

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
PARTICIPANT_ROLE_IDS  = {int(x) for x in os.getenv("PARTICIPANT_ROLE_IDS", "").split(",") if x.strip().isdigit()}

COUNTDOWN_SECONDS     = int(os.getenv("COUNTDOWN_SECONDS", "60"))         # e.g., 60
TICK_RATE             = float(os.getenv("TICK_RATE", "1.0"))              # seconds between UI updates
TIMEZONE              = os.getenv("TIMEZONE", "Europe/London")            # display only (not required)

# Quiet window (24h HH:MM). If start<end: same day window; if start>end: crosses midnight.
QUIET_START           = os.getenv("QUIET_START", "00:00")
QUIET_END             = os.getenv("QUIET_END", "09:00")

BOT_TOKEN             = os.getenv("DISCORD_BOT_TOKEN", "")
ALERT_AT_SECONDS     = int(os.getenv("ALERT_AT_SECONDS", "10"))
INVITE_BONUS_SECONDS = int(os.getenv("INVITE_BONUS_SECONDS", "10"))
STATE_DB_PATH        = os.getenv("STATE_DB_PATH", "giveaway_state.db")
INVITE_ROLE_BONUS_SECONDS = int(os.getenv("INVITE_ROLE_BONUS_SECONDS", "10"))
# Minimum account age (days) for an invited user to be eligible for any invite bonus
INVITE_MIN_ACCOUNT_AGE_DAYS = int(os.getenv("INVITE_MIN_ACCOUNT_AGE_DAYS", "3"))

# ---------------- Messages (EN - Nox RP) ----------------
BRAND = "Nox RP"
MSG_PREFIX = f"**{BRAND} Giveaway** —"
# Embed styling
EMBED_COLOR = 0xFF8383
EMBED_THUMB_URL = os.getenv("EMBED_THUMB_URL", "https://nox-rp.ir/media/site/icon4.png")

# Bilingual DM messages (FA/EN). For backward compatibility, if
# REGISTRATION_DM_MESSAGE is set, it is used as English content.
REGISTRATION_DM_MESSAGE_EN = os.getenv(
    "REGISTRATION_DM_MESSAGE_EN",
    os.getenv(
        "REGISTRATION_DM_MESSAGE",
        "To participate in the giveaway, please register and complete your profile at https://nox-rp.ir/",
    ),
)
REGISTRATION_DM_MESSAGE_FA = os.getenv(
    "REGISTRATION_DM_MESSAGE_FA",
    "برای شرکت در قرعه‌کشی، لطفاً در وب‌سایت ثبت‌نام کرده و پروفایل خود را تکمیل کنید: https://nox-rp.ir/",
)

# Quiet-hours DM messages (FA/EN)
QUIET_HOURS_MESSAGE_EN = os.getenv(
    "QUIET_HOURS_MESSAGE_EN",
    "The channel is in quiet hours. Please try again later.",
)
QUIET_HOURS_MESSAGE_FA = os.getenv(
    "QUIET_HOURS_MESSAGE_FA",
    "کانال در ساعات سکوت است. لطفاً بعداً تلاش کنید.",
)

def make_embed(title: str, description: str = "", *, fields: Optional[list] = None) -> discord.Embed:
    emb = discord.Embed(title=title, description=description, color=EMBED_COLOR)
    emb.set_author(name=f"{BRAND} Giveaway")
    if EMBED_THUMB_URL:
        emb.set_thumbnail(url=EMBED_THUMB_URL)
    if fields:
        for name, value, inline in fields:
            emb.add_field(name=name, value=value, inline=inline)
    return emb


class StateStore:
    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        with self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )

    def _set(self, key: str, value: Dict):
        payload = json.dumps(value)
        with self._lock, self._conn:
            self._conn.execute(
                "REPLACE INTO kv (key, value) VALUES (?, ?)",
                (key, payload),
            )

    def _get(self, key: str) -> Optional[Dict]:
        with self._lock:
            cursor = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,))
            row = cursor.fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None

    def _delete(self, key: str):
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM kv WHERE key = ?", (key,))

    def save_active_state(
        self,
        *,
        user_id: Optional[int],
        active_until: Optional[str],
        source_msg_id: Optional[int],
        countdown_msg_id: Optional[int],
    ):
        if user_id is None or active_until is None:
            self._delete("active_state")
            return
        self._set(
            "active_state",
            {
                "user_id": user_id,
                "active_until": active_until,
                "source_msg_id": source_msg_id,
                "countdown_message_id": countdown_msg_id,
            },
        )

    def load_active_state(self) -> Optional[Dict]:
        return self._get("active_state")

    def clear_active_state(self):
        self._delete("active_state")

    def save_channel_locked(self, locked: bool):
        self._set("channel_locked", {"locked": bool(locked)})

    def load_channel_locked(self) -> bool:
        data = self._get("channel_locked") or {}
        return bool(data.get("locked", False))

    def save_notified_users(self, user_ids: Set[int]):
        self._set("notified_users", {"ids": sorted(user_ids)})

    def load_notified_users(self) -> Set[int]:
        data = self._get("notified_users") or {}
        ids = data.get("ids", [])
        try:
            return {int(x) for x in ids}
        except (TypeError, ValueError):
            return set()

    def save_referrals(self, referrals: Dict[int, Dict]):
        serializable = {str(k): v for k, v in referrals.items()}
        self._set("referrals", serializable)

    def load_referrals(self) -> Dict[int, Dict]:
        data = self._get("referrals") or {}
        try:
            return {int(k): v for k, v in data.items()}
        except (ValueError, AttributeError):
            return {}

    def save_user_stats(self, stats: Dict[int, Dict]):
        serializable = {str(k): v for k, v in stats.items()}
        self._set("user_stats", serializable)

    def load_user_stats(self) -> Dict[int, Dict]:
        data = self._get("user_stats") or {}
        try:
            return {int(k): v for k, v in data.items()}
        except (ValueError, AttributeError):
            return {}

def _get_user_stats(uid: int) -> Dict:
    s = user_stats.get(uid)
    if not s:
        s = {
            "invites_applied": 0,
            "invite_seconds_applied": 0,
            "role_bonuses_applied": 0,
            "role_seconds_applied": 0,
        }
        user_stats[uid] = s
    return s

def msg_countdown(user: discord.Member, seconds_left: int) -> discord.Embed:
    s = _get_user_stats(user.id)
    inv_applied = int(s.get("invites_applied", 0))
    inv_secs = int(s.get("invite_seconds_applied", 0))
    role_applied = int(s.get("role_bonuses_applied", 0))
    role_secs = int(s.get("role_seconds_applied", 0))
    total_bonus = inv_secs + role_secs
    desc = (
        f"Active participant: {user.mention}\n"
        f"⏳ Remaining: **{seconds_left}s**\n"
        f"Reply to the pinned target message to take over."
    )
    fields = [
        ("Invites Applied", f"{inv_applied} (−{inv_secs}s)", True),
        ("Role Bonuses Applied", f"{role_applied} (−{role_secs}s)", True),
        ("Total Bonus", f"−{total_bonus}s", True),
    ]
    return make_embed("Giveaway Countdown", desc, fields=fields)

def msg_taken_over(new_user: discord.Member) -> discord.Embed:
    return make_embed(
        "New Participant",
        f"{new_user.mention} has taken over. Countdown restarted.",
    )

def msg_deleted_non_reply() -> discord.Embed:
    return make_embed(
        "How To Participate",
        "Please reply to the pinned target message to participate.",
    )

def msg_quiet_hours() -> discord.Embed:
    title = "ساعت سکوت | Quiet Hours"
    desc = f"🇮🇷 {QUIET_HOURS_MESSAGE_FA}\n\n🇬🇧 {QUIET_HOURS_MESSAGE_EN}"
    return make_embed(title, desc)

def msg_winner(user: discord.Member) -> discord.Embed:
    return make_embed(
        "Winner Announced",
        f"🏆 Winner: **{user.display_name}**. The channel is now locked.",
    )

def msg_alert(seconds: int) -> discord.Embed:
    return make_embed(
        "Countdown Alert",
        f"Only **{seconds} seconds** left!",
    )

def msg_registration_dm() -> discord.Embed:
    title = "ثبت‌نام لازم است | Registration Required"
    desc = f"🇮🇷 {REGISTRATION_DM_MESSAGE_FA}\n\n🇬🇧 {REGISTRATION_DM_MESSAGE_EN}"
    return make_embed(title, desc)

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

def has_participant_role(member: discord.Member) -> bool:
    if not PARTICIPANT_ROLE_IDS:
        return True
    return any(r.id in PARTICIPANT_ROLE_IDS for r in member.roles)

# ---------------- Bot Setup ----------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

state_store = StateStore(STATE_DB_PATH)

# Runtime state
active_user_id: Optional[int] = None
active_until: Optional[dt.datetime] = None
active_countdown_msg: Optional[discord.Message] = None
active_countdown_msg_id: Optional[int] = None
active_source_msg_id: Optional[int] = None  # the user's replied message id (should be TARGET_MESSAGE_ID, sanity)
countdown_task: Optional[asyncio.Task] = None
channel_locked_forever: bool = state_store.load_channel_locked()
notified_missing_role: Set[int] = set(state_store.load_notified_users())
invite_uses: Dict[int, Dict[str, int]] = {}
state_restored: bool = False
referral_map: Dict[int, Dict] = {}
user_stats: Dict[int, Dict] = {}

def persist_active_state():
    iso_until = active_until.isoformat() if active_until else None
    state_store.save_active_state(
        user_id=active_user_id,
        active_until=iso_until,
        source_msg_id=active_source_msg_id,
        countdown_msg_id=active_countdown_msg_id,
    )

def persist_notified_users():
    state_store.save_notified_users(notified_missing_role)

def persist_user_stats():
    state_store.save_user_stats(user_stats)

async def lock_channel_permanently(channel: discord.TextChannel):
    global channel_locked_forever
    overwrites = channel.overwrites
    overwrites[channel.guild.default_role] = discord.PermissionOverwrite(send_messages=False)
    await channel.edit(overwrites=overwrites, reason=f"{BRAND} Giveaway: locked after winner declared")
    channel_locked_forever = True
    state_store.save_channel_locked(True)

async def clear_active(skip_cancel: bool = False):
    global active_user_id, active_until, active_countdown_msg, active_source_msg_id, countdown_task, active_countdown_msg_id
    active_user_id = None
    active_until = None
    active_source_msg_id = None
    active_countdown_msg_id = None
    if active_countdown_msg:
        with contextlib.suppress(discord.NotFound, discord.Forbidden):
            await active_countdown_msg.delete()
    active_countdown_msg = None
    if countdown_task and not countdown_task.done():
        if skip_cancel:
            countdown_task = None
        else:
            countdown_task.cancel()
            countdown_task = None
    else:
        countdown_task = None
    state_store.clear_active_state()

def _now_utc_naive() -> dt.datetime:
    return dt.datetime.utcnow()

async def reduce_active_time(inviter: discord.Member, seconds: int):
    global active_until, active_countdown_msg
    if seconds <= 0:
        return

    if active_user_id != inviter.id or not active_until:
        return

    active_until -= dt.timedelta(seconds=seconds)

    now = _now_utc_naive()
    if active_until < now:
        active_until = now

    remaining = int((active_until - now).total_seconds())

    if active_countdown_msg:
        with contextlib.suppress(discord.HTTPException, discord.Forbidden):
            await active_countdown_msg.edit(embed=msg_countdown(inviter, remaining))

    persist_active_state()

async def apply_invite_bonus(inviter: discord.Member, invite_count: int):
    global active_user_id, active_until
    if invite_count <= 0 or INVITE_BONUS_SECONDS <= 0:
        return
    seconds = INVITE_BONUS_SECONDS * invite_count
    did_apply = active_user_id == inviter.id and active_until is not None
    if did_apply:
        await reduce_active_time(inviter, seconds)
        s = _get_user_stats(inviter.id)
        s["invites_applied"] = int(s.get("invites_applied", 0)) + invite_count
        s["invite_seconds_applied"] = int(s.get("invite_seconds_applied", 0)) + seconds
        persist_user_stats()

async def apply_role_bonus(inviter: discord.Member):
    global active_user_id, active_until
    if INVITE_ROLE_BONUS_SECONDS <= 0:
        return
    did_apply = active_user_id == inviter.id and active_until is not None
    if did_apply:
        await reduce_active_time(inviter, INVITE_ROLE_BONUS_SECONDS)
        s = _get_user_stats(inviter.id)
        s["role_bonuses_applied"] = int(s.get("role_bonuses_applied", 0)) + 1
        s["role_seconds_applied"] = int(s.get("role_seconds_applied", 0)) + INVITE_ROLE_BONUS_SECONDS
        persist_user_stats()


async def start_countdown(
    channel: discord.TextChannel,
    participant: discord.Member,
    reply_to: discord.Message,
    *,
    resume_until: Optional[dt.datetime] = None,
    existing_message: Optional[discord.Message] = None,
):
    global active_user_id, active_until, active_countdown_msg, active_source_msg_id, countdown_task, active_countdown_msg_id

    # Cancel previous
    if countdown_task and not countdown_task.done():
        countdown_task.cancel()
    if (
        active_countdown_msg
        and (existing_message is None or active_countdown_msg.id != existing_message.id)
    ):
        with contextlib.suppress(discord.NotFound, discord.Forbidden):
            await active_countdown_msg.delete()

    active_user_id = participant.id
    active_source_msg_id = reply_to.id

    now = dt.datetime.utcnow()
    if resume_until and resume_until > now:
        active_until = resume_until
        initial_remaining = int((resume_until - now).total_seconds())
    else:
        active_until = now + dt.timedelta(seconds=COUNTDOWN_SECONDS)
        initial_remaining = COUNTDOWN_SECONDS

    if existing_message:
        active_countdown_msg = existing_message
        active_countdown_msg_id = existing_message.id
        with contextlib.suppress(discord.HTTPException, discord.Forbidden):
            await active_countdown_msg.edit(
                embed=msg_countdown(participant, initial_remaining)
            )
    else:
        active_countdown_msg = await reply_to.reply(
            embed=msg_countdown(participant, initial_remaining), mention_author=False
        )
        active_countdown_msg_id = active_countdown_msg.id

    persist_active_state()

    async def run_countdown():
        nonlocal participant, channel
        try:
            while True:
                await asyncio.sleep(TICK_RATE)
                if channel_locked_forever:
                    return
                if active_user_id != participant.id:
                    return  # taken over
                now_tick = dt.datetime.utcnow()
                remaining = int((active_until - now_tick).total_seconds()) if active_until else 0
                if remaining == ALERT_AT_SECONDS:
                    alert_msg = f"@here"
                    with contextlib.suppress(discord.Forbidden):
                        await channel.send(content=alert_msg, embed=msg_alert(ALERT_AT_SECONDS))
                if remaining <= 0:
                    # Declare winner and lock channel
                    await channel.send(embed=msg_winner(participant))
                    await lock_channel_permanently(channel)
                    await clear_active(skip_cancel=True)
                    return
                # Update countdown message
                if active_countdown_msg:
                    with contextlib.suppress(discord.HTTPException, discord.Forbidden):
                        await active_countdown_msg.edit(
                            embed=msg_countdown(participant, remaining)
                        )
        except asyncio.CancelledError:
            return

    countdown_task = asyncio.create_task(run_countdown())

async def restore_persisted_state():
    global active_user_id, active_until, active_source_msg_id, active_countdown_msg, active_countdown_msg_id

    if channel_locked_forever:
        state_store.clear_active_state()
        return

    stored = state_store.load_active_state()
    if not stored:
        return

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(CHANNEL_ID)
        except discord.HTTPException:
            return

    if not isinstance(channel, discord.TextChannel):
        return

    try:
        base_msg = await channel.fetch_message(TARGET_MESSAGE_ID)
    except discord.NotFound:
        state_store.clear_active_state()
        return

    user_id = stored.get("user_id")
    if not user_id:
        state_store.clear_active_state()
        return

    participant = channel.guild.get_member(user_id)
    if participant is None:
        try:
            participant = await channel.guild.fetch_member(user_id)
        except (discord.NotFound, discord.HTTPException):
            participant = None
    if participant is None:
        state_store.clear_active_state()
        return

    countdown_msg = None
    countdown_msg_id = stored.get("countdown_message_id")
    if countdown_msg_id:
        with contextlib.suppress(discord.HTTPException, discord.NotFound):
            countdown_msg = await channel.fetch_message(countdown_msg_id)

    try:
        resume_until = dt.datetime.fromisoformat(stored.get("active_until"))
    except (TypeError, ValueError):
        state_store.clear_active_state()
        return

    if resume_until <= dt.datetime.utcnow():
        await channel.send(embed=msg_winner(participant))
        await lock_channel_permanently(channel)
        await clear_active(skip_cancel=True)
        return

    await start_countdown(
        channel,
        participant,
        base_msg,
        resume_until=resume_until,
        existing_message=countdown_msg,
    )

# ---------------- Event Handlers ----------------
@bot.event
async def on_ready():
    global state_restored
    try:
        if GUILD_ID:
            guild = bot.get_guild(GUILD_ID)
            if guild:
                await bot.tree.sync(guild=guild)
        else:
            await bot.tree.sync()
    except Exception:
        pass
    for guild in bot.guilds:
        try:
            invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            invite_uses[guild.id] = {}
        else:
            invite_uses[guild.id] = {invite.code: invite.uses or 0 for invite in invites}

    if not state_restored:
        await restore_persisted_state()
        state_restored = True
    # Load persisted referrals once on ready
    global referral_map
    referral_map = state_store.load_referrals()
    # Load user stats for displaying in countdown
    global user_stats
    user_stats = state_store.load_user_stats()

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

    # Participant role requirement
    if not admin and not has_participant_role(message.author):
        # Send bilingual registration DM every time
        with contextlib.suppress(discord.Forbidden):
            await message.author.send(embed=msg_registration_dm())
        # Small delay so the user reliably sees removal client-side
        await asyncio.sleep(1)
        with contextlib.suppress(discord.Forbidden, discord.NotFound):
            await message.delete()
        return

    # Quiet hours: delete from members having quiet roles (admins exempt)
    if not admin and in_quiet_hours() and has_quiet_role(message.author):
        with contextlib.suppress(discord.Forbidden, discord.NotFound):
            await message.delete()
        with contextlib.suppress(discord.Forbidden):
            await message.author.send(embed=msg_quiet_hours())
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
                warn = await message.channel.send(embed=msg_deleted_non_reply(), delete_after=5)
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
        note = await message.reply(embed=msg_taken_over(message.author), mention_author=False)
        await asyncio.sleep(2)
        with contextlib.suppress(discord.Forbidden, discord.NotFound):
            await note.delete()

@bot.event
async def on_member_join(member: discord.Member):
    try:
        invites = await member.guild.invites()
    except (discord.Forbidden, discord.HTTPException):
        return

    guild_invites = invite_uses.get(member.guild.id, {})
    used_invite = None
    usage_increase = 0
    for invite in invites:
        previous_uses = guild_invites.get(invite.code, 0)
        current_uses = invite.uses or 0
        if current_uses > previous_uses:
            used_invite = invite
            usage_increase = current_uses - previous_uses
            break

    invite_uses[member.guild.id] = {invite.code: invite.uses or 0 for invite in invites}

    if not used_invite or not used_invite.inviter:
        return

    inviter_member = member.guild.get_member(used_invite.inviter.id)
    if not inviter_member:
        return

    # Check minimum account age for eligibility
    try:
        created_at = member.created_at
        if created_at.tzinfo is not None:
            created_at = created_at.replace(tzinfo=None)
    except AttributeError:
        created_at = None

    age_ok = True
    if created_at is not None and INVITE_MIN_ACCOUNT_AGE_DAYS > 0:
        age_ok = (_now_utc_naive() - created_at) >= dt.timedelta(days=INVITE_MIN_ACCOUNT_AGE_DAYS)

    if not age_ok:
        return  # New accounts do not count for any bonus

    # Record referral for potential role-bonus later
    referral_map[member.id] = {
        "inviter_id": inviter_member.id,
        "role_bonus_applied": False,
    }
    state_store.save_referrals(referral_map)

    # Apply join-time invite bonus immediately (if inviter is currently active)
    await apply_invite_bonus(inviter_member, usage_increase)

@bot.event
async def on_invite_create(invite: discord.Invite):
    if not invite.guild:
        return
    guild_invites = invite_uses.setdefault(invite.guild.id, {})
    guild_invites[invite.code] = invite.uses or 0

@bot.event
async def on_invite_delete(invite: discord.Invite):
    if not invite.guild:
        return
    guild_invites = invite_uses.setdefault(invite.guild.id, {})
    guild_invites.pop(invite.code, None)

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    # Detect gaining the participant role later and reward inviter with role bonus
    had_role_before = has_participant_role(before)
    has_role_after = has_participant_role(after)
    if had_role_before or not has_role_after:
        return

    info = referral_map.get(after.id)
    if not info or info.get("role_bonus_applied"):
        return

    inviter_id = info.get("inviter_id")
    if not inviter_id:
        return

    inviter_member = after.guild.get_member(inviter_id)
    if not inviter_member:
        with contextlib.suppress(discord.NotFound, discord.HTTPException):
            inviter_member = await after.guild.fetch_member(inviter_id)
    if not inviter_member:
        return

    await apply_role_bonus(inviter_member)
    applied_now = active_user_id == inviter_member.id and active_until is not None
    if applied_now:
        info["role_bonus_applied"] = True
        state_store.save_referrals(referral_map)

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
    state_store.save_channel_locked(False)
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
