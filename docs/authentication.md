# My Bright Day 家长认证链

## 当前入口

美国家长登录入口已切换为 [Family Information Center](https://familyinfocenter.brighthorizons.com/)，而不是旧版 My Bright Day 的根登录页，也不同于主要面向英国托儿所的 `families.brighthorizons.com`。

目前生产环境前端的公开配置信息包含以下终点：

```text
apiUrl: https://mbdwgateway.brighthorizons.com/api/
mbdUrl: https://mybrightday.brighthorizons.com/auth/jwt/redirect?url=&jwt=
loginUri: https://familyinfocenter.brighthorizons.com/okta/login
```

家长成功登录后，主页的孩子卡片会向后台发送 `GET /api/account/mbdtoken` 请求，然后跳转打开以下地址：

```text
https://mybrightday.brighthorizons.com/auth/jwt/redirect?url=&jwt=<one-time-token>&childid=<child-id>
```

JWT bridge 会在旧版 My Bright Day 域名下建立家长 session。随后网页继续调用原有 API：

```text
GET /remote/v1/events
GET /remote/v1/obj_attachment
```

底层 API 没有整体迁移，变化主要发生在认证入口。

`childid` 对应本地配置中的 `BH_DEPENDENT_ID`，只用于构造请求 Referer 和初始化家长 session，不代表 API 的数据边界。成功请求可能返回多个 member 的事件，也可能返回没有 member 的共享事件。同步器保存全部可见事件及其 `member` 元数据，不据此过滤或判断照片所有权。

## Cookie 获取流程（受信浏览器辅助，优先）

存在受信本地浏览器自动化环境时，优先使用本流程，家长全程不需要打开 DevTools 或复制任何内容：

1. 用专用 user-data-dir 启动附带远程调试端口的可见 Chrome，打开 [Family Information Center](https://familyinfocenter.brighthorizons.com/)，由家长本人在窗口内完成登录与 MFA。端口与 profile 路径属本地配置，不入公开文档。
2. 家长从孩子卡片进入 My Bright Day（`…/dashboard/parents.html?dependent_id=…`）。两个易错点：该页面空闲态不会发出 `/remote/v1/events` 请求；也不要直接导航 `mybrightday.brighthorizons.com` 根路径，会落到仅接受员工凭证的登录入口。对已打开的页面刷新一次即可稳定触发。
3. 不嗅探请求头（自动化客户端看不到这些请求的 Cookie 头）。通过浏览器的 cookie 读取接口获取 `mybrightday.brighthorizons.com` 域的 cookie，其中 `session` 承载家长会话；将其按域拼接后写入 `BH_SESSION_COOKIE`，`dependent_id` 从页面 URL 读取后写入 `BH_DEPENDENT_ID`。
4. `BH_TADPOLES_UID` 为账号级固定标识，不随会话过期：`.env` 已有旧值时直接复用。Cookie 会话有效期的经验值约为 3 个月，过期后按本流程重取。
5. 全程不得把 cookie 值、JWT 链接或 `access_token` 打印到会话、日志或公共文档中，只写入本地 `.env`。

## Cookie 获取步骤（手动回退）

以下 DevTools 手动流程在没有任何受信浏览器自动化环境时使用：

1. 打开 [Family Information Center](https://familyinfocenter.brighthorizons.com/)，登录家长账号，点击孩子卡片进入 My Bright Day 页面。
2. 按下 `F12` 打开浏览器开发者工具（DevTools），切换到 `Network` 面板，然后在网页上点击切换月份或查看 Memories 以触发接口请求。
3. 找到状态码返回为 `200` 的 `/remote/v1/events` 请求，右键选择 `Copy as cURL`。
4. 从复制的 cURL 命令中，提取出完整的 `Cookie` 头部内容、`X-TADPOLES-UID` 的值以及 Referer 中携带的 `dependent_id`，并依次填入本地的 `.env` 配置文件中。
5. 请勿保存或向他人传播单次有效的 JWT 重定向链接，或者 Family Information Center 的 Okta `access_token`。

## Cookie 失效判定

- **状态码返回 401 或 403**：立即判定会话失效，不同步、不重试，避免请求被拦截导致死循环。
- **响应返回 HTML 页面**：说明会话已被拦截并重定向到登录页，视为认证失败，不同步该响应内容。
- 手机端 My Bright Day App 仍能使用并不代表浏览器 Cookie 依然有效。CLI 客户端依赖的是从浏览器中提取的家长网页会话，需单独维护。

## 调研证据

- Family Information Center 首页在 2026-06-03 仍有更新，并明确建议 enrolled families 使用 My Bright Day。
- 美国 App Store 的 My Bright Day 12.12.1 版于 2026-06-24 更新，产品描述仍包含 Memories、照片和视频。
- 未认证调用旧事件接口返回 `401` 而不是 `404`，说明服务仍然存在，并要求有效 session。该结果只能证明接口当时可达，不能证明其内部鉴权实现从未变化。
