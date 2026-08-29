#!/usr/bin/env python3
"""
查询EverOS Knowledge导入进度
"""

import subprocess
import json
import sys

def run_ssh_command(command: str) -> tuple:
    """通过SSH执行命令"""
    try:
        full_command = f"ssh my-second-brain-server '{command}'"
        result = subprocess.run(full_command, shell=True, capture_output=True, text=True, timeout=30)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Command timeout"
    except Exception as e:
        return -1, "", str(e)

def query_knowledge_documents():
    """查询Knowledge文档数量"""
    print("查询Knowledge文档导入状态...")
    code, stdout, stderr = run_ssh_command("curl -s -X GET http://127.0.0.1:8000/api/v2/knowledge/documents")
    
    if code == 0 and stdout:
        try:
            result = json.loads(stdout)
            if isinstance(result, dict) and "documents" in result:
                docs = result["documents"]
                print(f"✓ Knowledge文档总数: {len(docs)}")
                for doc in docs[:10]:  # 显示前10个
                    print(f"  - {doc.get('source_name', 'unknown')}: {doc.get('doc_id', 'unknown')}")
                if len(docs) > 10:
                    print(f"  ... 还有 {len(docs) - 10} 个文档")
                return len(docs)
            else:
                print(f"✗ 返回格式不符合预期: {stdout[:200]}")
                return None
        except json.JSONDecodeError:
            print(f"✗ JSON解析失败: {stdout[:200]}")
            return None
    else:
        print(f"✗ 查询失败: {stderr}")
        return None

def query_everos_health():
    """查询EverOS健康状态"""
    print("查询EverOS健康状态...")
    code, stdout, stderr = run_ssh_command("curl -s http://127.0.0.1:8000/health")
    
    if code == 0 and stdout:
        try:
            result = json.loads(stdout)
            print(f"✓ EverOS状态: {result.get('status', 'unknown')}")
            capabilities = result.get('capabilities', {})
            print(f"  Knowledge功能: {'启用' if capabilities.get('knowledge') else '禁用'}")
            print(f"  Embed功能: {'启用' if capabilities.get('embed') else '禁用'}")
            return result
        except json.JSONDecodeError:
            print(f"✗ JSON解析失败: {stdout[:200]}")
            return None
    else:
        print(f"✗ 查询失败: {stderr}")
        return None

def main():
    print("=" * 60)
    print("EverOS Knowledge导入进度查询")
    print("=" * 60)
    
    health = query_everos_health()
    print()
    doc_count = query_knowledge_documents()
    
    print("\n" + "=" * 60)
    if doc_count is not None:
        print(f"当前进度: {doc_count}/95 篇文档已导入")
        if doc_count >= 95:
            print("✓ 所有文档导入完成")
        else:
            print(f"🔄 还需导入 {95 - doc_count} 篇文档")
    else:
        print("无法确定导入进度")
    print("=" * 60)

if __name__ == "__main__":
    main()