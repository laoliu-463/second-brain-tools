# GitHub CLI 安装和配置步骤

## 1. 安装 GitHub CLI

### Windows (推荐使用 winget)
```powershell
winget install --id GitHub.cli
```

### 或使用 Scoop
```powershell
scoop install gh
```

### 验证安装
```powershell
gh --version
```

## 2. 登录 GitHub
```powershell
gh auth login
```
会跳转到浏览器，授权后返回终端显示认证成功。

## 3. 配置Git使用GitHub CLI
```powershell
git config --global credential.helper gh
```

## 4. 验证认证
```powershell
gh auth status
```

## 5. 验证Git连接
```powershell
cd "D:\第二大脑"
git remote -v
git fetch origin
```

## 当前状态
- ✅ 远程仓库已配置：`https://github.com/laoliu-463/second-brain.git`
- ✅ Token已删除（安全）
- ⏳ 需要安装GitHub CLI并登录
- ⏳ 需要配置Git使用GitHub CLI认证