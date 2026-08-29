#!/usr/bin/env python3
import hashlib
import json
import secrets
from pathlib import Path

def generate_token():
    return "evt_" + secrets.token_urlsafe(32)

def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

# 生成token
claude_token = generate_token()
claude_digest = token_digest(claude_token)

print(f"Generated token: {claude_token}")
print(f"SHA256: {claude_digest}")

# 更新适配器配置
config_path = Path(r"d:\第二大脑\studio\everme-everos-adapter\runtime\config.json")
with open(config_path, "r", encoding="utf-8") as f:
    config = json.load(f)

for agent in config["agents"]:
    if agent["platform"] == "claude-code":
        agent["token_sha256"] = claude_digest

with open(config_path, "w", encoding="utf-8") as f:
    json.dump(config, f, ensure_ascii=False, indent=2)

# 更新Cursor Hook配置
hooks_config_path = Path(r"C:\Users\caojianing\.cursor\hooks.json")
with open(hooks_config_path, "r", encoding="utf-8") as f:
    hooks_config = json.load(f)

hooks_config["hooks"]["user_prompt_submit"]["headers"]["Authorization"] = f"Bearer {claude_token}"

with open(hooks_config_path, "w", encoding="utf-8") as f:
    json.dump(hooks_config, f, ensure_ascii=False, indent=2)

# 更新EverMe配置
everme_config_path = Path(r"C:\Users\caojianing\.cursor\everme-config.json")
with open(everme_config_path, "r", encoding="utf-8") as f:
    everme_config = json.load(f)

everme_config["everme"]["token"] = claude_token

with open(everme_config_path, "w", encoding="utf-8") as f:
    json.dump(everme_config, f, ensure_ascii=False, indent=2)

print("Configuration updated successfully")
print(f"Token: {claude_token}")