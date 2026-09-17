---
name: update-provider-models
description: Use when olaparty or commandcode models in opencode look stale, or after a new model release needs syncing to the global config
---

# Update Provider Models

Syncs `provider.olaparty.models` and `provider.commandcode.models` in `~/.config/opencode/opencode.json` from live APIs. OpenRouter is metadata-only.

## When to Use

- Model list for olaparty/commandcode is outdated, missing, or 404s
- User asks to refresh, add, or prune provider models
- NOT for other providers; NOT for keys, baseURLs, or plugin code

## Sources

| Purpose | Endpoint |
|---|---|
| olaparty list (needs key) | `GET https://llm.olaparty.org/v1/models` + `Authorization: Bearer $OLAPARTY_API_KEY` (`LITELLM_API_KEY` also accepted, else `provider.olaparty.options.apiKey` from config) |
| commandcode list (public) | `GET https://api.commandcode.ai/provider/v1/models` |
| metadata only (effort levels, ctx/output, tools) | `GET https://openrouter.ai/api/v1/models` — match on full id, short id, then normalized key; never adds models from here |

## Filter Rule

Drop `claude`/`anthropic` and `gpt`/`o1`/`o3`/`o4`/`codex`/`openai` matches. Exception first: anything containing `luna` (luna, luna-pro) is kept.

## Hard Rules

- Every model entry gets `"template": "opencode"` unconditionally — for both providers, no exceptions, enforced post-build before write.
- Apply always REPLACES `provider.<name>.models` with the fresh filtered API list. Never merge: anything absent from the live list (deprecated/delisted) is removed.

## Quick Reference

```bash
# preview
python3 ~/.config/opencode/skill/update-provider-models/scripts/update-models.py --dry-run
python3 ~/.config/opencode/skill/update-provider-models/scripts/update-models.py --only commandcode --dry-run
# apply (backs up opencode.json, rewrites ONLY provider.<name>.models)
python3 ~/.config/opencode/skill/update-provider-models/scripts/update-models.py
```

Restart opencode after apply (config loads once at startup).

## Implementation

- commandcode entry: `{template, id (full provider id), name, reasoning, tool_call, limit:{context,output}, provider:{npm,api}}` + `variants{standard,low,medium,high,xhigh}` iff OpenRouter lists `reasoning_effort`.
- olaparty entry: `{template, name, reasoning, contextWindow, variants}` — same variant rule, else `{standard:{}}`.
- Key = short id lowercased (`Qwen/Qwen3.8-Max` → `qwen3.8-max`); suffix `-2` on collision.
- Context prefers provider `context_length`, else OpenRouter `context_length`; output prefers `top_provider.max_completion_tokens`, else `ctx//4` capped at 131072.

## Common Mistakes

- Editing `options`/`api`/`npm` blocks — only `models` is rewritten.
- Copying OpenRouter ids into config — provider ids come only from that provider's own endpoint.
- Forgetting restart — running session keeps old model list.
- Committing the backup (`opencode.json.bak-*`) — leave it local.
