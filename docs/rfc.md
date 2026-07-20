# RFC: 同步、存储与照片导入架构

## 系统概览

```text
家庭浏览器会话
      │
      ▼
MyBrightDayClient ───> 原子 API 缓存
      │
      ▼
完整区间收集器 ───> SQLite Store ───> JSON/CSV 导出
      │                      │
      ▼                      ▼
附件下载器            规范化对象存储
                             │
                             ▼
                     Photos 兼容导出目录
                             │
                             ▼
                    精确相册 Photos 导入
```

SQLite 数据库是结构化数据的单一事实来源。规范化的媒体对象和原始 API 响应缓存均保存在同一个私有数据根目录下。JSON/CSV 导出、Photos 兼容导出文件以及 Photos 导入报告均为衍生出来的产物，可以独立重新生成或进行审计。

## 配置与数据边界

CLI 从本地的 `.env` 文件中读取三个认证参数：完整的浏览器 Cookie 头部、`X-TADPOLES-UID` 以及作为 Referer 的 `dependent_id`。此外，`BH_TIMEZONE` 控制本地时间边界和 EXIF 时区偏移量，`BH_DATA_DIR` 或命令行参数 `--data-dir` 指定私有数据根目录。

`dependent_id` 用于在 API 请求中构造 Referer 并建立家长会话，但它并不代表 API 返回数据的过滤边界。My Bright Day 的后台可能会返回与多个成员相关或不带成员信息的共享事件。客户端会保存所有可见的事件，并保留原始的 `member` 字段元数据，不进行所有权推断、分类或过滤。

## 私有数据布局

```text
data/
├── state.sqlite3
├── backups/
├── objects/<prefix>/<attachment-id>.<extension>
├── cache/api/events/
├── state/
│   ├── photos_operation.lock
│   └── osxphotos/
├── reports/photos_import/
├── exports/
│   ├── photos_import_all/
│   ├── merged_events.json
│   ├── events_summary.csv
│   └── event_attachments.csv
└── legacy/                         # 可选的历史遗留归档
```

整个数据根目录以及本地 `.env` 文件均已被 Git 忽略。API 缓存和导出文件中可能会包含个人姓名、评论、可见的元数据以及媒体文件；它们享有与 SQLite 数据库同等的隐私保护级别。

## 日期区间与完整性

用户输入日期形成半开区间 `[since, until)`，使用 IANA 时区解析。由于服务端的旧 API 采用包含上界的 Epoch 戳，因此在发送请求时会使用 `until_epoch - 1`。

收集器首先请求限定的日期窗口。如果单次响应返回的事件数达到服务端单页上限，说明数据可能存在截断，收集器将递归地二分当前 Epoch 时间戳范围。如果由于网络等原因导致请求失败，并在重试次数耗尽后，也会触发二分区间重新尝试。遇到认证失败、数据格式损坏、本地存储写入失败或时间跨度已压缩到 1 秒仍请求超限时，程序会立即中断。

来自重叠窗口的事件会通过稳定的事件 `key` 进行去重。如果同一次运行中的一个事件 `key` 出现两个不同 payload，程序会报告完整性错误。只有所有叶子区间都成功，而且媒体阶段也已完成（`--events-only` 除外），同步水位才会推进。

## 原子事件缓存

每次成功的事件 API 请求都会以 JSON 格式保存到 `cache/api/events/`。文件名记录请求区间与数量上限，文件内容包含抓取时间和完整事件数组。客户端先写入唯一临时文件，调用 flush 和 fsync 确保落盘，再通过 `os.replace` 原子发布。如果缓存写入失败，程序中止运行，不会丢弃审计证据后继续报成功。

## SQLite 数据库结构

数据库当前版本为 3，包含以下表：

- `events`：存储稳定 key、API 关联的 `member_id` 及其展示名、事件时间戳、原始 JSON payload、payload 哈希以及首次和最后一次观察时间。
- `attachments`：存储附件 ID、原始文件名、MIME 类型、本地对象相对路径、文件大小、SHA-256 哈希、同步生命周期状态、是否被 Legacy 覆盖标记以及最后一次下载报错信息。
- `event_attachments`：记录事件与附件之间的多对多关联关系。
- `sync_state`：记录持久化的成功水位线。
- `sync_runs`：记录每次运行的起止时间戳、状态、成功事件/下载数以及错误摘要。

数据库提供 `v_attachments`（每个附件一行）和 `v_attachment_context`（包含事件上下文，每个附件-事件关联一行）视图。视图仅暴露原始 member 字段，不作任何所有权过滤。

打开版本 1 或版本 2 的旧数据库时，程序先检查外键，生成 SQLite 备份，再原子升级到版本 3。版本 3 移除了本地孩子分类机制，同时保留 API 返回的原始事件 payload 和 member ID。

## 附件同步生命周期

附件的状态包括：`pending`（等待下载）、`failed`（下载失败）、`complete`（下载完成）以及 `legacy`（已被历史 Legacy 归档覆盖）。

1. 发现阶段检测到新附件时，会将其插入为 `pending` 状态；如果 Legacy 归档映射表证实该附件已覆盖，则标记为 `legacy`。
2. 下载器请求附件元数据接口，获取并验证 HTTPS 签名链接，以流式块方式写入临时 `.part` 文件。
3. 流读取完毕后，拒绝空文件或不支持的格式，通过文件签名识别真实类型，计算 SHA-256 并执行 fsync，最后原子重命名到 `objects/<prefix>/`。
4. 只有当重命名并确认落盘无误后，SQLite 中的状态才会原子更新为 `complete`。
5. 在运行 `pending` 队列前，如果检测到本地已标记 `complete` 的文件丢失或大小不符，会将其重置为 `pending`。深度校验命令还会重新比对 SHA-256 哈希值。

