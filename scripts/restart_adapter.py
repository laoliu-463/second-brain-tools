#!/usr/bin/env python3
"""
适配器重启脚本
用于重启适配器服务以应用配置更改
"""

import subprocess
import sys
import time
import requests
from pathlib import Path

def stop_adapter():
    """停止适配器服务"""
    print("停止适配器服务...")
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "python.exe", "/FI", "WINDOWTITLE eq everme_adapter*"],
            capture_output=True
        )
        time.sleep(2)
        print("✓ 适配器服务已停止")
    except Exception as e:
        print(f"停止服务时出错: {e}")

def start_adapter():
    """启动适配器服务"""
    print("启动适配器服务...")
    adapter_dir = Path(r"d:\第二大脑\studio\everme-everos-adapter")
    config_path = adapter_dir / "runtime" / "config.json"
    
    if not config_path.exists():
        print(f"✗ 配置文件不存在: {config_path}")
        return False
    
    try:
        process = subprocess.Popen(
            ["python", "-m", "everme_adapter", "--runtime-path", "runtime", "--config", "runtime/config.json"],
            cwd=adapter_dir,
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
        print(f"✓ 适配器服务已启动 (PID: {process.pid})")
        return True
    except Exception as e:
        print(f"✗ 启动服务失败: {e}")
        return False

def wait_for_adapter():
    """等待适配器服务就绪"""
    print("等待适配器服务就绪...")
    for i in range(30):
        try:
            response = requests.get("http://127.0.0.1:18100/healthz", timeout=2)
            if response.status_code == 200:
                print("✓ 适配器服务已就绪")
                return True
        except:
            pass
        time.sleep(1)
        print(f"等待中... ({i+1}/30)")
    
    print("✗ 适配器服务未能在30秒内就绪")
    return False

def verify_config():
    """验证配置"""
    print("验证配置...")
    try:
        response = requests.get("http://127.0.0.1:18100/healthz", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"✓ 适配器健康检查通过")
            print(f"  Backend: {data.get('backend')}")
            print(f"  Agent Memory: {data.get('agentMemory')}")
            print(f"  Server Knowledge: {data.get('serverKnowledge')}")
            return True
        else:
            print(f"✗ 健康检查失败: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ 验证失败: {e}")
        return False

def main():
    print("=" * 60)
    print("适配器服务重启")
    print("=" * 60)
    
    stop_adapter()
    time.sleep(2)
    
    if start_adapter():
        if wait_for_adapter():
            verify_config()
            print("\n" + "=" * 60)
            print("适配器服务重启完成")
            print("=" * 60)
        else:
            print("适配器服务启动失败")
            sys.exit(1)
    else:
        print("适配器服务启动失败")
        sys.exit(1)

if __name__ == "__main__":
    main()