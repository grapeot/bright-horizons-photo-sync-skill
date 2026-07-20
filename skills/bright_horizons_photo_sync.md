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

支持的 V0 版提取流程为本地交互式：

1. 引导用户打开 `https://familyinfocenter.brighthorizons.com/`，登录家长账号，并从孩子卡片点击进入 My Bright Day。
2. 打开浏览器的开发者工具（DevTools）Network 面板，触发月份或 Memories 加载，选择一个返回 200 的 `/remote/v1/events` 请求。
3. 让用户将该请求的完整 `Cookie` 头部、`X-TADPOLES-UID` 和 Referer 中携带的 `dependent_id` 填入本地的 `.env` 配置文件（参考 `.env.example`）。
4. 在本地终端执行 `chmod 600 .env` 以限制读取权限，并核实该文件已被 Git 忽略。

如果本地有受信任的自动化浏览器环境，可辅助识别该请求，但严禁打印或向外发送任何敏感凭证。如果无法进行受信任的本地提取，必须退回到上述手动提取流程。

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
