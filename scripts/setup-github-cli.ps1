# GitHub CLI 配置脚本
# 手动执行此脚本完成GitHub CLI配置

Write-Host "=== GitHub CLI 配置脚本 ===" -ForegroundColor Green

# 1. 检查GitHub CLI是否已安装
Write-Host "`n[1/5] 检查GitHub CLI安装状态..." -ForegroundColor Yellow
try {
    $ghVersion = gh --version
    Write-Host "✓ GitHub CLI已安装: $ghVersion" -ForegroundColor Green
} catch {
    Write-Host "✗ GitHub CLI未安装，正在安装..." -ForegroundColor Red
    winget install --id GitHub.cli
}

# 2. 配置Git使用GitHub CLI认证
Write-Host "`n[2/5] 配置Git使用GitHub CLI认证..." -ForegroundColor Yellow
git config --global credential.helper gh
Write-Host "✓ Git credential helper已配置为gh" -ForegroundColor Green

# 3. 验证配置
Write-Host "`n[3/5] 验证Git配置..." -ForegroundColor Yellow
$credentialHelper = git config --global credential.helper
Write-Host "当前credential.helper: $credentialHelper" -ForegroundColor Cyan

# 4. 登录GitHub（需要交互）
Write-Host "`n[4/5] 登录GitHub..." -ForegroundColor Yellow
Write-Host "即将打开浏览器进行授权..." -ForegroundColor Cyan
Read-Host "按Enter键继续"
gh auth login

# 5. 验证登录状态
Write-Host "`n[5/5] 验证GitHub登录状态..." -ForegroundColor Yellow
gh auth status

# 6. 测试Git连接
Write-Host "`n[6/6] 测试Git连接..." -ForegroundColor Yellow
cd "D:\第二大脑"
git remote -v
git fetch origin

Write-Host "`n=== 配置完成 ===" -ForegroundColor Green