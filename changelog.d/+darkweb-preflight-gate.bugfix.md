type: bugfix
title: 暗网 preflight gate — 未选择时不探测
description: |
  `run_preflight_check` 的暗网 gate 改为三信号 OR
  (`search.tool == "darkweb"` OR `snapshot.darkweb.enabled`)。
  用户未选择暗网时, preflight 跳过暗网探测并返回
  `skipped` 状态行, 而非静默消耗 60s 超时。
  前端同步: 全局开关关闭时, `include_darkweb` 勾选框
  显示但 disabled 灰显 (与后端跳过语义对齐)。