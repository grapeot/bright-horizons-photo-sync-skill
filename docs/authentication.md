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

## Cookie 获取步骤

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
