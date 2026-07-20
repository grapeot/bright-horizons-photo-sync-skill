# 测试策略

## 默认离线测试

`python -m pytest -q` 不允许访问网络，覆盖：

- 可配置 IANA 时区、Pacific DST 日期边界和 Photos EXIF offset。
- 半开区间到旧 API 包含上界的转换。
- 7 天窗口规划和满页递归二分。
- 相邻窗口重复事件 key 的幂等 upsert。
- 缺失 key 和同 key 冲突 payload 的失败行为。
- 三种附件字段提取与多对多关系。
- SQLite v1/v2→v3 备份迁移、外键检查、状态水位和确定性导出。
- 媒体 content type、空文件、临时文件和原子 rename。
- 服务端 JPEG header 与 PNG magic bytes 冲突时以文件签名为准。
- `fix-formats` 原子修正既有对象扩展名和数据库 MIME，第二次运行无变化。
- 附件 metadata JSON 到 HTTPS signed URL 的两跳下载。
- signed URL 网络错误脱敏，不进入异常文本。
- 已完成对象丢失后自动回到 pending。
- 显式日期区间不能跨过当前水位制造缺口，历史同步不能让水位倒退。
- 同步不创建或修改历史 `children/` 视图。
- 每个成功事件窗口原子写入完整 response cache。
- 全部可见事件和 API member metadata 进入数据库；attachment 状态可独立查询，不提供按孩子过滤。
- Photos 导出合并规范库和 Legacy；图片统一 JPEG+标准 ExifIFD，视频写 creation time，并跳过重复内容。
- 每个测试 JPEG 必须通过 ExifTool `Validate: OK`，防止标签落入错误 IFD 后被 Photos 静默拒绝。
- Photos 转码/remux 支持多进程，worker 默认是 CPU 核数减二，单 worker 路径用于确定性离线测试。
- 认证失败不重试且不推进水位。
- 已完成媒体重复同步时不再次下载。

## 真实 API 测试

只有设置 `BH_ENABLE_LIVE_TESTS=1` 才运行 `live` marker。测试读取本地 `.env`，使用 pytest 临时目录，不接触正式数据目录，也不打印 cookie。

最小 live test 查询一个短日期窗口，验证：

- 认证有效。
- 响应是 JSON 且顶层包含 `events`。
- 每个事件时间位于请求范围内或明确记录 API 的边界异常。
- 最多下载一个附件并验证类型、大小与 SHA-256。

## E2E 验收

首次正式 backfill 不在默认 pytest 内执行。完成标准：

1. `discover --since 2024-01-01 --until <明天>` 返回 0，数据库 attachment 集合与 cache 一致。
2. `status` 显示成功水位等于目标 `until`。
3. `verify --deep` 无缺失、零字节或 hash 不一致。
4. `download` 完成后再次运行时新增下载数为 0。
5. `fix-formats` 后 `verify --deep` 通过，第二次格式修复为 0 变更。
6. `photos-export` 的 JPEG EXIF 和视频 creation time 与来源时间一致，且不包含 PDF/空文件。
7. 导出的事件最大日期达到本次目标日期范围内的最新服务端事件。

## 运行命令

```bash
source .venv/bin/activate
python -m pytest -q
ruff check .
BH_ENABLE_LIVE_TESTS=1 python -m pytest -q -m live
```
