# 🎁 Nox RP Discord Giveaway Bot  
(c) 2025 ViraUp (viraup.com) – All rights reserved.  

A Discord giveaway bot for Nox RP written in Python.
It manages countdown-based reply giveaways with quiet hours, admin exemptions, and automatic locking on winner selection.
Countdown progress is stored in a local SQLite database so the giveaway can recover after unexpected restarts.
The countdown message also shows the active participant's invite- and role-bonus stats (applied only).

## 🔧 Setup
```bash
pip install -r requirements.txt
cp .env.example .env
python main.py
```

### Environment variables

| Variable | Description |
| --- | --- |
| `DISCORD_BOT_TOKEN` | Bot token (required). |
| `CHANNEL_ID` | Giveaway text channel ID (required). |
| `TARGET_MESSAGE_ID` | ID of the message users must reply to (required). |
| `ADMIN_ROLE_IDS` | Comma-separated role IDs treated as admins. |
| `QUIET_ROLE_IDS` | Roles muted during quiet hours. |
| `PARTICIPANT_ROLE_IDS` | Comma-separated role IDs allowed to participate; others receive a registration DM. Leave empty to allow everyone. |
| `COUNTDOWN_SECONDS` | Countdown duration for each participant. |
| `INVITE_BONUS_SECONDS` | Seconds removed from the countdown per successful invite (default `10`). |
| `REGISTRATION_DM_MESSAGE` | DM text sent to users without participant role (default English message provided). |
| `EMBED_THUMB_URL` | URL of thumbnail displayed in embeds (default Nox RP icon). |
| `STATE_DB_PATH` | Path to the local SQLite database used to persist giveaway progress (default `giveaway_state.db`). |
| `INVITE_ROLE_BONUS_SECONDS` | Extra seconds removed when an invited user later gains a participant role. |
| `INVITE_MIN_ACCOUNT_AGE_DAYS` | Minimum account age (days) for an invited user to be eligible for any bonus. |

### Permissions & Intents

- Enable `Message Content Intent` and `Server Members Intent` for the bot in the Developer Portal.
- Grant the bot permission to view the giveaway channel, manage messages, and fetch invites (`Manage Guild` or appropriate invite permissions) so invite bonuses work.
- The role-bonus feature relies on `on_member_update` to detect when an invited user later receives a participant role.
