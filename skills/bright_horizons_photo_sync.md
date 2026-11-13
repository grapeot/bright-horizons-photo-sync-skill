---
name: bright-horizons-photo-sync
description: 增量备份已授权 Bright Horizons 家庭账号可见的 My Bright Day 事件和媒体，续传中断的下载，校验本地文件，并去重导入 macOS Photos 的指定 album。
---

# Bright Horizons Photo Sync

当用户需要在本地备份 My Bright Day 的照片、视频、PDF 或事件元数据时，使用此 skill。CLI 使用用户本人控制的家庭账号浏览器 session，不得绕过登录、MFA、授权或服务访问控制。

## 成功标准

- 用户可自行选择本地数据存储目录及请求的日期范围。
- 认证信息只保存在本地且已被 Git 忽略的 `.env` 中，不得出现在聊天记录、命令输出、GitHub issue 或 commit 中。
- `discover` 命令必须完整覆盖所请求的半开日期区间。如果遇到部分区间同步失败，严禁向前推移成功水位线。
- `download` 阶段必须使 `pending` 或 `failed` 附件归零，或者清楚报告尚未解决的错误。
- 在声明备份完成前，运行 `verify --deep` 校验必须通过并返回 `ok: True`。
- 如果用户提出导出需求，`photos-export` 应生成包含正确 IANA 时区且可重复构建的照片导出目录。
- `photos-import --dry-run` 必须在发生实际照片库写入前，准确分析出新增和重复资产的分布。
- 完成导入后，所有导出的候选媒体必须成功添加至目标相册中；再次重跑导入命令时，应返回导入了 0 个新资产。

## 边界规则

- 下载已认证 API 返回的所有事件和附件。事件返回的 `member` 关联信息仅作为元数据保留，不用于过滤下载或归类所有权。
- 在开始大范围同步前，应明确告知用户，该账号可能包含全班共享或未指派特定成员的公共事件。
- 不要要求用户将 Cookie、cURL 请求、token、JWT 重定向链接、人名或 dependent ID 粘贴到 AI 会话中。
- 不提供密码或 MFA 的自动化登录交互。用户必须在可见的浏览器窗口中自行完成登录。
- 绝对不将下载的媒体文件上传、发布或执行删除操作。`data/` 目录和 `.env` 文件严格保留在本地。
- 遇到 `401`、`403`、HTML 登录响应、数据截断或存储失败等异常时，必须将其视为硬性失败中断退出。

## 身份认证

认证提取为本地交互式流程：V1（受信浏览器辅助提取）优先，受信浏览器环境不可用时回退 V0（手动 DevTools）。两条路径都不得绕过登录、MFA 或访问控制，凭证只进本地 `.env`。

### V1：受信浏览器辅助提取

1. 用专用 user-data-dir 启动附带 CDP 远程调试端口的可见 Chrome（端口应避开本机已占用的调试端口；profile 路径与端口属本地运行配置，不写入公开文档），打开 `https://familyinfocenter.brighthorizons.com/`，并由用户本人在窗口内完成登录与 MFA。
2. Agent 只做被动监听，不代填凭证、不替用户点击登录。家长登录后从孩子卡片进入 My Bright Day，地址形如 `…/dashboard/parents.html?dependent_id=…`，`BH_DEPENDENT_ID` 可直接读取该 URL 获得。
3. 两个易错点：`dashboard/parents.html` 处于空闲态时不会发出 `/remote/v1/events` 请求；且不要让浏览器直接导航 `mybrightday.brighthorizons.com` 根路径，该入口会落到仅接受员工凭证的登录页。稳定的触发方式是对已打开的 My Bright Day 页面做一次刷新，或等待页面自身的网络活动。
4. 不依赖抓包请求头：`/legacy/parents/params` 等请求在自动化客户端侧不暴露 Cookie 头。直接用 CDP 的 cookie 读取接口取 `mybrightday.brighthorizons.com` 域的 cookie（其中的 `session` 承载会话），按该域拼接成 Cookie 头写入 `BH_SESSION_COOKIE`。
5. `BH_TADPOLES_UID` 是账号级固定标识，不随会话过期；`.env` 已有旧值时直接复用。Cookie 会话有效期的经验值约为 3 个月。
6. 写入后确认 `.env` 权限为 600 且已被 Git 忽略。

### V0：手动 DevTools 提取（回退）

1. 引导用户打开 `https://familyinfocenter.brighthorizons.com/`，登录家长账号，并从孩子卡片点击进入 My Bright Day。
2. 打开 DevTools 的 Network 面板，触发月份或 Memories 加载，选择一个返回 200 的 `/remote/v1/events` 请求。
3. 将该请求的完整 `Cookie` 头部、`X-TADPOLES-UID` 与 Referer 中携带的 `dependent_id` 填入本地 `.env` 配置文件（参考 `.env.example`）。
4. 在本地终端执行 `chmod 600 .env` 以限制读取权限，并核实该文件已被 Git 忽略。

