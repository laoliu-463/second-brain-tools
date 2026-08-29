# 将.agents\skills同步到.cursor\skills-cursor的脚本

$sourceSkills = "C:\Users\caojianing\.agents\skills"
$targetSkills = "C:\Users\caojianing\.cursor\skills-cursor"

# 获取源目录和目标目录的技能列表
$sourceSkillsList = Get-ChildItem -Path $sourceSkills -Directory | Select-Object -ExpandProperty Name
$targetSkillsList = Get-ChildItem -Path $targetSkills -Directory | Select-Object -ExpandProperty Name

Write-Host "源技能数量: $($sourceSkillsList.Count)"
Write-Host "目标技能数量: $($targetSkillsList.Count)"

# 找出需要复制的新技能
$newSkills = $sourceSkillsList | Where-Object { $_ -notin $targetSkillsList }

if ($newSkills.Count -gt 0) {
    Write-Host "发现 $($newSkills.Count) 个新技能需要安装:"
    $newSkills | ForEach-Object { Write-Host "  - $_" }
    
    # 复制新技能
    foreach ($skill in $newSkills) {
        $sourcePath = Join-Path $sourceSkills $skill
        $targetPath = Join-Path $targetSkills $skill
        
        Write-Host "正在复制: $skill"
        Copy-Item -Path $sourcePath -Destination $targetPath -Recurse -Force
    }
    
    Write-Host "✓ 已完成 $($newSkills.Count) 个新技能的安装"
} else {
    Write-Host "没有新技能需要安装"
}

# 可选：删除目标中不存在于源中的技能
$obsoleteSkills = $targetSkillsList | Where-Object { $_ -notin $sourceSkillsList }

if ($obsoleteSkills.Count -gt 0) {
    Write-Host "发现 $($obsoleteSkills.Count) 个过时技能:"
    $obsoleteSkills | ForEach-Object { Write-Host "  - $_" }
    
    $response = Read-Host "是否删除这些过时技能？(y/n)"
    if ($response -eq 'y') {
        foreach ($skill in $obsoleteSkills) {
            $targetPath = Join-Path $targetSkills $skill
            Write-Host "正在删除: $skill"
            Remove-Item -Path $targetPath -Recurse -Force
        }
        Write-Host "✓ 已删除 $($obsoleteSkills.Count) 个过时技能"
    }
}

Write-Host "同步完成"
Write-Host "更新后的目标技能数量: $((Get-ChildItem -Path $targetSkills -Directory).Count)"