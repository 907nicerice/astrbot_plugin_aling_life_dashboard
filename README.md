# astrbot_plugin_aling_life_dashboard

只读 WebUI Dashboard，用于观察：

- `shared_life_context`：daily 与 period 状态
- `qzone_life_bridge`：随机触发状态、窗口、额度、冷却
- `qzone_auto_like`：发送链路健康，只显示脱敏推断
- `shared_life_memory`：最近 traces 与重复率

## 只读原则

本插件只读文件与运行时配置，不会：

- 发送 QQ 空间
- 调用 `post_now`
- 调用 `qzone_auto_like` 发送接口
- 调用 `/slc period_refresh`
- 调用 `/slc auto_refresh`
- 修改配置
- 修改 `shared_life_context`
- 调用 LLM

页面没有发送按钮，API 也不提供任何写操作。

## 配置

默认不启动：

```json
{
  "dashboard_enabled": false,
  "bind_host": "127.0.0.1",
  "bind_port": 7842,
  "dashboard_password": ""
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
