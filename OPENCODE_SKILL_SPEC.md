# OpenCode Skill 官方开发规范指南 (深度总结版)

经过对 OpenCode 核心引擎（@opencode-ai/plugin）和系统环境的逆向分析，本指南总结了开发一个官方标准 Skill 插件的核心要素。

## 1. 技能部署架构 (Deployment)
OpenCode 采用灵活的技能加载机制，支持项目本地与全局两种模式。

*   **项目本地路径 (推荐用于分享)**：`.opencode/skills/[skill-id]/SKILL.md`。这种方式下，技能跟随代码仓库走，别人克隆仓库后即可直接使用。
*   **全局路径**：`~/.config/opencode/skills/[skill-id]/SKILL.md`（或 `~/.claude/skills/` / `~/.agents/skills/`）。用于跨项目的通用工具。
*   **加载逻辑**：OpenCode 会从当前工作目录向上递归搜索 `.opencode/skills`。

## 2. 核心定义：SKILL.md
这是插件的入口，必须遵循以下标准：

### YAML Frontmatter
```yaml
---
name: universal-reviewer   # 1-64位小写字母、数字和连字符，必须与文件夹名一致
description: A short blurb # 关键：主智能体通过描述来识别并决定是否调用该技能
---
```
*注意：官方规范中 `trigger` 字段并非强制。系统通过 `skill` 工具展示名称和描述，由主智能体进行语义匹配。*

### 结构化 Markdown
推荐使用 `## What I do` 和 `## When to use me` 章节。官方规范强调 `description` 应控制在 1-1024 字符内。

## 3. 多智能体协作 (Agentic Workflow)
OpenCode 的精髓在于通过 `task` 工具派生出子智能体。
*   **目录约定**：建议在根目录创建 `agents/` 文件夹。
*   **文件格式**：使用 Markdown 编写子智能体的系统提示词。
*   **分工模式**：
    *   **Manager/Researcher**: 负责拆解任务和生成计划。
    *   **Worker/Executor**: 负责调用脚本或执行命令。
    *   **Synthesizer**: 负责总结结果并生成最终交付物。

## 4. 外部工具与数据引擎 (Tools & Scripts)
子智能体可以通过 `bash` 工具调用本地脚本：
*   **Manager 模式 (推荐)**：创建一个核心编排脚本（如 `scripts/manager.py`），负责处理数据库、网络请求等“非 AI”任务。主智能体通过 `bash` 调用它来准备数据。
*   **路径管理**：脚本应放在 `scripts/` 下。在子智能体指令中，使用绝对路径或相对于项目根目录的路径引用这些脚本。
*   **依赖管理**：在根目录提供 `requirements.txt` 和 `install.sh`。

## 5. 权限沙箱 (Sandboxing)
OpenCode 为每个加载的技能自动配置权限：
*   **文件访问**：技能通常被授权访问其安装目录 `~/.opencode/skills/[name]/*`。
*   **项目访问**：技能被授权访问当前打开的项目根目录。
*   **安全准则**：不要在脚本中硬编码个人敏感路径，应通过环境变量或相对路径（相对于项目根目录）操作。

---
*本规范基于 OpenCode v1.2.27 版本的逆向分析生成。*
