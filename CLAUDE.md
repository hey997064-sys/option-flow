# option-flow · 项目约束（第三层）

> 全局 `~/.claude/CLAUDE.md`（钱学森工程控制论 harness）默认继承。本文件只写 option-flow 专属覆盖项。

## Superpowers 插件降级（按需手动调，不默认接管）

本项目**不启用 Superpowers 的强制流程**。具体：

- **不**因为"要改代码 / 加功能"就自动触发 `superpowers:brainstorming`、`writing-plans`、`test-driven-development`、`subagent-driven-development` 等技能。
- 这些技能仅在**用户显式要求**时调用（例如用户说"用 superpowers 流程做这个"、"走 TDD"、"先 brainstorm"）。
- 默认工作方式仍遵循全局 harness：原则 8（必要性优先）→ 直接、最小复杂度路径。

**理由**（对应全局模式 G / J）：option-flow 是 **skill 形态**项目（用户手动 invoke、用户即 validator），不是无人值守 pipeline。Superpowers 的"任何构建任务前强制 brainstorm + 红绿 TDD + 子代理驱动"是为长周期自主开发设计的，套到本项目会让流程明显变重，且与"skill 运行期不堆工程化验证"的设计哲学冲突。

> Superpowers 入口技能 `using-superpowers` 自身声明：用户 CLAUDE.md 指令优先级最高。本约束据此生效。
