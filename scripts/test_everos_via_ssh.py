#!/usr/bin/env python3
"""
使用已有SSH配置测试EverOS连接
"""

import subprocess
import json
import sys

def run_ssh_command(command: str) -> tuple:
    """通过SSH执行命令"""
    try:
        # 使用SSH配置中的my-second-brain-server
        full_command = f"ssh my-second-brain-server '{command}'"
        result = subprocess.run(full_command, shell=True, capture_output=True, text=True, timeout=30)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Command timeout"
    except Exception as e:
        return -1, "", str(e)

def test_everos_health():
    """测试EverOS健康检查"""
    print("1. 测试EverOS健康检查...")
    code, stdout, stderr = run_ssh_command("curl -s http://127.0.0.1:8000/health")
    
    if code == 0 and stdout:
        print(f"✓ EverOS健康检查成功: {stdout}")
        return True
    else:
        print(f"✗ EverOS健康检查失败: {stderr}")
        return False

def test_everos_search():
    """测试EverOS记忆检索"""
    print("\n2. 测试EverOS记忆检索...")
    search_query = '{"query": "认知思维"}'
    code, stdout, stderr = run_ssh_command(f'curl -s -X POST http://127.0.0.1:8000/api/v2/memory/search -H "Content-Type: application/json" -d \'{search_query}\'')
    
    if code == 0 and stdout:
        try:
            result = json.loads(stdout)
            results_count = len(result.get('results', []))
            print(f"✓ 记忆检索成功: 找到 {results_count} 条结果")
            if results_count > 0:
                print(f"  示例结果: {result['results'][0].get('content', '')[:50]}...")
            return True
        except json.JSONDecodeError:
            print(f"✗ 记忆检索返回格式错误: {stdout}")
            return False
    else:
        print(f"✗ 记忆检索失败: {stderr}")
        return False

def test_everos_add():
    """测试EverOS记忆添加"""
    print("\n3. 测试EverOS记忆添加...")
    test_content = "这是一个EverOS连接测试的记忆条目 - 大聪明认知思维定位模型"
    test_metadata = '{"type": "test", "source": "ssh_test", "timestamp": "2026-08-14"}'
    
    add_data = f'{{"content": "{test_content}", "metadata": {test_metadata}}}'
    code, stdout, stderr = run_ssh_command(f'curl -s -X POST http://127.0.0.1:8000/api/v2/memory/add -H "Content-Type: application/json" -d \'{add_data}\'')
    
    if code == 0 and stdout:
        try:
            result = json.loads(stdout)
            memory_id = result.get('id', 'unknown')
            print(f"✓ 记忆添加成功: ID {memory_id}")
            return memory_id
        except json.JSONDecodeError:
            print(f"✗ 记忆添加返回格式错误: {stdout}")
            return None
    else:
        print(f"✗ 记忆添加失败: {stderr}")
        return None

def test_everos_flush():
    """测试EverOS记忆固化"""
    print("\n4. 测试EverOS记忆固化...")
    code, stdout, stderr = run_ssh_command("curl -s -X POST http://127.0.0.1:8000/api/v2/memory/flush")
    
    if code == 0 and stdout:
        try:
            result = json.loads(stdout)
            print(f"✓ 记忆固化成功")
            return True
        except json.JSONDecodeError:
            print(f"✗ 记忆固化返回格式错误: {stdout}")
            return False
    else:
        print(f"✗ 记忆固化失败: {stderr}")
        return False

def main():
    print("=" * 60)
    print("EverOS 服务器连接测试 (通过SSH配置)")
    print("目标服务器: my-second-brain-server (192.168.101.220)")
    print("=" * 60)
    
    # 测试序列
    health_ok = test_everos_health()
    if not health_ok:
        print("\n✗ 基础连接失败，请检查:")
        print("  1. EverOS服务是否运行")
        print("  2. SSH连接是否正常")
        print("  3. 防火墙是否阻止8000端口")
        sys.exit(1)
    
    search_ok = test_everos_search()
    add_ok = test_everos_add()
    flush_ok = test_everos_flush()
    
    # 验证添加的记忆
    if add_ok:
        print("\n5. 验证添加的记忆...")
        verify_query = '{"query": "EverOS连接测试"}'
        code, stdout, stderr = run_ssh_command(f'curl -s -X POST http://127.0.0.1:8000/api/v2/memory/search -H "Content-Type: application/json" -d \'{verify_query}\'')
        
        if code == 0 and stdout:
            try:
                result = json.loads(stdout)
                results_count = len(result.get('results', []))
                if results_count > 0:
                    print(f"✓ 验证成功: 找到刚添加的记忆")
                else:
                    print(f"✗ 验证失败: 未找到刚添加的记忆")
            except json.JSONDecodeError:
                print(f"✗ 验证返回格式错误")
    
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)
    
    if health_ok and search_ok and add_ok and flush_ok:
        print("\n✓ EverOS服务器功能正常，可以接入知识库")
        sys.exit(0)
    else:
        print("\n✗ 部分功能异常，请检查EverOS配置")
        sys.exit(1)

if __name__ == "__main__":
    main()