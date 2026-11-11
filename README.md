# Jarvis v2 🤵

A Gemini-first Discord bot that runs your whole server through natural language.
Instead of a fixed command menu, Jarvis exposes a large registry of audited
actions (moderation, channels, roles, messages, community features, voice,
games, and more) to an AI agent. You talk; it plans and executes — with
permission checks, confirmations, and approvals around anything destructive.

Jarvis is a from-scratch rewrite of the v1 bot. v1 inspired the tone; none of
its command structure is required to use v2.

```
"hey jarvis, lock #general and tell everyone why"
"jarvis, give the Helper role to everyone who reacted to the rules message"
"oi jarvis, start a blackjack game"
"jarvis, remute the guy who keeps spamming and log it"
```

---

## Highlights

- **Gemini-first, never Gemini-only.** Automatic failover across every
  configured provider and model. When a model is rate-limited, missing, or
  hanging, Jarvis transparently switches to the next one — even a different
  provider.
- **25 tool groups / 246 actions.** One coherent action registry with per-action
  risk levels, Discord permission checks, confirmation prompts, moderator
  approval, and audit logging.
- **1,379 wake phrases.** Built from greetings, names, titles, and suffixes, plus
  mentions, replies, and special phrases.
- **14 minigames**, all interactive in-channel: Rock Paper Scissors, Coin Flip,
  Magic 8-Ball, Dice, Slots, Trivia, Hangman, Wordle, Tic-Tac-Toe, Connect Four,
  Blackjack, Higher or Lower, Memory, and a Reaction Test.
- **Persistent community systems**: welcome/goodbye, autorole, autoresponses,
  logging, leveling, reaction roles, starboard, temp voice, reminders,
  giveaways, scheduled unbans, and stat counters.
- **Discord channel storage** for warnings, notes, config, reminders, giveaways,
  tickets, levels, economy, reaction roles, scheduled actions, game stats,
  starboard, and audit history. Jarvis creates a private category and one
  channel per data domain, uploads JSON snapshots, and deletes the previous
  snapshot only after the new one is safely stored.
- **Slash commands are optional shortcuts**, not the interface: `/ask`, `/help`,
  `/status`, `/ping`, `/game`, `/model`, `/phrases`.

---

## Requirements

- Python **3.10+**
- `ffmpeg` on `PATH` for music/voice playback (optional, but recommended)
- A Discord application with a bot token
- At least one AI provider key (Gemini recommended)

Install dependencies:

```bash
cd v2
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Optional voice/music extras:

```bash
pip install PyNaCl yt-dlp
# and install ffmpeg with your package manager
```

---

## Discord setup

1. Create an application at <https://discord.com/developers/applications>.
2. **Bot → Privileged Gateway Intents**: enable **Message Content** and
   **Server Members**. Enable **Presence** only if you set
   `ENABLE_PRESENCES=true`.
3. Invite the bot with the `bot` and `applications.commands` scopes. Grant
   `Administrator` for the full feature set, or the specific permissions you
   want it to use.
4. Put Jarvis's role above any roles it must manage.

---

## Configuration

Copy the template and edit it:

```bash
cp .env.example .env
$EDITOR .env
```

Real environment variables always win. Jarvis also loads `.env` from the
project root and the legacy `v1/ai/gemini/.env`; the token can come from
`v2/token.txt` or `v1/token.txt`, and `GUILD_ID` from `serverID.txt`.

### Minimum

```dotenv
DISCORD_TOKEN=...          # or token.txt
GUILD_ID=...               # optional guild-scoped command sync
GEMINI_API_KEY=...         # or inherit from the environment / v1 config
```

### Recommended Gemini settings

```dotenv
GEMINI_MODELS=gemini-3.5-flash,gemini-3.5-flash-lite
GEMINI_AUTO_DISCOVER=true
AI_REQUEST_TIMEOUT=45
```

Older model IDs such as `gemini-2.5-flash` may be retired or return 404. The
router automatically cools down bad models and keeps moving through the chain;
auto-discovery appends newly available Gemini models as extra failover capacity.
`GEMINI_MODEL` (singular) is still accepted as a legacy alias for one model.

### Models that balance latency and cost

- Gemini flash/lite models accept `thinkingBudget=0` (or no special config for
  lite), which keeps responses fast. Jarvis sends the right field per model.
- Pro/Gemma models do not accept `thinkingBudget`; Jarvis leaves them alone.
- Models that spend their entire budget on hidden reasoning and return an empty
  `MAX_TOKENS` response are treated as failed so another model can answer.

### Selecting providers

Any provider with a key is enabled automatically. Precedence is controlled by
`AI_PROVIDER_ORDER`; the full model order can be pinned with `AI_MODEL_CHAIN`.

```dotenv
AI_PROVIDER_ORDER=gemini,openrouter,groq,openai,anthropic,mistral,deepseek,together,xai,ollama,lmstudio
# Explicit chain (provider:model, comma separated):
# AI_MODEL_CHAIN=gemini:gemini-3.5-flash,openrouter:google/gemini-2.5-flash,ollama:llama3.2
```

Supported provider presets:

| Provider | Kind | Key variable |
| --- | --- | --- |
| Gemini | `gemini` | `GEMINI_API_KEY` |
| OpenRouter | OpenAI-compatible | `OPENROUTER_API_KEY` |
| Groq | OpenAI-compatible | `GROQ_API_KEY` |
| OpenAI | OpenAI-compatible | `OPENAI_API_KEY` |
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` |
| Mistral | OpenAI-compatible | `MISTRAL_API_KEY` |
| DeepSeek | OpenAI-compatible | `DEEPSEEK_API_KEY` |
| Together | OpenAI-compatible | `TOGETHER_API_KEY` |
| xAI | OpenAI-compatible | `XAI_API_KEY` |
| Ollama | OpenAI-compatible | `OLLAMA_ENABLED=true` |
| LM Studio | OpenAI-compatible | `LMSTUDIO_ENABLED=true` |
| Custom / extra | OpenAI-compatible or Anthropic | `CUSTOM_*` or `PROVIDERS_JSON` |

