# astrbot_plugin_aling_life_dashboard

WebUI Dashboard，用于观察阿绫插件状态，并在显式授权后管理长期记忆：

- `shared_life_context`：daily 与 period 状态
- `qzone_life_bridge`：随机触发状态、窗口、额度、冷却
- `qzone_auto_like`：发送链路健康，只显示脱敏推断
- `shared_life_memory`：最近 traces 与重复率

## 安全边界

Dashboard 默认只读。只有同时满足“已设置 Dashboard 密码”和“开启 `memory_edit_enabled`”时，才允许新增、修改、归档或恢复 `aling_memory` 记忆。

无论是否开启记忆编辑，本插件都不会：

- 发送 QQ 空间
- 调用 `post_now`
- 调用 `qzone_auto_like` 发送接口
- 调用 `/slc period_refresh`
- 调用 `/slc auto_refresh`
- 修改 AstrBot 或其他插件配置
- 修改 `shared_life_context`
- 调用 LLM

记忆管理不提供永久删除，归档可以恢复；写入前校验字段，并为 `memory_store.json` 创建 `.bak` 备份。

## 配置

默认不启动：

```json
{
  "dashboard_enabled": false,
  "bind_host": "127.0.0.1",
  "bind_port": 7842,
  "dashboard_password": "",
  "memory_edit_enabled": false
}
```

`dashboard_password` 为空时，即使执行 `/ald start` 也不会启动。

## 访问模式

### 模式 A：默认 SSH 隧道

保持：

```json
"bind_host": "127.0.0.1"
```

执行：

```bash
ssh -L 7842:127.0.0.1:7842 root@服务器IP
```

然后打开：

```text
http://127.0.0.1:7842
```

### 模式 B：公网监听

设置：

```json
"bind_host": "0.0.0.0"
```

然后访问：

```text
http://服务器IP:7842
```

强烈建议只在云安全组中放行自己的固定 IP，并设置强密码。

## 命令

- `/ald status`
- `/ald url`
- `/ald start`
- `/ald stop`

`/ald status` 返回当前 period、micro_experience、下一次 period refresh、today_post_count、last_post_at、last_error 与 WebUI URL。

## API

登录后可访问：

- `/api/status`
- `/api/life`
- `/api/qzone`
- `/api/memory`
- `/api/health`
- `/api/continuity-content`
- `/api/continuity-debug`
- `/api/memories`
- `/api/memories/preview`

开启 `memory_edit_enabled` 后还可使用：

- `POST /api/memories`
- `PATCH /api/memories/{id}`
- `POST /api/memories/{id}/archive`
- `POST /api/memories/{id}/restore`
- `POST /api/memory-candidates/{id}/approve`
- `POST /api/memory-candidates/{id}/reject`

未登录访问 `/api/*` 返回 `401`。

API 不返回完整 cookie、`p_skey`、`skey`、`pt4_token`。

## 页面能力

- 顶部状态卡：当前时间、WebUI、shared_life_context、bridge、qzone_auto_like、dry_run、bridge enabled
- 今日 `daily_plan`
- 当前 period 状态与 stale warning
- daily 与 period 刷新倒计时
- QQ 空间 bridge 状态
- 随机触发窗口推断
- 发送链路健康脱敏状态
- 最近历史
- shared_life_memory 最近 traces、days、最近 24h 重复率
- 状态漂移 warning badge
- Bridge 发帖机会评分：Low / Medium / High
- 长期记忆搜索、筛选、查看、新增、修改、归档与恢复
- 候选记忆审核、确认和拒绝
- 编辑长期记忆的重要性、稳定性、敏感等级和有效期；修改有效期后从保存时刻重新计算到期时间
- 查看明确到期时间、证据次数和最近确认信息
- 记忆匹配快速预览（只读模拟，不向 QQ 发送消息）
