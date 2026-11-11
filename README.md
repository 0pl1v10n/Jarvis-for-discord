# Jarvis

**An AI-powered Discord assistant designed to manage, automate, and interact with Discord servers through natural language.**

Jarvis is a butler-style Discord bot that allows users to communicate with their server through ordinary conversation rather than relying on traditional command interfaces.

Powered by AI, Jarvis interprets user requests, selects from a registry of predefined actions, and executes them through Discord's API. From moderation and server management to community automation and interactive games, Jarvis aims to make Discord administration more accessible and intuitive.

Instead of remembering commands, you simply ask.

```text
"Hey Jarvis, ban user2. He's been spamming."

"Jarvis, give the Helper role to everyone who reacted to the rules message."

"Jarvis, lock #general and tell everyone why."

"Jarvis, start a game of blackjack."
```

Jarvis handles the interpretation, validation, permissions, and execution.

---

## Overview

Jarvis is built around a few core principles:

- **Natural-language interaction:** Communicate with Jarvis using ordinary language.
- **AI-driven execution:** An AI agent interprets requests and selects the appropriate actions.
- **Structured architecture:** Capabilities are organised into reusable tool groups and registered actions.
- **Permission enforcement:** Every action is validated against predefined security policies and Discord permissions.
- **Controlled execution:** Destructive and privileged operations are protected by confirmations and approval workflows.
- **Extensibility:** New functionality can be introduced without rebuilding the entire AI integration.

Jarvis is an evolving project. Each major release introduces improvements to its architecture, capabilities, and overall functionality.

---

## Versions

Jarvis is developed through major releases. Each version represents a significant stage in the project's development.

| Version | Status | Description |
|---|---|---|
| v1 | Released | Original Gemini-powered conversational Discord bot. |
| v2 | Released | Complete rewrite featuring a modular architecture, multiple AI providers, 246 actions, and persistent community systems. |
| v3 | Planned | Future development. |
| v4 | Planned | Future development. |
| v5 | Planned | Future development. |

### v1 — Original Implementation

The first version of Jarvis introduced the concept of managing a Discord server entirely through natural language.

Rather than exposing traditional slash commands, v1 used Google Gemini as its primary interface. The AI interpreted requests and selected from approximately 27 predefined functions implemented in Python.

**Key features:**

- Conversational butler-style AI.
- Moderation and member management.
- Role and channel management.
- Fuzzy name matching for members, roles, and channels.
- Webhook-based impersonation.
- Interactive minigames.
- Server setup automation.
- Permission checks and approval workflows.
- Automatic Gemini model failover.
- Optional media re-embedding functionality.

v1 established the foundation for the project and demonstrated how AI could serve as a natural-language interface for Discord administration.

### v2 — Complete Rewrite

v2 is a complete rewrite of the original implementation.

While v1 established the concept, v2 introduced a substantially more extensive architecture, expanding Jarvis into a general-purpose Discord management and automation system.

**Key features:**

- 25 tool groups containing 246 registered actions.
- 1,379 generated wake phrases.
- Multiple AI provider integrations.
- Automatic cross-provider model failover.
- 14 interactive minigames.
- Persistent community management systems.
- Discord channel-backed data storage.
- Moderation, tickets, leveling, and economy systems.
- Reaction roles, starboard, giveaways, and reminders.
- Temporary voice channels.
- Optional slash commands.
- Centralised permission enforcement and audit logging.

Unlike v1, v2 is not exclusively dependent on Google Gemini. Multiple providers can be configured, with Gemini remaining the recommended starting point.

---

## Architecture

Jarvis separates AI interpretation from action execution.

The AI agent is responsible for understanding user intent and selecting appropriate actions. The actual execution is handled by predefined Python implementations.

The general execution process is:

1. **Message intake:** Jarvis receives a message through Discord.
2. **Intent interpretation:** The message and relevant conversation context are passed to the configured AI provider.
3. **Action selection:** The AI determines whether a conversational response or registered action is appropriate.
4. **Validation:** Jarvis validates arguments, permissions, risk levels, and execution requirements.
5. **Approval:** If necessary, Jarvis requests confirmation or approval from an authorised user.
6. **Execution:** The validated action is executed through the appropriate Discord API operation.
7. **Response:** Jarvis reports the result back to the conversation.

The AI does not independently enforce permissions or execute arbitrary administrative operations.

All actions pass through the application's execution layer.

### Core Components

| Component | Responsibility |
|---|---|
| AI Agent | Interprets requests and selects registered actions. |
| Provider Router | Handles provider selection, model health, and automatic failover. |
| Action Registry | Defines available actions, schemas, and tool groups. |
| Action Runner | Validates and executes registered actions. |
| Permission System | Enforces permission requirements and risk policies. |
| Confirmation System | Handles confirmations and approval workflows. |
| Data Storage | Manages persistent application and server data. |
| Features | Handles automated events and background tasks. |
| Minigames | Provides interactive games through Discord components. |

