---
type: synthesis
status: active
created: 2026-08-26
updated: 2026-08-26
sources:
  - AGENTS.md
---

# 用 Hermes 从微信问第二大脑

电脑上的 Hermes 连着本库。手机微信只是入口。电脑要开着，网关要在跑。

## 已经做好的

- Hermes 项目「第二大脑」指向 `D:\第二大脑`
- 网关工作目录已设为本库（会读 `AGENTS.md`）
- 查询技能：`.hermes/skills/second-brain/SKILL.md`

## 你需要在本机做的（必须扫码，我代不了）

在 PowerShell：

```powershell
hermes gateway setup
```

选 Weixin，用手机微信扫终端里的码，并在手机上确认。

然后只允许你自己发私信（不要用对所有人开放）：

```powershell
hermes config set platforms.weixin.extra.dm_policy pairing
```

配对成功后再改成白名单。不要把 token 写进仓库。

启动（电脑登录后自动跑）：

```powershell
hermes gateway install
hermes gateway start
hermes gateway status
```

先试跑、不装服务也可以：

```powershell
hermes gateway run
```

## 怎么用

扫码后，微信里出现的是 **iLink 机器人**（账号常带 `@im.bot`），一般只能**私信**，普通群往往收不到。

你发一句自己的问题。它会先查人智和大小课，再查其他允许的材料。默认只回答，不改库。

微信侧只开三项本机能力：技能、终端、读文件。查库用检索脚本，不经过浏览器或电脑操控。等回复出来再发下一句，中途再发会打断重跑。

## 限制

- 电脑关机或网关停了，手机就问不成。
- 登录过期（常见错误码 -14）要重新 `hermes gateway setup`。
- 同一微信 token 不能同时开两个网关。

## 速度（2026-08-26）

- 微信工具：只留 `file`、`terminal`、`skills`。
- 思考档：`agent.reasoning_effort` 从 `xhigh` 改为 `medium`（本机 Hermes 全局生效，含桌面会话）。
- 微信不推工具进度气泡。
- 改配置后需 `hermes gateway restart` 才对正在跑的网关生效。
