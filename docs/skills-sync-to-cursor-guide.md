# Skills同步到Cursor指南

## 手动执行步骤

### 方法1：运行同步脚本（推荐）
```powershell
cd D:\第二大脑\scripts
.\sync-skills-to-cursor.ps1
```

### 方法2：手动复制关键技能
如果只想复制特定技能（如`wait-what`），可以手动执行：

```powershell
# 复制wait-what技能
Copy-Item -Path "C:\Users\caojianing\.agents\skills\wait-what" -Destination "C:\Users\caojianing\.cursor\skills-cursor\wait-what" -Recurse -Force

# 复制其他需要的技能（根据需要）
Copy-Item -Path "C:\Users\caojianing\.agents\skills\ask-matt" -Destination "C:\Users\caojianing\.cursor\skills-cursor\ask-matt" -Recurse -Force
```

### 方法3：批量复制所有缺失技能
```powershell
$source = "C:\Users\caojianing\.agents\skills"
$target = "C:\Users\caojianing\.cursor\skills-cursor"

# 获取目标目录已有的技能
$existing = Get-ChildItem -Path $target -Directory | Select-Object -ExpandProperty Name

# 复制源目录中目标不存在的技能
Get-ChildItem -Path $source -Directory | Where-Object { $_.Name -notin $existing } | ForEach-Object {
    Write-Host "复制: $($_.Name)"
    Copy-Item -Path $_.FullName -Destination (Join-Path $target $_.Name) -Recurse -Force
}
```

## 验证安装
执行后检查：
```powershell
# 检查Cursor技能目录
Get-ChildItem -Path "C:\Users\caojianing\.cursor\skills-cursor" -Directory | Measure-Object

# 验证特定技能存在
Test-Path "C:\Users\caojianing\.cursor\skills-cursor\wait-what\SKILL.md"
```

## 建议执行方式
推荐使用方法1（同步脚本），它会：
- 自动识别需要复制的新技能
- 提供删除过时技能的选项
- 显示详细的同步过程