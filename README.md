# Bright Horizons 照片增量备份

这是一个本地优先的 CLI，用于增量下载 My Bright Day 中账号有权访问的事件、照片、视频和附件。它把代码与家庭数据分开保存，并通过 SQLite 记录事件、attachment ID、文件 hash 和同步水位，适合手工运行或定时运行。本项目是非官方工具，与 Bright Horizons 无隶属或认可关系。

## 安装

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -e '.[dev]'
# photos-export 使用 ExifTool 写标准 EXIF，并用 ffmpeg 无损写视频 creation_time
brew install exiftool ffmpeg
```

复制配置模板并填写本机凭证：

```bash
cp .env.example .env
```

`.env` 只要求 `BH_SESSION_COOKIE`、`BH_TADPOLES_UID` 和 `BH_DEPENDENT_ID`。`BH_TIMEZONE` 控制日期边界和 Photos EXIF，默认是 `America/Los_Angeles`。家庭数据默认写入项目根目录下的 `data/`，该目录整体被 Git ignore；也可以通过 `BH_DATA_DIR` 或 `--data-dir` 覆盖。

凭证赋予读取家庭媒体的权限。只在本机 `.env` 中保存，不要粘贴到 AI 对话、issue、日志或 commit。建议执行 `chmod 600 .env`。

## Cookie 失效时怎么恢复

不要从旧 `mybrightday.brighthorizons.com` 根登录页开始。它可能把家长带到只接受 employee ID 和 network password 的员工认证入口。

1. 打开美国当前家长入口：[Family Information Center](https://familyinfocenter.brighthorizons.com/)。
2. 点击 `Log In`，使用家长账号完成登录。
3. 从首页的孩子卡片进入 My Bright Day。系统会先请求 `mbdwgateway.brighthorizons.com/api/account/mbdtoken`，再通过一次性 JWT 跳到 `mybrightday.brighthorizons.com`。
4. 打开浏览器 DevTools 的 Network，切换 My Bright Day 月份或 Memories，让页面发出 `/remote/v1/events` 请求。
5. 选择一个返回 `200` 的事件请求，使用 `Copy as cURL`，从中提取完整 `Cookie` header 和 `X-TADPOLES-UID`。
6. 把 Cookie 值写入本地 `.env` 的 `BH_SESSION_COOKIE`；把 UID 写入 `BH_TADPOLES_UID`。浏览器地址或请求 Referer 中的 `dependent_id` 写入 `BH_DEPENDENT_ID`。
7. 先执行短窗口验证：

```bash
bh-photo-sync sync --since 2025-01-01 --until 2025-01-02 --events-only
```

如果 CLI 返回 `401` 或 `403`，说明 cookie 不完整、已经过期，或复制的不是成功的 My Bright Day API 请求。重新走上述流程，不要把 Family Information Center 的 `access_token`、JWT redirect URL 或 cookie 提交到 Git。

详细认证链和证据见 [`docs/authentication.md`](docs/authentication.md)。

## 常用命令

推荐把完整工作流拆成三个可独立重跑的阶段：

```bash
# 1. 发现事件和 attachment ID，不下载媒体
bh-photo-sync discover --since 2024-01-01 --until 2025-01-01

# 2. 断点续传 pending/failed 附件
bh-photo-sync download

