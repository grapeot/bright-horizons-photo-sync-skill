# My Bright Day 家长认证链

## 当前入口

美国家长入口是 [Family Information Center](https://familyinfocenter.brighthorizons.com/)，不是旧 My Bright Day 根登录页，也不是主要面向英国 nursery 的 `families.brighthorizons.com`。

当前生产前端公开配置包含：

```text
apiUrl: https://mbdwgateway.brighthorizons.com/api/
mbdUrl: https://mybrightday.brighthorizons.com/auth/jwt/redirect?url=&jwt=
loginUri: https://familyinfocenter.brighthorizons.com/okta/login
```

登录后，孩子卡片会请求 `GET /api/account/mbdtoken`，再打开：

```text
https://mybrightday.brighthorizons.com/auth/jwt/redirect?url=&jwt=<one-time-token>&childid=<child-id>
```

JWT bridge 在旧 My Bright Day 域名建立家长 session。随后网页继续调用：

```text
GET /remote/v1/events
GET /remote/v1/obj_attachment
```

因此 API 没有整体迁移；变化主要在认证入口。

`childid` / `BH_DEPENDENT_ID` 用于建立家长 session 和构造 Referer，不代表事件 API 的数据边界。一次成功请求可能返回账号或班级范围内的多个 member，也可能返回没有 member 的共享事件；同步器保留所有可见事件和 API 自带的 member metadata，不据此过滤或判断照片属于谁。

## Cookie 获取

1. 从 Family Information Center 登录并从孩子卡片进入 My Bright Day。
2. DevTools -> Network 中触发月份或 Memories 加载。
3. 找到返回 200 的 `/remote/v1/events` 请求并 `Copy as cURL`。
4. 把完整 Cookie header、`X-TADPOLES-UID` 和 Referer 的 `dependent_id` 写入本地 `.env`。
5. 不保存或传播 JWT redirect URL、Family Information Center bearer token。

## Cookie 失效判断

- `401` / `403`：立即视为失效，不重试。
- 返回 HTML 或登录页而不是 JSON：视为认证失败，不能保存为事件或媒体。
- My Bright Day App 仍可用不代表浏览器 cookie 仍有效；CLI 使用的是浏览器家长 session。

## 调研证据

- Family Information Center 首页在 2026-06-03 仍更新，并明确推荐 enrolled families 使用 My Bright Day。
- 美国 App Store 的 My Bright Day 版本 12.12.1 于 2026-06-24 更新，描述仍包含 Memories、照片和视频。
- 未认证调用旧事件接口返回 401 而不是 404，证明服务存在且要求有效 session。
