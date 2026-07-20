# 测试策略

## 默认离线测试

执行 `python -m pytest -q` 时默认在无网环境下运行，主要覆盖以下核心逻辑：

- 验证可配置的 IANA 时区、太平洋夏令时日期边界以及 Photos EXIF 时区偏移计算。
- 验证用户侧半开区间 `[since, until)` 到服务端包含上界（`until_epoch - 1`）的协议转换。
- 验证 7 天分片请求策略，以及在单页达到数量上限（300 条）时的递归二分查找逻辑。
- 验证相邻时间窗口重叠请求时，对重复事件 `key` 执行幂等 upsert，并确保多对多附件关联正常。
- 验证缺失 `key` 以及同一个事件 `key` 出现不同 payload 时触发完整性失败的行为。
- 验证从事件中正确提取三种附件字段（`attachments`、`new_attachments`、`media`）并保存。
- 验证 SQLite 数据库从 v1 或 v2 迁移到 v3 的原子备份、外键一致性校验以及成功水位的维护。
- 验证媒体文件的 content type 判定（基于文件签名而非服务器返回的 MIME）、空文件拦截、下载临时文件隔离与原子重命名替换逻辑。
- 验证当服务器返回的 JPEG 头部与真实的 PNG 二进制特征码发生冲突时，以文件真实签名为准。
- 验证 `fix-formats` 命令在发现文件后缀与特征码不符时，原子修正本地扩展名和 SQLite 记录，且二次运行无变更。
- 验证两阶段文件下载逻辑：首阶段请求附件 metadata 获取 HTTPS 签名 URL，次阶段流式下载文件内容。
- 验证签名 URL 错误脱敏：在网络请求异常时过滤 URL 信息，避免其泄露到控制台或错误日志中。
- 验证已标记为 `complete` 的附件若在本地被手动删除或大小不符，会自动退回 `pending` 重新下载。
- 验证用户指定的显式区间不能跨越当前水位制造缺口，且历史同步绝不能使水位倒退。
- 确认同步机制不会创建或修改历史遗留的 `children/` 视图。
- 验证每次成功的事件 API 响应均原子化地保存到 `cache/api/events/` 缓存文件中。
- 验证所有可见事件与原始 `member` 元数据完整落库；附件状态独立管理，不提供按孩子或成员的过滤。
- 验证 Photos 导出合并规范库和 Legacy 文件；图片均转换为高品质 RGB JPEG 并附加标准 EXIF，视频写入拍摄时间戳，去重后跳过非图片/视频文件。
- 导出的规范 JPEG 样本必须通过 ExifTool `Validate: OK` 静态校验，防止元数据写入错误的 IFD 导致 Photos 静默拒绝导入。
- 验证多进程并行导出转换，默认 worker 数为 `CPU 核数 - 2`；单 worker 路径用于确定性离线测试。
- `photos-import` 导入器校验：macOS 环境门控、精确相册存在性校验、独立子进程环境变量隔离、dry-run 预检统计、导入新文件、分配已存在重复文件至相册、关闭 Apple Event 自动重试以及写入逐文件导入报告。
- 验证认证失败（状态码 401/403）时不推进成功水位，且不进行网络重试。
- 验证在重新同步时，本地已下载的完好媒体文件不会触发重复下载。

## 真实 API 在线测试

只有在命令行中显式声明环境变量 `BH_ENABLE_LIVE_TESTS=1` 时，标记为 `live` 的测试套件才会运行。该测试将读取本地的 `.env` 配置并生成 pytest 临时目录进行验证，绝不读取或写入用户的正式数据目录，也严禁打印 Cookie 内容。

在线冒烟测试会向 API 发送一个较短的日期窗口请求，以验证：

- 本地配置的身份凭证有效。
- API 响应格式为 JSON 且最外层包含 `events` 键值。
- 返回的事件能够正常解析为字典对象列表。
- 如果请求区间包含附件，会尝试读取其中一个附件，验证返回的 Content-Type 是否在支持列表中，且流式读取的块大小不为零。

## Photos 在线测试

只有在命令行中显式声明环境变量 `BH_ENABLE_PHOTOS_LIVE_TESTS=1` 时，标记为 `photos_live` 的测试套件才会运行。该在线测试仅支持 macOS 系统，且仅会读取系统默认照片库的路径以及现有相册列表，绝对不执行实际导入、修改或删除操作。

涉及向相册写入数据的变更动作不属于 pytest 自动运行的范围。手动验收时，需先指定一个现有的相册名，运行 `photos-import --dry-run` 确认无误，然后执行正式导入并审计生成的 JSON 报告。第二次运行该命令时，必须确认报告中的 `imported` 数量为 0，且相册内的总文件数与候选导出的数量完全吻合。

目前离线测试不模拟文件并发锁的锁竞争，也不涉及真实修改本地照片库的行为。针对并发锁互斥、照片库在运行中的一致性校验以及导入后的实际资产统计，均需要在手动 E2E 阶段进行最终验收。

## 手动 E2E 最终验收

首次对较大日期区间做全历史 backfill 时，不能只依赖自动化测试。按以下清单验收：

1. 指定完整的历史区间运行 `discover` 发现命令，确保 SQLite 数据库中的 attachments 列表与本地 API 缓存目录完全吻合。
2. 运行 `status` 确认当前的同步成功水位已推进至指定的 `until` 日期戳。
3. 运行 `verify --deep`，确认报告中无缺失、零字节或哈希不一致的文件。
4. 运行 `download` 完成全部下载后，再次运行该命令，确认新增下载的文件数为 0。
5. 运行 `fix-formats` 后运行 `verify --deep` 并成功通过，且第二次格式修复命令返回 0 变更。
6. 检查 `photos-export` 生成的 JPEG EXIF 拍摄时间与视频创建时间是否正确，且目录内不含 PDF 或空文件。
7. 运行 `photos-import --album <exact-name> --dry-run`，核实预检返回的新增、重复和跳过数，且未引起 Photos 库的更改。
8. 运行正式 `photos-import` 导入，确认报告中 errors 为 0，且所有候选媒体均进入指定相册；重复运行该导入命令不会导致相册内资产数翻倍。
9. 验证导出的 JSON 文件中事件的最大日期，已达到本次目标日期区间内服务端实际存在的最新事件时间。

## 测试运行命令

```bash
source .venv/bin/activate
ruff check .

# 离线 pytest，利用临时 HOME 避免干扰本地的 Photos 操作锁
TEST_HOME="$(mktemp -d)"
HOME="$TEST_HOME" python -m pytest -q
rm -rf "$TEST_HOME"

# 开启 Bright Horizons 在线 API 测试
BH_ENABLE_LIVE_TESTS=1 python -m pytest -q -m live

# 开启 Photos 在线只读测试（必须保持真实 HOME 路径以读取本机 Photos 库）
BH_ENABLE_PHOTOS_LIVE_TESTS=1 python -m pytest -q -m photos_live
```