Custom endpoint:

```dotenv
CUSTOM_NAME=nvidia
CUSTOM_BASE_URL=https://integrate.api.nvidia.com/v1
CUSTOM_API_KEY=...
CUSTOM_MODELS=meta/llama-3.1-70b-instruct
```

Multiple extra providers:

```dotenv
PROVIDERS_JSON=[{"name":"nvidia","kind":"openai","base_url":"https://integrate.api.nvidia.com/v1","api_key":"...","models":["meta/llama-3.1-70b-instruct"]}]
```

### Failover behaviour

- Healthy model is remembered and reused.
- 429/quota → 15-minute cooldown for that model, then the next model/provider.
- 404/unknown model → 6-hour cooldown.
- 5xx/network/timeout → progressive cooldown (max 10 minutes).
- Invalid key/permission → provider disabled for 24 hours; others continue.
- Total failure does **not** shut the bot down by default; it reports the
  failure, retries on the next message, and recovers automatically.
- `/status` shows the chain, the current model, cooldowns, and last errors.
- `/model <provider> <model>` pins a model for managers/owners.

---

## Waking Jarvis

Call it however feels natural. Examples:

```
jarvis
hey jarvis
oi jarvis
hello there jarvis
mr jarvis
jarvis, please
good morning jarvis
wake up jarvis
```

Jarvis also responds to:

- an `@mention`
- a reply to one of its messages
- any of **1,379** generated phrases (`/phrases` lists a sample)

A wake phrase starts a short conversational session. Once engaged, follow-ups can
omit the name until `JARVIS_SESSION_SECONDS` expires. Set
`AUTO_ACTION_WITHOUT_WAKE=false` if you want actions to require an explicit wake
word or mention every time.

---

## Minigames

Start with `/game` (blank for random) or just ask:

```
jarvis, play wordle
jarvis, blackjack
jarvis, start a reaction test
```

| Game | Key |
| --- | --- |
| 🪨 Rock Paper Scissors | `rps` |
| 🪙 Coin Flip | `coinflip` |
| 🎱 Magic 8-Ball | `8ball` |
| 🎲 Dice | `dice` |
| 🎰 Slots | `slots` |
| ❓ Trivia | `trivia` |
| 🪢 Hangman | `hangman` |
| 🟩 Wordle | `wordle` |
| ⭕ Tic-Tac-Toe | `tictactoe` |
| 🔴 Connect Four | `connect4` |
| 🃏 Blackjack | `blackjack` |
| ⬆️ Higher or Lower | `higherlower` |
| 🧠 Memory | `memory` |
| ⚡ Reaction Test | `reaction` |

Games use Discord UI buttons and menus, work in channels or private game
channels, and time out after `GAME_IDLE_TIMEOUT` seconds.

---

## What Jarvis can do

The natural-language agent can call any of these tool groups

