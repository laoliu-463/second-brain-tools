# 移动诊断文件夹到内层对应位置
# 源路径: d:\第二大脑\studio\everme-everos-adapter\.ecc\diagnostics\everme-write-pipeline-repair-20260814
# 目标路径: d:\第二大脑\第二大脑\.ecc\diagnostics\everme-write-pipeline-repair-20260814

$sourcePath = "d:\第二大脑\studio\everme-everos-adapter\.ecc\diagnostics\everme-write-pipeline-repair-20260814"
$targetPath = "d:\第二大脑\第二大脑\.ecc\diagnostics\everme-write-pipeline-repair-20260814"

# 创建目标目录
New-Item -ItemType Directory -Force -Path (Split-Path $targetPath)

# 复制整个文件夹
Copy-Item -Path $sourcePath -Destination $targetPath -Recurse -Force

Write-Host "诊断文件夹已成功复制到: $targetPath"
Write-Host "源文件夹保持不变: $sourcePath"