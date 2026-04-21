# 错误记录

遇到的错误和异常，包括原因分析和修复方式。

### [已修复] KeyError: 'frequency' — 2026-04-20

**错误**: `KeyError: 'frequency'`

**触发**: 心跳任务状态检查时，`_decide_status` 对心跳任务使用了 `j['frequency']`，但心跳任务没有 `frequency` 字段。

**原因分析**: 系统任务和心跳任务数据结构不同。系统任务有 `frequency` 字段，心跳任务有 `condition` 字段。`_decide_status` 未区分两种任务类型。

**恢复状态**: 已修复。通过检查 `j.get("type")` 区分任务类型，使用 `.get()` 安全取值。

