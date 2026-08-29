#!/usr/bin/env python3
"""
测试适配器配置和Agent Memory功能
"""

import requests
import json

def test_adapter_capabilities():
    """测试适配器能力配置"""
    try:
        response = requests.get("http://127.0.0.1:18100/healthz", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print("✓ 适配器健康检查通过")
            print(f"  Backend: {data.get('backend')}")
            print(f"  Capabilities: {data.get('capabilities')}")
            print(f"  Agent Memory: {data.get('agentMemory')}")
            print(f"  Server Knowledge: {data.get('serverKnowledge')}")
            return True
        else:
            print(f"✗ 适配器健康检查失败: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ 适配器连接失败: {e}")
        return False

def test_agent_memory_config():
    """测试Agent Memory配置"""
    try:
        with open(r"d:\第二大脑\studio\everme-everos-adapter\runtime\config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
        
        print("✓ 配置文件读取成功")
        print(f"  EverOS URL: {config.get('everos_base_url')}")
        print(f"  Agent Search Method: {config.get('agent_search_method')}")
        
        for agent in config.get('agents', []):
            print(f"  Agent {agent.get('platform')}:")
            print(f"    ID: {agent.get('id')}")
            print(f"    Memory ID: {agent.get('memory_id', '未设置')}")
        
        return True
    except Exception as e:
        print(f"✗ 配置文件读取失败: {e}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("适配器配置验证")
    print("=" * 60)
    
    print("\n1. 测试配置文件:")
    test_agent_memory_config()
    
    print("\n2. 测试适配器能力:")
    test_adapter_capabilities()
    
    print("\n" + "=" * 60)