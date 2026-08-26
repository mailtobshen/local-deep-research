`run_preflight_check` 的暗网 gate 现在以「全局开关 OR 主引擎下拉框选了暗网」为触发条件；用户未选择暗网时跳过探测并返回 `skipped` 状态行，而不是静默消耗 60 秒超时。前端同步：全局开关关闭时 `include_darkweb` 勾选框显示但 disabled 灰显。
