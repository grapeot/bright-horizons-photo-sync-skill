# Bright Horizons 照片增量备份

这是一个本地优先的 CLI，用于增量下载 My Bright Day 中家庭账号有权访问的事件、照片、视频和附件。它将代码与家庭数据分开保存，通过 SQLite 记录事件、附件 ID、文件哈希和同步水位，既可手动运行，也可定时运行。

> [!IMPORTANT]
> 本项目是非官方开源工具，与 Bright Horizons 没有隶属关系，也未获得其认可或背书。

## 安装说明

核心同步功能支持 macOS 和其他提供 `fcntl` 的类 Unix 系统。`photos-import` 导入功能仅支持 macOS，Windows 系统目前不在支持范围内。

```bash
uv venv .venv
source .venv/bin/activate

# 仅运行核心备份功能
uv pip install -e .

# 安装开发与离线测试环境
uv pip install -e '.[dev]'

# 安装 macOS Photos 导入集成
uv pip install -e '.[photos]'

# photos-export 依赖外部工具来写入 EXIF 拍摄时间（ExifTool）和视频元数据（ffmpeg）
brew install exiftool ffmpeg
```

接着复制配置模板并填写本地凭证：

```bash
cp .env.example .env
```

本地 `.env` 文件只需要配置三个关键认证项：`BH_SESSION_COOKIE`、`BH_TADPOLES_UID` 和 `BH_DEPENDENT_ID`。此外，`BH_TIMEZONE` 用于控制同步的日期边界和 Photos 中的 EXIF 时区偏移量（默认值为 `America/Los_Angeles`）。家庭私有数据默认写入项目根目录下的 `data/` 目录（该目录已被 Git 忽略）。你也可以通过环境变量 `BH_DATA_DIR` 或命令行参数 `--data-dir` 指定其他本地路径。

这些凭证允许程序读取家庭媒体。请只在本地 `.env` 中保存，不要粘贴到 AI 会话、GitHub issue、日志或 commit 中。建议执行 `chmod 600 .env` 限制文件访问权限。

## Cookie 失效恢复指南

当登录会话过期时，请勿从旧的 `mybrightday.brighthorizons.com` 登录页开始，因为该页面可能会引导家长进入只接受员工凭证的认证入口。请按照以下步骤重新获取 Cookie：