# 3. 按 magic bytes 修正历史对象的扩展名和数据库 MIME
bh-photo-sync fix-formats
```

`sync` 仍提供 discover + download 的一体化入口。

显式同步半开日期区间 `[since, until)`：

```bash
bh-photo-sync sync --since 2024-08-01 --until 2025-01-01
```

首次全历史 backfill 可以从已知入园历史之前开始，例如：

```bash
bh-photo-sync sync --since 2023-01-01 --until 2025-01-01
```

不传日期时，CLI 从上次成功水位回看 14 天并同步到今天结束。这种重叠运行会捕捉迟到或近期修改的事件，同时按稳定 ID 避免重复保存：

```bash
bh-photo-sync sync
```

若事件已经进入数据库，只需断点续传 pending/failed 附件：

```bash
bh-photo-sync download
```

Bright Horizons 有时把 PNG 内容错误标记成 `image/jpeg`。新下载会在原子发布前检查文件签名；已有规范对象可随时运行 `fix-formats`，它只改正确的扩展名和数据库 metadata，不转码、不修改媒体字节。

查看状态和验证本地对象：

```bash
bh-photo-sync status
bh-photo-sync verify
```

每次成功的事件 API 请求都会原子保存到 `data/cache/api/events/`。API 返回的全部可见事件进入 SQLite；所有未被 legacy 覆盖的附件进入统一 `data/objects/`，不按孩子、日期或关联对象建立业务文件夹。

按数据库字段查询附件：

```bash
bh-photo-sync --json query --status complete --mime-prefix image/ --limit 20
bh-photo-sync --json query --status failed --limit 100
```

SQLite 同时提供 `v_attachments`（每个 attachment 一行）和 `v_attachment_context`（每个 attachment-event 关系一行），可直接用 `sqlite3` 查询。

合并规范对象库与 Legacy，生成完整 Photos 导入目录：

```bash
bh-photo-sync photos-export
```

`data/exports/photos_import_all/` 是一个扁平、可重建的导出目录。所有图片统一转为标准 RGB JPEG；规范对象使用数据库最早 event time 写入 EXIF，Legacy 图片沿用原 `DateTimeOriginal`。视频不转码，只用 ffmpeg 无损写入 QuickTime creation time；Legacy 视频没有内部时间时使用日期文件名对应当天中午。PDF、空文件和内容重复项不进入导出。可以在 Mac Photos 中用 `File > Import` 选择整个目录。可用 `BH_TIMEZONE` 或 `photos-export --timezone Asia/Shanghai` 指定 EXIF 时区。

不要把数千个文件从 Finder 拖进 Photos。拖放会通过 pasteboard 为每个文件分配 sandbox extension；数量较大时，Photos 日志会出现 `Failed to consume sandbox extension ... Cannot allocate memory`，随后所有文件都以 `Operation not permitted` 失败。普通 RAM、磁盘空间、Unix 权限和文件描述符并不是这个错误的根因。

推荐用 `osxphotos` 做可恢复导入，并把所有资产放进 `Bright Horizons` album。Photos 的 album 只是 Library 中资产的引用，因此资产仍会出现在 Library；下面的命令保证它们同时归入指定 album。先 dry-run：

```bash
uvx --python 3.12 osxphotos import data/exports/photos_import_all \
  --walk --skip-dups --dup-albums --album "Bright Horizons" \
  --dry-run --no-progress
```

确认数量后执行导入：

```bash
uvx --python 3.12 osxphotos import data/exports/photos_import_all \
  --walk --skip-dups --dup-albums --album "Bright Horizons" \
  --resume --stop-on-error 3
```

`--skip-dups` 按 Photos fingerprint 跳过已经存在的文件，`--dup-albums` 把已存在的资产补进目标 album，`--resume` 使用 `~/.local/share/osxphotos/osxphotos_import.db` 继续中断的运行。如果单文件 AppleScript 导入也报 sandbox `ENOMEM`，说明当前系统的 extension table 已经耗尽；仅重启 Photos 不足以恢复，应先重启 macOS，再运行上述命令。以后避免逐文件拖放，手工导入时通过 `File > Import` 选择包含目录本身，让系统授予一个递归目录 scope。

如果用户选择 Photos 图形界面而不是 `osxphotos`，每批导入约 1,000 个文件。每批完成后彻底退出并重新打开 Photos，再从上次完成的位置继续下一批；某批失败时先重启 Photos 并重试该批。若连单文件都持续返回 sandbox `ENOMEM`，再重启 macOS。

图片转码和视频 remux 默认使用 `CPU 核数 - 2` 个 worker；发现、内容去重和最终清理由主进程完成。每个输出先写独立临时文件再原子替换，可安全重复运行。

导出合并 JSON 和 CSV：

```bash
bh-photo-sync export
```

## 日期和去重语义

- `--since` 包含起始日，`--until` 不包含结束日；时区读取 `--timezone`、`BH_TIMEZONE`，最后回退到 `America/Los_Angeles`。
- CLI 使用小时间窗口请求 API；达到服务端结果上限时自动二分，避免旧脚本按月抓取造成静默漏数。
- 每个成功时间窗口保存完整原始 response cache；SQLite 保存 API 返回的全部可见事件和关联。
- 事件按事件 `key` upsert，媒体按 attachment ID 去重。API 自带的 `member` metadata 原样保留用于审计，不用于过滤、推断所有权或决定下载范围。
- 文件必须完成下载、类型校验、SHA-256 计算和原子 rename 后才标记完成。
- 日期化照片名不再作为身份。旧日期命名文件保留在 `data/legacy/`；历史 `data/children/` 视图停止维护但不会自动删除。

## 测试

默认测试完全离线：

```bash
source .venv/bin/activate
python -m pytest -q
ruff check .
```

真实 API 测试必须显式启用：

```bash
BH_ENABLE_LIVE_TESTS=1 python -m pytest -q -m live
```

完整测试策略见 [`docs/test.md`](docs/test.md)。
