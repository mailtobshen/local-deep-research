# 启用暗网检索（SearXNG 侧配置）

LDR 的开关只控制"要不要去查暗网引擎"。引擎本身必须先在 SearXNG 中启用 ——
这一步在 LDR 之外，需人工操作。

## 背景

`searxng/settings.yml` 是宿主机绑定挂载（`searxng/` → 容器 `/etc/searxng`），
且被 `.gitignore` 排除，因此模板入库、实际配置不入库。

## 步骤

1. 备份现有配置：

       cp searxng/settings.yml searxng/settings.yml.bak.$(date +%s)

2. 把 `searxng/engines-darkweb.yml.template` 的内容追加到 `settings.yml`
   的 `engines:` 段落末尾，保持 YAML 缩进一致（条目为 2 空格缩进的 `- name:`）。

3. 重启 SearXNG（**只重启 searxng-ldr，不要动 ldr-local**，后者的日志是研究
   任务的唯一证据来源）：

       docker compose -f docker-compose.searxng-ldr.yml restart searxng-ldr

4. 验证：在 LDR 设置页点击「测试暗网连接」，或运行连接探测。期望到达 L4。

## 排错

| 探测结果 | 含义 | 处理 |
|---|---|---|
| L1 | SearXNG 未运行 | `docker ps` 查 searxng-ldr 状态 |
| L2 | 引擎块未生效 | 检查 YAML 缩进；确认已重启 searxng-ldr |
| L3 | 取不到 .onion 结果 | Tor 线路问题，见下 |
| L4 | 正常 | — |

L3 常见成因：`ldr-tor` 的 Tor 从未建立过线路。用
`docker logs ldr-tor | grep Heartbeat` 查看，若持续显示
`0 kB sent / 0 kB received` 且 `0 circuits open`，说明 Tor 未真正出网。
注意 `ldr-tor` 的 torrc 把自身出口挂在 Privoxy 之后
（`HTTPSProxy 172.25.128.1:10888`），这条链路本身也需验证。