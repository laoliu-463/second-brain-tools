# GitHub CLI 配置完成指南

## 已完成的配置

### ✅ 1. Git全局配置
已通过编辑 `C:\Users\caojianing\.gitconfig` 添加：
```ini
[credential]
    helper = gh
```

### ✅ 2. 本地仓库配置
`D:\第二大脑\.git\config` 已配置：
```ini
[credential]
    helper = gh
```

## 需要手动执行的步骤

### 步骤1: 验证GitHub CLI安装
在PowerShell中执行：
```powershell
gh --version
```

如果未安装，执行：
```powershell
winget install --id GitHub.cli
```

### 步骤2: 登录GitHub
```powershell
gh auth login
```

这会打开浏览器进行授权：
1. 选择 GitHub.com
2. 选择 HTTPS
3. 选择 Yes（上传git凭证）
4. 选择 Login with a web browser
5. 复制显示的code到浏览器中
6. 授权成功后按Enter

### 步骤3: 验证登录状态
```powershell
gh auth status
```

应该显示已登录状态和账户信息。

### 步骤4: 测试Git连接
```powershell
cd "D:\第二大脑"
git remote -v
git fetch origin
```

### 步骤5: 首次推送（如需要）
```powershell
git push -u origin main
```

## 故障排查

### 如果 `gh auth login` 失败
尝试清理现有凭据：
```powershell
gh auth logout
gh auth login
```

### 如果Git仍然提示认证失败
检查credential helper配置：
```powershell
git config --global credential.helper
```

应该输出：`gh`

### 如果仍然需要Token认证
作为临时方案，可以使用Personal Access Token：
```powershell
git config --global credential.helper store
```
然后推送时会提示输入Token。

## 配置文件位置
- 全局配置：`C:\Users\caojianing\.gitconfig`
- 本地配置：`D:\第二大脑\.git\config`
- GitHub CLI配置：`C:\Users\caojianing\AppData\Roaming\gh\hosts.yml`