| Group | Capabilities |
| --- | --- |
| `manage_member` | info, warn/notes, timeout, kick, ban, softban, tempban, unban, nickname, role add/remove, DM, voice mute/deafen/move |
| `manage_channel` | create/delete/rename/move/clone, lock/unlock, slowmode, topic, NSFW, bitrate, permission overwrites, sync |
| `manage_category` | create/delete/rename categories and reorder them |
| `manage_thread` | create, delete, rename, lock, archive, add/remove members |
| `manage_roles` | list/info/members, create, delete, rename, colour, hoist, mentionable, permissions, position |
| `manage_server` | server info/settings, icon/banner/splash, verification, AFK, system/rules/updates channels, notifications, locale, widget |
| `manage_invites` | create/list/delete invites |
| `manage_webhooks` | create/edit/delete webhooks and send messages through them |
| `manage_expressions` | emoji/sticker creation, rename, delete |
| `manage_events` | scheduled events |
| `manage_automod` | automod rules and keyword filters |
| `manage_message` | send, reply, edit, delete, purge, pin, react, embed, poll, download |
| `bulk_message` | clear reactions, delete a user's recent messages, forward messages |
| `manage_community` | welcome/goodbye, logging, autoroles, autoresponses, reaction roles, starboard, counters, temp voice |
| `manage_tickets` | panels, open/claim/close/add/remove/transcript |
| `manage_leveling` | rank, leaderboard, XP config, reset, role rewards |
| `manage_economy` | balance, daily, work, pay, gamble, leaderboard, admin set/add/reset |
| `manage_giveaway` | create, end, reroll, cancel, entries |
| `manage_reminder` | create, list, cancel, clear |
| `manage_voice` | join/leave, play/search/queue, skip/stop/pause, volume, loop, shuffle |
| `play_game` | launch any minigame and record results |
| `game_stats` | per-user stats and leaderboards |
| `get_info` | members, channels, roles, server, avatar/banner, emojis, boosters, bans, invites, audit log, permissions, messages |
| `utility` | ping, uptime, time, math, encoding, passwords, UUIDs, weather, define, search, translate, summarize, rewrite, explain |

---

## Permissions, confirmations, and audit

Every action declares a risk level:

| Risk | Who can run it without approval |
| --- | --- |
| 🟢 Safe | everyone |
| 🔵 Low | a member |
| 🟡 Moderate | a moderator |
| 🟠 High | an administrator |
| 🔴 Critical | the server owner |

- The requester must have the matching Discord permission (or be owner/admin).
- Destructive actions ask for an explicit confirmation first (buttons or a plain
  “yes”).
- If the requester lacks permission, a qualified moderator/administrator can
  approve the request instead of executing it immediately.
- Every action is written to the audit table. If `LOG_CHANNEL_ID` is set, a
  human-readable copy is posted there too.
- Jarvis never grants itself permissions or acts outside its own Discord role
  hierarchy.

Useful optional IDs:

```dotenv
LOG_CHANNEL_ID=...
APPROVAL_CHANNEL_ID=...
MOD_ROLE_ID=...
ADMIN_ROLE_ID=...
GAME_CATEGORY_ID=...
OWNER_IDS=...
```

---

## Slash commands

| Command | Purpose |
| --- | --- |
| `/ask <prompt>` | Talk to the agent directly. |
| `/help` | List tool groups, games, and commands. |
| `/status` | Uptime, latency, model chain, cooldowns. |
| `/ping` | Gateway and round-trip latency. |
| `/game [game]` | Start a minigame. |
| `/model [provider] [model]` | Show health or pin a model. |
| `/phrases` | Show wake-phrase examples and games. |

Guild commands sync on startup. Set `GUILD_ID` for instant sync in one server;
leave it blank for global sync (which Discord can take a while to propagate).

---

## Running

```bash
cd v2
python run.py
```

or:

```bash
python -m jarvis
```

Logs go to stdout. `LOG_LEVEL=DEBUG` adds provider and router detail. No local
database is created: data is stored in a private Discord category named
`Jarvis Data` (configurable with `JARVIS_DATA_CATEGORY`). If an old
`DATABASE_PATH` SQLite file exists, it is read once on first startup to migrate
existing data into channels and then never written again.

Keep it running with systemd, Docker, or your process manager of choice. Example
systemd unit:

```ini
[Unit]
Description=Jarvis v2 Discord bot
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/laurence/Projects/Jarvis/v2
ExecStart=/home/laurence/Projects/Jarvis/v2/.venv/bin/python run.py
Restart=always
RestartSec=5
EnvironmentFile=/home/laurence/Projects/Jarvis/v2/.env

[Install]
WantedBy=multi-user.target
```

---

## Data storage (Discord channels)

Jarvis does not keep a local database. It creates a private category in each
server and lazily creates one text channel per data domain the first time that
domain is used:

| Channel | Holds |
| --- | --- |
| `#jarvis-levels` | XP, message counts, and last-XP timestamps |
| `#jarvis-economy` | balances, daily/work cooldowns |
| `#jarvis-warnings` / `#jarvis-notes` | moderation records |
| `#jarvis-config` | welcome/logging/leveling/ticket/starboard settings |
| `#jarvis-autoresponses` | trigger/reply rules |
| `#jarvis-reaction-roles` | reaction-role mappings |
| `#jarvis-reminders` | personal reminders |
| `#jarvis-giveaways` / `#jarvis-giveaway-entries` | giveaways and entries |
| `#jarvis-tickets` | ticket state |
| `#jarvis-scheduled-actions` | timed unbans and similar jobs |
| `#jarvis-game-stats` | minigame records |
| `#jarvis-audit` | action audit history |
| `#jarvis-starboard` | starboard message mappings |

