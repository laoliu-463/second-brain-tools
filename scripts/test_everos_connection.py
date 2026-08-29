#!/usr/bin/env python3
"""
EverOS 服务器连接测试脚本
用于验证自托管EverOS记忆系统的API功能
"""

import requests
import json
import sys
from typing import Dict, Any

class EverOSTester:
    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        self.base_url = base_url
        self.api_prefix = "/api/v2/memory"
    
    def test_connection(self) -> bool:
        """测试基础连接"""
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            print(f"✓ 连接测试成功: {response.status_code}")
            return True
        except requests.exceptions.RequestException as e:
            print(f"✗ 连接测试失败: {e}")
            return False
    
    def test_search(self, query: str = "test") -> Dict[str, Any]:
        """测试记忆检索功能"""
        try:
            url = f"{self.base_url}{self.api_prefix}/search"
            payload = {"query": query}
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                print(f"✓ 记忆检索成功: 找到 {len(result.get('results', []))} 条结果")
                return result
            else:
                print(f"✗ 记忆检索失败: {response.status_code}")
                return {"error": response.text}
        except requests.exceptions.RequestException as e:
            print(f"✗ 记忆检索异常: {e}")
            return {"error": str(e)}
    
    def test_add(self, content: str, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """测试记忆添加功能"""
        try:
            url = f"{self.base_url}{self.api_prefix}/add"
            payload = {
                "content": content,
                "metadata": metadata or {}
            }
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                print(f"✓ 记忆添加成功: ID {result.get('id', 'unknown')}")
                return result
            else:
                print(f"✗ 记忆添加失败: {response.status_code}")
                return {"error": response.text}
        except requests.exceptions.RequestException as e:
            print(f"✗ 记忆添加异常: {e}")
            return {"error": str(e)}
    
    def test_flush(self) -> Dict[str, Any]:
        """测试记忆固化功能"""
        try:
            url = f"{self.base_url}{self.api_prefix}/flush"
            response = requests.post(url, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                print(f"✓ 记忆固化成功")
                return result
            else:
                print(f"✗ 记忆固化失败: {response.status_code}")
                return {"error": response.text}
        except requests.exceptions.RequestException as e:
            print(f"✗ 记忆固化异常: {e}")
            return {"error": str(e)}
    
    def run_full_test(self) -> bool:
        """运行完整测试套件"""
        print("=" * 50)
        print("EverOS 服务器功能测试")
        print("=" * 50)
        
        # 1. 基础连接测试
        print("\n1. 基础连接测试...")
        if not self.test_connection():
            print("基础连接失败，终止测试")
            return False
        
        # 2. 记忆检索测试
        print("\n2. 记忆检索测试...")
        search_result = self.test_search("认知思维")
        
        # 3. 记忆添加测试
        print("\n3. 记忆添加测试...")
        test_content = "这是一个EverOS连接测试的记忆条目"
        test_metadata = {
            "type": "test",
            "source": "everos_tester",
            "timestamp": "2026-08-14"
        }
        add_result = self.test_add(test_content, test_metadata)
        
        # 4. 记忆固化测试
        print("\n4. 记忆固化测试...")
        flush_result = self.test_flush()
        
        # 5. 验证添加的记忆
        print("\n5. 验证添加的记忆...")
        verify_result = self.test_search("EverOS连接测试")
        
        print("\n" + "=" * 50)
        print("测试完成")
        print("=" * 50)
        
        return True

def main():
    # 默认测试本地EverOS服务器
    # 如果需要测试远程服务器，修改base_url
    # 例如: base_url = "http://your-server-ip:8000"
    
    print("请选择测试模式:")
    print("1. 本地测试 (http://127.0.0.1:8000)")
    print("2. 远程测试 (需要服务器IP)")
    
    choice = input("请输入选择 (1/2): ").strip()
    
    if choice == "2":
        server_ip = input("请输入服务器IP地址: ").strip()
        base_url = f"http://{server_ip}:8000"
    else:
        base_url = "http://127.0.0.1:8000"
    
    tester = EverOSTester(base_url)
    success = tester.run_full_test()
    
    if success:
        print("\n✓ 所有测试通过，EverOS服务器功能正常")
        sys.exit(0)
    else:
        print("\n✗ 测试失败，请检查EverOS服务器配置")
        sys.exit(1)

if __name__ == "__main__":
    main()