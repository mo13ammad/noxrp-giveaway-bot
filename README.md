# 🎁 Nox RP Discord Giveaway Bot  
(c) 2025 ViraUp (viraup.com) – All rights reserved.  

A Discord giveaway bot for Nox RP written in Python.  
It manages countdown-based reply giveaways with quiet hours, admin exemptions, and automatic locking on winner selection.

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