The architecture is intended to make new functionality easier to introduce without duplicating the underlying AI integration.

---

## Security and Permissions

Security is an important part of Jarvis's design.

AI-generated requests are never treated as automatically authorised operations. Every action is subject to application-level validation before execution.

Depending on the action and its risk level, execution may require:

| Risk Level | Authorisation |
|---|---|
| Safe | Available to everyone. |
| Low | Member-level access. |
| Moderate | Moderator permissions. |
| High | Administrator permissions. |
| Critical | Server owner permissions. |

Additional safeguards include:

- Explicit confirmation for destructive operations.
- Moderator and administrator approval workflows.
- Discord permission validation.
- Role hierarchy enforcement.
- Action audit logging.
- Protection against unauthorised privilege escalation.

Jarvis cannot grant itself permissions or execute operations beyond the permissions available to its Discord bot account.

---

## AI Provider Support

Jarvis v2 supports multiple AI providers, allowing users to configure their preferred models and fallback options.

Supported providers include:

| Provider | Configuration |
|---|---|
| Google Gemini | `GEMINI_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |
| Groq | `GROQ_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| Mistral | `MISTRAL_API_KEY` |
| DeepSeek | `DEEPSEEK_API_KEY` |
| Together AI | `TOGETHER_API_KEY` |
| xAI | `XAI_API_KEY` |
| Ollama | `OLLAMA_ENABLED` |
| LM Studio | `LMSTUDIO_ENABLED` |
| Custom providers | Custom configuration |

Provider availability depends on the corresponding configuration and credentials.

### Automatic Failover

Jarvis can automatically switch between configured models and providers when failures occur.

Examples include:

- Rate limits and quota exhaustion.
- Unavailable or retired models.
- Server-side errors.
- Network failures.
- Request timeouts.
- Invalid provider credentials.

The router tracks provider health and applies cooldowns to problematic models, allowing other configured providers to handle subsequent requests.

---

## Installation

Installation requirements and procedures vary between major versions.

### Requirements

Generally, Jarvis requires:

- Python 3.10 or newer.
- A Discord application with a configured bot user.
- Appropriate Discord gateway intents and permissions.
- At least one supported AI provider and its API credentials.

Additional dependencies may be required for optional functionality, such as voice playback.

### Clone the Repository

```bash
git clone <YOUR_REPOSITORY_URL>
cd <REPOSITORY_DIRECTORY>
```

### Jarvis v1

```bash
cd v1

pip install discord.py google-genai python-dotenv aiohttp

python bot.py
```

### Jarvis v2

```bash
cd v2

python -m venv .venv

source .venv/bin/activate

pip install -r requirements.txt

python run.py
```

On Windows, activate the virtual environment using:

```powershell
.venv\Scripts\activate
```

For complete installation and configuration instructions, refer to the README associated with the version you intend to run.

---

## Configuration

Jarvis uses environment variables and configuration files to manage Discord credentials, AI providers, and optional functionality.

A typical v2 configuration looks like this:

```dotenv
DISCORD_TOKEN=your-discord-bot-token
GUILD_ID=your-discord-server-id

GEMINI_API_KEY=your-gemini-api-key

LOG_CHANNEL_ID=
APPROVAL_CHANNEL_ID=
MOD_ROLE_ID=
ADMIN_ROLE_ID=
```

The available configuration options vary between versions.

Refer to the individual version's documentation for the complete configuration reference.

**Never commit API keys, Discord bot tokens, or other sensitive credentials to version control.**

---

## Development

Jarvis is an actively evolving project.

Development focuses on improving its architecture, expanding its capabilities, and making the system more reliable and extensible.

Areas of development include:

- Expanding the action registry.
- Improving AI interpretation and execution reliability.
- Supporting additional AI providers.
- Improving permission enforcement and security.
- Introducing additional Discord integrations.
- Expanding community management and automation.
- Improving modularity and maintainability.

Future releases may introduce significant architectural changes. Backward compatibility between major versions is not guaranteed.

---

## Roadmap

Jarvis is intended to be a long-term project, with future releases expanding on the concepts established by earlier versions.

| Version | Progress |
|---|---|
| v1 | Completed |
| v2 | Will be released soon |
| v3 | Planned |
| v4 | Planned |
| v5 | Planned |

Specific features and release timelines will be documented as development progresses.

---

## Author

Jarvis is an independently developed project focused on bringing AI-powered assistance and automation to Discord communities.

**Built with Python, Discord, and AI.**

*Jarvis is an evolving project. Each release represents another step towards a more capable and extensible Discord assistant.*
