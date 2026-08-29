#!/usr/bin/env python3
"""
生成和配置Cursor Hook集成所需的token
"""

import hashlib
import json
import secrets
from pathlib import Path

def generate_token():
    """生成新的token"""
    return "evt_" + secrets.token_urlsafe(32)

def token_digest(token: str) -> str:
    """计算token的SHA256哈希"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def main():
    print("=" * 60)
    print("Cursor Hook Token配置")
    print("=" * 60)
    
    # 读取现有适配器配置
    config_path = Path(r"d:\第二大脑\studio\everme-everos-adapter\runtime\config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    # 为Claude Code生成新token
    claude_token = generate_token()
    claude_digest = token_digest(claude_token)
    
    print(f"\n为Claude Code生成新token:")
    print(f"Token: {claude_token}")
    print(f"SHA256: {claude_digest}")
    
    # 更新适配器配置中的Claude Code token
    for agent in config["agents"]:
        if agent["platform"] == "claude-code":
            agent["token_sha256"] = claude_digest
            print(f"\n已更新Claude Code agent配置:")
            print(f"ID: {agent['id']}")
            print(f"新SHA256: {claude_digest}")
    
    # 保存更新后的适配器配置
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"\n✓ 适配器配置已更新")
    
    # 更新Cursor Hook配置
    hooks_config_path = Path(r"C:\Users\caojianing\.cursor\hooks.json")
    with open(hooks_config_path, "r", encoding="utf-8") as f:
        hooks_config = json.load(f)
    
    hooks_config["hooks"]["user_prompt_submit"]["headers"]["Authorization"] = f"Bearer {claude_token}"
    
    with open(hooks_config_path, "w", encoding="utf-8") as f:
        json.dump(hooks_config, f, ensure_ascii=False, indent=2)
    print(f"✓ Cursor Hook配置已更新")
    
    # 更新EverMe配置
    everme_config_path = Path(r"C:\Users\caojianing\.cursor\everme-config.json")
    with open(everme_config_path, "r", encoding="utf-8") as f:
        everme_config = json.load(f)
    
    everme_config["everme"]["token"] = claude_token
    
    with open(everme_config_path, "w", encoding="utf-8") as f:
        json.dump(everme_config, f, ensure_ascii=False, indent=2)
    print(f"✓ EverMe配置已更新")
    
    # 保存token信息
    token_info_path = Path(r"C:\Users\caojianing\.cursor\token-info.txt")
    with open(token_info_path, "w", encoding="utf-8") as f:
        f.write(f"# Cursor Hook Token配置信息\n")
        f.write(f"# 生成时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"CLAUDE_CODE_TOKEN={claude_token}\n")
        f.write(f"CLAUDE_CODE_TOKEN_SHA256={claude_digest}\n")
        f.write(f"\n# 请妥善保管此token，不要泄露\n")
    
    print(f"✓ Token信息已保存")
    
    print("\n" + "=" * 60)
    print("Token配置完成")
    print("=" * 60)
    print("\n下一步:")
    print("1. 重启适配器服务以应用配置更改")
    print("2. 在Cursor中测试Hook集成")
    print("3. 验证记忆检索功能")

if __name__ == "__main__":
    main()