1. 在浏览器中打开当前的家长入口：[Family Information Center](https://familyinfocenter.brighthorizons.com/)。
2. 点击 `Log In`，使用家长账号完成登录。
3. 在首页的孩子卡片中，点击进入 My Bright Day。此时浏览器会先请求 `mbdwgateway.brighthorizons.com/api/account/mbdtoken`，然后通过一次性的 JWT 重定向到 `mybrightday.brighthorizons.com`。
4. 按 `F12` 打开浏览器 DevTools 的 `Network` 面板，然后在网页中切换月份或查看 Memories，使页面触发 `/remote/v1/events` 请求。
5. 找到返回状态码为 `200` 的事件请求，右键选择 `Copy as cURL`，并从中提取出完整的 `Cookie` 头部内容以及 `X-TADPOLES-UID` 的值。
6. 将 Cookie 的值写入本地 `.env` 的 `BH_SESSION_COOKIE` 中，将 UID 的值写入 `BH_TADPOLES_UID` 中。将浏览器地址栏或请求 Referer 中携带的 `dependent_id` 写入 `BH_DEPENDENT_ID` 中。
7. 在使用新的 Cookie 之前，可以使用临时目录执行短时间窗口的冒烟测试，避免不确定的测试数据写入正式备份中：

```bash
SMOKE_DIR="$(mktemp -d)"
trap 'rm -rf "$SMOKE_DIR"' EXIT
bh-photo-sync --data-dir "$SMOKE_DIR" discover \
  --since YYYY-MM-DD --until YYYY-MM-DD
rm -rf "$SMOKE_DIR"
trap - EXIT
```

如果 CLI 返回 `401` 或 `403` 错误，说明 Cookie 复制不完整、已过期，或者选择的不是成功的 My Bright Day API 请求。请重新执行上述流程。请勿将 Family Information Center 的 `access_token`、JWT 重定向链接或临时 Cookie 提交到公共网络。

认证链和会话恢复细节见 [`docs/authentication.md`](docs/authentication.md)。

## 常用命令

推荐将完整的工作流拆分为三个可以独立重跑的阶段：

```bash
# 1. 发现指定时间段的事件和附件 ID，但不下载媒体文件
bh-photo-sync discover --since YYYY-MM-DD --until YYYY-MM-DD

# 2. 增量断点续传数据库中 pending 或 failed 状态的附件
bh-photo-sync download

# 3. 根据文件签名修复文件扩展名和数据库中的 MIME 类型
bh-photo-sync fix-formats
```

核心命令 `sync` 依然提供了一键执行 `discover` 和 `download` 的组合入口。

### 同步事件与媒体

同步半开日期区间 `[since, until)` 的全部数据：

```bash
bh-photo-sync sync --since YYYY-MM-DD --until YYYY-MM-DD
```

如果是初次全量同步，建议将 `--since` 指定为入园时间之前：

```bash
bh-photo-sync sync --since YYYY-MM-DD --until YYYY-MM-DD
```

如果不指定日期参数，CLI 会自动读取上次同步成功的终点，并往前重叠同步 14 天到今天结束。这种重叠设计可以捕获最近修改或补发的历史事件，同时利用数据库中的唯一 ID 避免重复下载：

```bash
bh-photo-sync sync
```

### 仅下载媒体

如果事件数据已经保存到本地数据库，只需增量补齐未完成或下载失败的附件即可：

```bash
bh-photo-sync download
```

### 格式修复

Bright Horizons 有时会将 PNG 文件错误地标记为 `image/jpeg`。对于新下载的文件，程序在原子写入前会检查其特征码。而对于历史下载的规范对象，你可以随时运行格式修复命令。该命令只会修改文件的扩展名和数据库中的 MIME 类型，不会对媒体文件进行重新转码或更改其原始字节：

```bash
bh-photo-sync fix-formats
```

### 状态查看与校验

你可以查看本地的备份统计，或者验证本地媒体文件是否完好：

```bash
bh-photo-sync status
bh-photo-sync verify
```

`status` 命令会显示事件、附件和运行历史的总数；`verify` 命令在加参数 `--deep` 时会重新计算所有已完成文件的 SHA-256 哈希值，确保文件没有损坏。

### 查询附件

```bash
# 查询 20 个下载状态为 complete 且类型为图片的附件
bh-photo-sync --json query --status complete --mime-prefix image/ --limit 20

# 查询 100 个下载失败的附件
bh-photo-sync --json query --status failed --limit 100
```

你也可以直接使用 `sqlite3` 查询数据库中的视图 `v_attachments`（每行代表一个附件）或 `v_attachment_context`（每行代表一个附件与事件的关联）。

### 重建 JSON 和 CSV 导出

```bash
bh-photo-sync export
```

该命令从 SQLite 重建 `merged_events.json`、`events_summary.csv` 和 `event_attachments.csv`。

### 导入 macOS Photos

先将规范对象库与 Legacy 归档合并为扁平、可重建的导入目录：

```bash
bh-photo-sync photos-export
```

命令会在 `data/exports/photos_import_all/` 中生成文件。图片统一转换为标准 RGB JPEG；规范对象使用最早事件时间写入 EXIF，Legacy 图片保留原 `DateTimeOriginal`。视频不转码，只用 ffmpeg 写入 QuickTime creation time。PDF、空文件和重复内容不会进入导出目录。

不要把数千个文件从 Finder 直接拖进 Photos。拖放会通过 pasteboard 为每个文件申请 sandbox extension，数量过大时可能出现 `Cannot allocate memory`，随后所有文件都以 `Operation not permitted` 失败。

目标 album 必须已经存在，而且名称必须精确匹配。先运行 dry-run：

```bash
bh-photo-sync photos-import --album "EXACT EXISTING ALBUM NAME" --dry-run
```

确认数量后再导入：

```bash
bh-photo-sync photos-import --album "EXACT EXISTING ALBUM NAME"
```

CLI 使用 `osxphotos` 从 Photos SQLite 的只读临时副本匹配指纹。新媒体每 100 个一批导入，已有资产会批量加入目标 album。命令不会直接修改 Photos SQLite，并用文件锁阻止并发操作。超时或中断后可直接重跑，新的指纹扫描会确定剩余工作。如果单文件导入仍返回 sandbox `ENOMEM`，请重启 macOS 后再运行 CLI。

## 日期与去重语义

- `--since` 为闭区间（包含），`--until` 为开区间（不包含）。时区由 `--timezone`、`BH_TIMEZONE` 控制，最后回退至 `America/Los_Angeles`。
- CLI 使用较小的时间片请求接口。如果遇到服务端响应上限，会自动启动二分法拆分请求，以防止大时间跨度请求导致静默数据截断。
- 每次成功的事件 API 请求都会被原子缓存至 `data/cache/api/events/` 目录。
- 附件通过 ID 进行全局去重。API 提供的成员信息（`member`）仅作为元数据供审计使用，不参与任何过滤或所有权过滤逻辑。
- 只有在文件完成下载、类型验证、计算 SHA-256 哈希值并且原子重命名完成后，数据库状态才会更改为 `complete`。

## 测试运行

默认测试在完全离线状态下运行，不与真实 API 交互，也不写入 Photos 库：

```bash
source .venv/bin/activate
ruff check .
TEST_HOME="$(mktemp -d)"
HOME="$TEST_HOME" python -m pytest -q
rm -rf "$TEST_HOME"
```

测试与发布门槛见 [`docs/test.md`](docs/test.md)。

## 安装为 Agent Skill

把本仓库的 GitHub URL 交给 Codex、Claude Code、Cursor、OpenCode 或其他 coding agent，并要求它安装 `skills/bright_horizons_photo_sync.md`。

安装 Agent 应先读取目标 workspace 的 `AGENTS.md`、`CLAUDE.md` 和路由文件，再将这个 skill 加入现有的 skill index 或发现链。若目标 workspace 没有 index，则在入口文档中添加简短指针。不要把 `.env` 或 `data/` 复制到 skill 目录。
