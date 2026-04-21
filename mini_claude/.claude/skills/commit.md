---
name: commit
description: 自动分析变更并生成规范的 git commit
---

# /commit — 智能提交

请执行以下步骤：

1. 运行 `git status` 和 `git diff --staged`（如果没有 staged 文件则用 `git diff`）查看所有变更
2. 运行 `git log --oneline -5` 了解本项目的 commit message 风格
3. 分析变更内容，判断类型：
   - `feat`: 新功能
   - `fix`: 修复 bug
   - `refactor`: 重构（不改变行为）
   - `docs`: 文档变更
   - `test`: 测试相关
   - `chore`: 构建/工具/依赖等杂项
4. 生成 commit message，格式：`type: 简短描述`
   - 中文描述，50 字以内
   - 如有必要加 body 说明 why
5. 如果有未 staged 的文件，先用 `git add` 添加相关文件（不要 `git add .`，逐个添加）
6. 执行 `git commit`

注意事项：
- 不要提交 `.env`、密钥、`node_modules`、`__pycache__` 等文件
- 如果变更太大且涉及多个不相关改动，建议拆分为多次提交
- 执行前向用户确认 commit message