## 增量与去重语义

- `discover` 报告的 `attachment_count` 是区间内事件的附件引用数。同一媒体对象会在多个事件间复用，数据库按附件 ID 去重，因此 `download` 实际新增下载量通常远小于 `attachment_count`；`downloaded` 与 `failed` 同时为 0 即队列已清空，不代表区间数据缺失。
- `download` 幂等：中断后重跑即可续传，水位线只在区间完整覆盖时推进。
- `photos-import` 幂等：导入完成后再次运行（或 dry-run）应返回 0 个新导入。目标相册名必须与 Photos 库中现有相册精确匹配，不要顺手「纠正」既有相册名的拼写。

## 运行指导

使用 `uv pip install -e .` 安装核心依赖，开发环境使用 `uv pip install -e '.[dev]'`，macOS Photos 导入使用 `uv pip install -e '.[photos]'`。`photos` extra 固定了经过测试的 `osxphotos` 版本。`photos-export` 还需要外部安装 ExifTool 和 ffmpeg。核心备份依赖类 Unix 系统提供 `fcntl`；`photos-import` 仅支持 macOS。

推荐采用以下可独立重跑的阶段式命令：

```bash
bh-photo-sync discover --since YYYY-MM-DD --until YYYY-MM-DD
bh-photo-sync download
bh-photo-sync fix-formats
bh-photo-sync verify --deep
```

在验证 Cookie 是否有效时，建议配合 `--data-dir` 使用临时的冒烟目录执行单日 `discover`，防止污染正式备份的水位。在正式全量备份时，`--since` 应早于最早入园日期，`--until` 晚于最迟结束日期。日期采用本地的 `BH_TIMEZONE`，除非命令行中显式传入了 `--timezone` 参数。

针对 macOS 照片库：

```bash
bh-photo-sync photos-export
```

请勿将大量文件直接从 Finder 拖拽进 Photos 的 GUI 界面中，以防沙盒 Extension 耗尽引发内存分配失败（`Cannot allocate memory`）和权限错误（`Operation not permitted`）。应当使用支持指纹去重的 CLI 导入命令，并传入一个已存在的精确相册名。相册仅是媒体库资产的引用，不提供独立的物理存储。

```bash
# 预检：分析照片库且不写入变更
bh-photo-sync photos-import --album "EXACT EXISTING ALBUM NAME" --dry-run

# 执行导入：导入缺失资产并将已存在重复项归入该相册
bh-photo-sync photos-import --album "EXACT EXISTING ALBUM NAME"
```

正式导入会从 Photos 数据库的临时只读副本中匹配候选指纹，每批导入 100 个新资产，并把已有资产加入目标 album。命令会申请文件锁，校验导入前后的 Photos library 一致性，并记录逐文件 JSON 报告。超时或中断后直接重跑即可。不要改用 `osxphotos import`：它逐个资产发送 Apple Event，大型导入可能耗时数小时。也不要添加 `--resume`；该路径可能跳过本流程要求的重复资产 album 修复。

如果单文件导入依然因为沙盒而引发 `ENOMEM` 错误，说明系统的 Extension 表已耗尽。此时仅重启 Photos 应用程序是无济于事的，必须重启 macOS 才能重置系统状态，重启后再次运行 CLI 即可。

如果用户倾向于使用 Photos 的图形界面进行导入，建议将导出目录切分为每批约 1,000 个文件。每完成一批导入，彻底退出并重新打开 Photos，然后再进行下一批；若某批次导入失败，重启 Photos 并重试该批次；若连单文件都持续报错，再考虑重启 macOS。

## 结果呈报

当备份运行结束后，应主动向用户呈报同步的日期区间、事件总数、附件总数、待处理/失败的附件数、哈希校验结果以及导出的文件路径与数量（如适用）。严禁将身份凭证的 Cookie 或 API 返回的原始 Payload 暴露到聊天日志中。

## 维护工作流

本项目仅通过公开的 `grapeot/bright-horizons-photo-sync-skill` 仓库进行维护。严禁从私有仓库进行拉取、推送、合并或提交 PR。

主线分支保持在公开的 `master`。如果需要对代码或文档进行修改，应当基于 `origin/master` 创建本地的 `feat/*` 特征分支，在该分支下通过验证后，推送至公开的 `origin`，然后提交 PR 合并回受保护的 `master`。合并后删除临时分支。如果本地的 Git 钩子配置只允许 `master -> master` 的推送，说明其与分支保护策略冲突，应当更新钩子规则以允许 `feat/*` 的 PR 分支推送，并继续拦截对 `master` 及私有命名分支的直接 Push。
