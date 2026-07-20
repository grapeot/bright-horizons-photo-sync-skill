# Bright Horizons Photo Sync 开发指南

## 项目范围

本公开仓库包含 My Bright Day 备份 CLI 及其 Agent Skill。该工具会保存已认证家庭账号可见的所有事件和附件。API 返回的 `member` 字段仅作为元数据保留，不作为所有权划分或下载过滤的依据。

## 仓库结构

- `src/bright_horizons_photo_sync/`：可复用的 CLI 实现。
- `tests/`：离线测试及显式启用的在线测试。
- `docs/prd.md`：产品需求与成功标准。
- `docs/rfc.md`：架构设计、数据契约与失败语义。
- `docs/test.md`：验证策略与发布门槛。
- `docs/working.md`：开发日志与经验总结。
- `skills/bright_horizons_photo_sync.md`：Agent 运行契约。
- `data/`: 本地私有数据，已完全加入 Git 忽略列表。

## 开发规则

- 使用项目的虚拟环境：`source .venv/bin/activate`。
- 使用 `uv pip install -e '.[dev]'` 安装开发依赖。在 macOS 上，如需集成 Photos 导入功能，请使用 `uv pip install -e '.[dev,photos]'`。禁止直接使用 `pip install`。
- 严禁提交 `.env`、Cookie、账号标识符、API 响应、SQLite 数据库、媒体文件、Photos 导入报告、绝对用户路径或真实的家庭元数据。
- 默认测试严禁访问网络或修改 Photos 数据库。Bright Horizons 真实 API 测试与 Photos 在线测试分别需要单独的显式环境变量授权。
- 保证区间完整性契约：遇到认证、数据截断、网络、完整性或存储错误时，程序必须返回非零状态码，且不得推进同步成功水位。
- 保留所有 API 可见的事件。不要添加针对特定孩子或成员的过滤规则，也不要自行推断所有权。
- 将附件 ID 作为媒体的唯一标识。`photos-export` 可以创建导出的衍生文件，但绝对不能修改原始对象。
- `photos-import` 的目标 album 必须精确匹配且已存在。命令必须使用当前选中的 Photos library，不能直接修改 Photos 的 SQLite 数据库。
- 当底层协议或契约变更时，同步更新 PRD、RFC、测试策略、README、`skills/bright_horizons_photo_sync.md` 以及开发日志。
- 只有在用户明确授权的前提下，才能执行 commit、push、提交 PR、合并分支或修改远程仓库的操作。

## 验证方法

本地验证命令：

```bash
source .venv/bin/activate
ruff check .
TEST_HOME="$(mktemp -d)"
HOME="$TEST_HOME" python -m pytest -q
rm -rf "$TEST_HOME"
```

在线测试需要手动开启：

```bash
BH_ENABLE_LIVE_TESTS=1 python -m pytest -q -m live
BH_ENABLE_PHOTOS_LIVE_TESTS=1 python -m pytest -q -m photos_live
```

在发布公开内容前，务必扫描所有追踪的文件，确保没有泄露凭证、个人姓名、账号 ID、绝对路径、私有 API 响应负载以及家庭实际的统计数据。
