# Git分支策略

## 分支结构
- `main`：稳定版本，生产环境使用
- `develop`：开发中内容，测试环境使用
- `feature/*`：功能分支，每个功能一个分支

## 分支命名规范
- 功能分支：`feature/功能描述-YYYYMMDD`
- 修复分支：`fix/问题描述-YYYYMMDD`
- 热修分支：`hotfix/紧急修复-YYYYMMDD`

## 工作流程
1. 从 `main` 创建 `develop` 分支
2. 功能开发在 `feature/*` 分支
3. 功能完成后合并到 `develop`
4. `develop` 测试通过后合并到 `main`
5. 定期将 `main` 标记版本标签

## 分支保护
- `main` 分支需要保护，禁止直接推送
- 需要通过Pull Request合并
- 代码审查后才能合并