"""
GitHub CLI 配置脚本 - 通过Python执行
"""
import subprocess
import sys
from pathlib import Path

def run_command(cmd, description):
    """执行命令并显示结果"""
    print(f"\n{'='*50}")
    print(f"执行: {description}")
    print(f"命令: {cmd}")
    print(f"{'='*50}")
    
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.stdout:
            print("输出:")
            print(result.stdout)
        
        if result.stderr:
            print("错误:")
            print(result.stderr)
        
        print(f"返回码: {result.returncode}")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print("命令执行超时")
        return False
    except Exception as e:
        print(f"执行失败: {e}")
        return False

def main():
    print("GitHub CLI 配置脚本")
    print("="*50)
    
    # 1. 检查GitHub CLI
    run_command("gh --version", "检查GitHub CLI版本")
    
    # 2. 配置Git credential helper
    run_command("git config --global credential.helper gh", "配置Git使用GitHub CLI")
    
    # 3. 验证配置
    run_command("git config --global credential.helper", "验证credential helper配置")
    
    # 4. 检查当前目录Git配置
    repo_path = Path(r"D:\第二大脑")
    if repo_path.exists():
        print(f"\n仓库路径: {repo_path}")
        run_command(f'cd "{repo_path}" && git remote -v', "检查远程仓库配置")
    
    print("\n" + "="*50)
    print("基础配置完成")
    print("注意: gh auth login 需要手动在PowerShell中执行")
    print("="*50)

if __name__ == "__main__":
    main()