The newest message in each channel is a JSON snapshot attachment. The update
sequence is deliberately crash-safe:

1. Jarvis updates the in-memory record (for example a user's XP/level).
2. It uploads a complete new snapshot of that domain to the matching channel.
3. Only after the upload succeeds does it delete the previous snapshot.

So if Discord is unavailable mid-update, the old snapshot is still there. On
startup Jarvis reads the snapshots back into memory, which is what the AI-facing
actions query. The AI never manages channels itself: it calls the normal
`manage_leveling`, `manage_economy`, etc. actions, and the hard-coded store does
the lookup and persistence.

Useful storage settings:

```dotenv
JARVIS_DATA_CATEGORY=Jarvis Data
JARVIS_DATA_FLUSH_SECONDS=2
```

`JARVIS_DATA_FLUSH_SECONDS` coalesces bursts (for example XP from a busy chat)
into one snapshot upload. Ask Jarvis to `manage_storage status` to see the
channels and record counts, `setup` to create every channel, or `refresh` to
re-read them from Discord. The category denies `@everyone` by default; Jarvis's
own role is granted access.

---

## Architecture

```
v2/
├── run.py                    # entry point
├── jarvis/
│   ├── bot.py                # client lifecycle, message intake, audit
│   ├── config.py             # env, provider presets, legacy secret lookup
│   ├── triggers.py           # wake-phrase generation and matching
│   ├── memory.py             # per-channel conversation memory
│   ├── context.py            # ActionContext shared by chat + slash
│   ├── permissions.py        # risk → permission policy
│   ├── confirmations.py      # confirm / approve buttons and chat replies
│   ├── runner.py             # validates and executes registered actions
│   ├── database.py           # Discord channel-backed data store
│   ├── slash.py              # optional slash commands
│   ├── ai/
│   │   ├── agent.py          # tool-calling loop
│   │   ├── router.py         # model discovery, health, failover
│   │   ├── prompt.py         # system prompt
│   │   ├── tools.py          # action schemas / registry
│   │   └── providers/        # gemini, openai-compatible, anthropic
│   ├── actions/              # member, structure, messages, server, …
│   ├── minigames/            # registry + 14 interactive games
│   └── features/             # passive event features and periodic jobs
└── requirements.txt
```

### Design notes

- **Natural language is the interface.** Slash commands are thin wrappers around
  the same `ActionContext`; new capabilities only need to be registered once.
- **Providers are raw REST adapters** (no vendor SDKs), so adding a provider is a
  small config entry or one class.
- **Failover is per request.** The router holds a lock around generation so two
  requests cannot race the current-model pointer, while health state persists
  across requests.
- **State lives in Discord channels**, so giveaways, tickets, reminders,
  reaction roles, and persistent views survive restarts. The in-memory index is
  hydrated from the channels on startup; writes are coalesced and uploaded as
  JSON snapshots by a background flusher.
- **Persistent views use stable custom IDs** (`jarvis:ticket:*`,
  `jarvis:giveaway:<id>:enter`) and are re-registered on startup.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `No Discord token found` | Set `DISCORD_TOKEN` or create `v2/token.txt`. |
| `No AI provider configured` | Set `GEMINI_API_KEY` or another provider key. |
| Bot ignores messages | Enable **Message Content** intent; use a wake phrase or mention. |
| `PrivilegedIntentsRequired` | Enable **Server Members** intent (and Presence if enabled). |
| Commands missing | Set `GUILD_ID` and restart; allow time for global sync. |
| A model always times out | It is cooled down automatically; check `/status`. Pin a known-good model with `/model`. |
| `404 model not found` | The model ID was retired; remove it from `GEMINI_MODELS`. |
| Voice won't play | Install `ffmpeg`, `PyNaCl`, and optionally `yt-dlp`. |
| Empty replies | Router treats empty `MAX_TOKENS` responses as failures and tries the next model. |
| Downloads/uploads fail | Large assets may exceed Discord limits; Jarvis reports the error rather than silently failing. |
| Data-store channels missing | Give Jarvis **Manage Channels**, **Manage Messages**, and **Attach Files**, or ask it to run `manage_storage setup`. |
| Old levels/economy disappeared | Keep `jarvis.db` beside the bot for the first start so it can migrate; after migration the file is no longer used. |

---

## License / usage

Private project. Use it on servers you administer. Never commit `.env`,
`token.txt`, or API keys — `.gitignore` already excludes them.
