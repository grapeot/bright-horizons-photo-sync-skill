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
- 规范 JPEG 样本必须通过 ExifTool `Validate: OK`，防止标签落入错误 IFD 后被 Photos 静默拒绝。
- Photos 转码/remux 支持多进程，worker 默认是 CPU 核数减二，单 worker 路径用于确定性离线测试。
- `photos-import` 覆盖 macOS 门控、album 名称校验、命令构造、第三方进程环境隔离、dry-run 摘要和逐文件 JSON 报告。
- 认证失败不重试且不推进水位。
- 已完成媒体重复同步时不再次下载。

## 真实 API 测试

只有设置 `BH_ENABLE_LIVE_TESTS=1` 才运行 `live` marker。测试读取本地 `.env`，使用 pytest 临时目录，不接触正式数据目录，也不打印 cookie。

最小 live test 查询一个短日期窗口，验证：

- 认证有效。
- 响应是 JSON 且顶层包含 `events`。
- 返回值是事件对象列表。
- 若窗口内存在附件，最多读取一个附件，验证允许的 content type 和非空数据块。

## Photos live 测试

只有设置 `BH_ENABLE_PHOTOS_LIVE_TESTS=1` 才运行 `photos_live` marker。该测试只读取当前 Photos library 和 album 列表，不导入、移动或删除资产。

Photos mutation 不放进自动 pytest。手工验收必须指定一个现有 album，先运行 dry-run，再执行导入并核对 JSON 报告。

当前离线测试没有模拟 file-lock contention，也没有自动修改或统计 Photos library。并发拒绝、目标 library 稳定性和 mutation 后资产数量属于手工 E2E 验收。

## E2E 验收

首次正式 backfill 不在默认 pytest 内执行。完成标准：

1. 对用户指定的完整历史范围运行 `discover`，数据库 attachment 集合与 cache 一致。
2. `status` 显示成功水位等于目标 `until`。
3. `verify --deep` 无缺失、零字节或 hash 不一致。
4. `download` 完成后再次运行时新增下载数为 0。
5. `fix-formats` 后 `verify --deep` 通过，第二次格式修复为 0 变更。
6. `photos-export` 的 JPEG EXIF 和视频 creation time 与来源时间一致，且不包含 PDF/空文件。
7. `photos-import --album <exact-name> --dry-run` 返回候选、待导入、跳过和错误数量，且不修改 Photos。
8. 正式 `photos-import` 的报告错误数为 0；每个非错误项进入指定 album，重复运行不增加重复资产。
9. 导出的事件最大日期达到本次目标日期范围内的最新服务端事件。

## 运行命令

```bash
source .venv/bin/activate
ruff check .
TEST_HOME="$(mktemp -d)"
HOME="$TEST_HOME" python -m pytest -q
rm -rf "$TEST_HOME"
BH_ENABLE_LIVE_TESTS=1 python -m pytest -q -m live
BH_ENABLE_PHOTOS_LIVE_TESTS=1 python -m pytest -q -m photos_live
```

默认套件使用临时 `HOME`，避免测试用的 machine-level Photos lock 与正在运行的真实导入互相干扰。Photos live 测试必须恢复真实 `HOME` 才能读取当前 library。