带签名的临时 URL 严禁写入 SQLite 或任何错误日志中。网络报错文本会对 URL 进行脱敏过滤。

## 文件格式修复

由于服务端可能会给出不准确的文件 MIME 标记，新下载的文件均会读取二进制特征码来决定扩展名。命令行 `fix-formats` 对已下载的规范媒体文件进行批量检测。如果发现后缀与真实特征不符，会原子重命名文件并更新 SQLite 中的相对路径与 MIME 类型；如果数据库修改失败，文件名将回滚至原始状态。该命令仅修改扩展名和元数据，不修改任何文件原始字节。

## Photos 导出

`photos-export` 命令处理所有 `complete` 状态的规范媒体与 Legacy 文件。通过源文件 SHA-256 哈希进行全局去重，并在 `exports/photos_import_all/` 下生成扁平化文件。

- **图片**：解码并保存为 RGB 模式的高品质 JPEG，使用 ExifTool 写入包含时区偏移的标准 EXIF 属性（DateTimeOriginal、创建和修改时间），并标记软件版本和源哈希。
- **视频**：不进行重转码，保持视频轨与音频轨流拷贝，仅无损写入 UTC 拍摄时间元数据。
- 剔除 PDF、空文件、不支持的文件以及内容重复项。

每个 worker 独立写入临时文件，完成后通过 `os.replace` 原子发布。如果已有输出的源哈希和时间戳符合当前导出契约，命令会直接复用。过期的受管衍生文件会被清理。项目级文件锁会阻止导出与导入同时运行。

## Photos 导入

`photos-import` 仅在 macOS 系统中有效，且必须使用项目 photos extra 锁定的 `osxphotos` 库。导入器通过 Photos 数据库只读临时副本进行高速指纹匹配，绝对不直接修改 Photos 的底层数据库。

在导入前，程序会执行以下校验：

1. 确认导出目录中不包含软链接、子目录或不受支持的文件格式。
2. 检测最近一次使用的照片库（`.photoslibrary`）路径并打开。
3. 申请系统级的导入锁，并确保导入过程中选中的照片库未发生改变。
4. 确认命令行指定的 `--album` 在 Photos 中精确存在且唯一（拒绝歧义或缺失名称）。
5. 启动 osxphotos 子进程，重置环境变量，确保不把 `.env` 中的敏感凭证带入子进程环境中。

在 `dry-run` 模式下，命令解析 osxphotos 预检摘要并返回统计结果，不执行实际导入。正式导入会通过指纹匹配已有资产，批量把它们加入指定 album，再把缺失文件每 100 个一批提交给 Photos。修改 Photos 的 Apple Event 会关闭 photoscript 外层超时重试。遇到结果不明确的超时或中断后，用户直接重跑命令；新的指纹扫描会确定剩余工作。命令会保存带时间戳的 JSON 报告。只有每个候选文件都能对应到目标 album 中的 asset ID，运行才会成功。

直接拖放数千个文件进入 Photos 的 GUI 容易导致沙盒 Extension 耗尽。CLI 避免了这一问题。如果仍报 sandbox `ENOMEM`，通常代表系统的 Extension 表已满，必须重启 macOS 才能释放，重启后再次运行 CLI 即可。

## 并发锁机制

项目引入了两个非阻塞的建议性文件锁保护 Photos 操作：

- `data/state/photos_operation.lock`：防止同一个数据目录下并发地执行导出或导入操作。
- `~/.cache/bright-horizons-photo-sync/photos_import.lock`：防止在同一台机器上跨数据目录并发地往 Photos 导入数据。

如果发生并发竞争，程序会直接报错退出，而不会无限期等待。

## 退出状态码说明

- **Exit 2**：本地 `.env` 配置缺失，或者命令行参数不合法。
- **Exit 3**：My Bright Day 会话 Cookie 缺失、过期或无效。
- **Exit 4**：网络中断或同步期间发生通用请求异常。
- **Exit 5**：数据完整性校验失败（如单秒区间超限或发现冲突事件 Payload）。
- **Exit 6**：SQLite 数据库读写失败或本地磁盘写入受阻。
- **Exit 7**：Photos 导入的前置预检失败、子进程错误、照片库不一致或报告生成失败。

同步失败时不会推进水位，附件失败状态保留供后续重试。Photos 导入必须先写出报告，才能声明成功。

## 安全与隐私保护

- 所有的 Cookie 和账号 ID 严格保留在本地 `.env` 文件中，禁止进入任何 SQLite、API 缓存、报告或日志。
- 带签名的媒体 CDN 临时链接（Signed URL）会在错误日志输出时被过滤脱敏。
- 原始 API payload、姓名、评论、member 元数据、媒体、哈希和 Photos 导入报告均保存在被 Git 忽略的 `data/` 私有根目录下。
- Photos 子进程被严格隔离，仅能继承 `HOME`、区域语言、系统 `PATH` 等白名单环境变量。
- 项目不会自动输入密码或 MFA、绕过授权、上传家庭媒体，也不会直接修改 Photos 数据库。
- 测试套件默认使用合成的虚拟数据。真实 API 与本地 Photos 测试默认关闭，需显式声明环境变量方可运行。
