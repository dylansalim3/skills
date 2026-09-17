#!/usr/bin/env python3
"""Sync olaparty + commandcode models into global opencode.json.

Reads live model lists, enriches with OpenRouter metadata, applies the
luna-exception filter, and rewrites only provider.<name>.models.
Never touches other providers, keys, or options.

Usage:
  update-models.py [--only olaparty|commandcode] [--dry-run] [--config PATH]
  OLAPARTY_API_KEY or LITELLM_API_KEY env preferred; falls back to
  provider.olaparty.options.apiKey already in opencode.json.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import sys
import urllib.request

# Hard rules: every emitted model uses this template, and apply always
# REPLACES provider.<name>.models (never merges) so delisted models vanish.
TEMPLATE = "opencode"

OLAPARTY_URL = "https://llm.olaparty.org/v1/models"
COMMANDCODE_URL = "https://api.commandcode.ai/provider/v1/models"
OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
DEFAULT_CONFIG = os.path.expanduser("~/.config/opencode/opencode.json")


def get(url, headers=None, timeout=25):
    base = {"User-Agent": "opencode-update-provider-models/1.0",
            "Accept": "application/json"}
    base.update(headers or {})
    req = urllib.request.Request(url, headers=base)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def norm_key(mid):
    m = (mid or "").lower().split(":")[0]
    short = m.split("/")[-1]
    return re.sub(r"[^a-z0-9.\-]+", "-", short).strip("-")


def keep_model(mid, name):
    """Filter: drop claude + openai families, except luna/luna-pro."""
    n = f"{mid or ''} {name or ''}".lower()
    if "luna" in n:
        return True
    if "claude" in n or "anthropic" in n:
        return False
    if "openai" in n:
        return False
    if re.search(r"(^|[/\-_ ])gpt([\-_ ]?\d|($|[\-_ ]))", n):
        return False
    if re.search(r"(^|[/\-_ ])o[134](mini)?($|[\-_. ])", n):
        return False
    if "codex" in n:
        return False
    return True


def build_openrouter_index():
    try:
        data = get(OPENROUTER_URL).get("data", [])
    except Exception as e:
        print(f"WARN: openrouter fetch failed ({e}), metadata fallback active", file=sys.stderr)
        return {}
    idx = {}
    for m in data:
        mid = m.get("id", "")
        base = mid.lower().split(":")[0]
        idx[base] = m
        idx[base.split("/")[-1]] = m
        idx[norm_key(mid)] = m
    return idx


def lookup_meta(idx, mid, name):
    for cand in [mid.lower().split(":")[0], (mid or "").split("/")[-1].lower(), norm_key(mid)]:
        if cand in idx:
            return idx[cand]
    # fuzzy: openai/gpt-5.6-luna matches commandcode gpt-5.6-luna
    for k, v in idx.items():
        if norm_key(mid) and norm_key(mid) in k:
            return v
    return {}


def variants_for(meta):
    supp = meta.get("supported_parameters", []) or []
    if "reasoning_effort" in supp:
        return {"standard": {},
                "low": {"options": {"reasoning_effort": "low"}},
                "medium": {"options": {"reasoning_effort": "medium"}},
                "high": {"options": {"reasoning_effort": "high"}},
                "xhigh": {"options": {"reasoning_effort": "xhigh"}}}
    return {"standard": {}}


def meta_flags(meta):
    supp = meta.get("supported_parameters", []) or []
    reasoning = bool({"reasoning", "reasoning_effort", "include_reasoning"} & set(supp)) or True
    tool_call = bool({"tools", "tool_choice"} & set(supp)) or True
    ctx = meta.get("context_length") or ((meta.get("top_provider") or {}).get("context_length"))
    out = (meta.get("top_provider") or {}).get("max_completion_tokens")
    return reasoning, tool_call, ctx, out


def build_entries(items, idx, provider):
    out = {}
    for it in items:
        mid, name = it.get("id", ""), it.get("name") or it.get("id", "")
        if not mid or not keep_model(mid, name):
            continue
        key, i = norm_key(mid), 2
        while key in out:
            key, i = f"{norm_key(mid)}-{i}", i + 1
        meta = lookup_meta(idx, mid, name)
        reasoning, tool_call, ctx, out_tok = meta_flags(meta)
        ctx = it.get("context_length") or ctx or 200000
        out_tok = out_tok or min(131072, ctx // 4)
        if provider == "olaparty":
            out[key] = {"template": TEMPLATE, "name": name,
                        "reasoning": reasoning, "contextWindow": ctx,
                        "variants": variants_for(meta)}
        else:
            entry = {"template": TEMPLATE, "id": mid, "name": name,
                     "reasoning": reasoning, "tool_call": tool_call,
                     "limit": {"context": ctx, "output": out_tok},
                     "provider": {"npm": "opencode-commandcode-provider-go",
                                  "api": "https://api.commandcode.ai"}}
            v = variants_for(meta)
            if len(v) > 1:
                entry["variants"] = v
            out[key] = entry
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["olaparty", "commandcode"], default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    a = ap.parse_args()

    cfg = json.load(open(a.config))
    providers = cfg.get("provider", {})
    targets = [a.only] if a.only else ["olaparty", "commandcode"]
    idx = build_openrouter_index()
    plan = {}

    if "commandcode" in targets:
        items = get(COMMANDCODE_URL).get("data", [])
        plan["commandcode"] = build_entries(items, idx, "commandcode")
    if "olaparty" in targets:
        key = (os.environ.get("OLAPARTY_API_KEY") or os.environ.get("LITELLM_API_KEY")
               or providers.get("olaparty", {}).get("options", {}).get("apiKey", ""))
        if not key:
            sys.exit("ERROR: no olaparty key (OLAPARTY_API_KEY/LITELLM_API_KEY or provider.olaparty.options.apiKey)")
        try:
            items = get(OLAPARTY_URL, {"Authorization": f"Bearer {key}"}).get("data", [])
        except Exception as e:
            sys.exit(f"ERROR: olaparty fetch failed: {e}")
        plan["olaparty"] = build_entries(items, idx, "olaparty")

    for p, models in plan.items():
        old = set(providers.get(p, {}).get("models", {}))
        new = set(models)
        print(f"[{p}] keep={len(old & new)} add={len(new - old)} remove={len(old - new)} total={len(new)}")
        for k in sorted(new - old):
            print(f"  + {k}")
        for k in sorted(old - new):
            print(f"  - {k} (dropped: filtered or delisted)")

    if a.dry_run:
        print("dry-run: no write")
        return
    bak = a.config + datetime.datetime.now().strftime(".bak-%Y%m%d-%H%M%S")
    shutil.copy2(a.config, bak)
    print(f"backup: {bak}")
    for p, models in plan.items():
        # Enforce template + full replace (drop anything not in the live API list).
        for m in models.values():
            m["template"] = TEMPLATE
        providers.setdefault(p, {}).setdefault("models", {})
        providers[p]["models"] = models
    json.dump(cfg, open(a.config, "w"), indent=2, ensure_ascii=False)
    print(f"updated: {a.config} (restart opencode to reload)")


if __name__ == "__main__":
    main()
