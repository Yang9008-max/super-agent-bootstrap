# Linux 个人 AI Agent 环境一键恢复

第一版支持 Codex CLI。将当前电脑的默认模型、接口地址、推理设置、个人 Skills 和全局 `AGENTS.md` 导出，打成一个自包含 Bash 安装包。导出工具也可以在 Windows 上运行。

## 当前已生成的个人安装包

文件位于项目根目录下：

```text
.private/portable-agent/profile.json
.private/portable-agent/super-agent-personal.sh
```

将 `super-agent-personal.sh` 复制到目标 Linux 电脑，在它所在目录执行：

```bash
bash super-agent-personal.sh
```

安装包自带管理脚本和个人配置，不需要目标机提前安装 Git、Node.js、Python 或克隆本项目。需要 Bash，以及 curl/wget 中的一个；必须能访问下载服务。支持 Linux x86_64、aarch64。已通过 GitHub Actions 的 Ubuntu 联网安装测试；其他发行版、架构与网络环境仍需在目标机确认。

安装过程使用当前 Linux 用户，不调用 sudo。请以最终使用 Codex 的用户执行。步骤如下：

1. 优先使用现有 Python 3.11+；缺少时，通过 uv 获取用户目录下的 Python。
2. 检查安装包完整性、配置结构和目标路径。
3. 复用已有 Codex；没有时运行 OpenAI 官方独立安装器。
4. 合并个人模型配置，将 Skills 写入 `~/.agents/skills/`。
5. 将管理命令放到 `~/.local/bin/super-agent`，输出本机检查结果。

配置使用 Codex 默认的 `~/.codex/`，也尊重已有 `CODEX_HOME`。不会复制源电脑的工程信任列表、Windows 设置、通知命令、聊天历史、登录凭据和桌面应用状态。MCP 需要逐个适配路径和认证方式，第一版导出时会提示跳过。插件市场和命名 profiles 也不在第一版同步范围内。

## 模型认证

个人接口地址和模型已经包含在安装包里。第三方接口统一使用环境变量认证；如果原提供商没有设置 `env_key`，导出时使用 `OPENAI_API_KEY`，同时关闭该自定义提供商的 `requires_openai_auth`。

在 Linux 中输入密钥，不将它写入安装包或命令历史：

```bash
read -rsp 'API Key: ' OPENAI_API_KEY; printf '\n'; export OPENAI_API_KEY
codex
```

这个变量只在当前 Shell 及其子进程生效。关闭终端后需要重新提供，或者使用你已有的密钥管理方式注入环境变量。已有自定义 `env_key` 的提供商继续使用原变量名，安装输出会列出名字。

如果配置的是 OpenAI 官方提供商，使用 `codex login` 完成账号认证。安装器不会从本电脑复制 `auth.json`。

## 检查、重复运行与备份

```bash
# 只校验安装包；缺少 Python 时仍可能下载运行时。
bash super-agent-personal.sh --check-only

# 只恢复配置，不安装 Codex。
bash super-agent-personal.sh --skip-codex

# 检查配置、Codex 是否存在和认证变量是否缺失。
~/.local/bin/super-agent doctor
```

`doctor` 不发送模型请求，因此不产生推理费用，也不代表已经验证接口可用。

重复运行会跳过相同文件。发生覆盖前，原文件保存在：

```text
~/.local/state/super-agent/backups/<UTC 时间>/
```

`manifest.json` 记录每个目标文件及其备份。`backup` 为 null 的条目表示目标文件原来不存在；其他条目可从同目录中的相对路径找到旧文件。恢复配置发生写入失败时，会尝试自动回滚本次已写入的文件。

现有目标机 TOML 设置按键合并，未被个人配置覆盖的设置保留。需要修改配置文件时会重新格式化 TOML，原始格式和注释在备份中。同步 Skills 会更新同路径文件，不会自动删除目标机已有的其他 Skills 或旧文件。若目标机已在旧目录 `~/.codex/skills/` 安装同名 Skills，需要自行整理重复项。

如果当前 Shell 找不到新命令：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

安装器不会主动修改你的 Shell 启动文件。可以按自己的 Shell 配置习惯持久化 PATH。

## 更新你的个人环境包

源电脑配置变化后，使用新文件名重新导出、打包。工具拒绝覆盖已有导出文件，以免误覆盖旧快照。

Windows 当前项目中可运行：

```powershell
py -3.13 tools/portable_agent.py export --output .private/portable-agent/profile-v2.json
py -3.13 tools/portable_agent.py bundle .private/portable-agent/profile-v2.json --output .private/portable-agent/super-agent-personal-v2.sh
```

在 Linux 中，已安装的管理命令同样可以导出、打包：

```bash
~/.local/bin/super-agent export --output profile-v2.json
~/.local/bin/super-agent bundle profile-v2.json --output super-agent-personal-v2.sh
```

默认收集 Codex home 下的 `skills/` 以及 `~/.agents/skills/`。使用 `--skills-root` 可以改为指定目录；可重复传入，后面的同名 Skill 优先。

安装包会携带个人提示词、接口地址和 Skills 源文件，应作为个人配置保存。导出会排除已知凭据文件、符号链接、缓存和依赖目录，并拒绝明显的密钥内容；这不等于对任意脚本和自定义文件的完整秘密审计。

## 分发成远程一行命令

GitHub 分发使用两个仓库：公开的安装代码 `Yang9008-max/super-agent-bootstrap`，以及私有的个人安装包 `Yang9008-max/super-agent-personal`。Linux 上运行：

```bash
curl -fsSL https://raw.githubusercontent.com/Yang9008-max/super-agent-bootstrap/main/install.sh | bash
```

这个公开入口会自动安装 GitHub CLI，首次按提示通过浏览器登录你的 GitHub 账号，再下载私有 Release 中的个人安装包并校验 SHA-256。终端提示网址和一次性验证码时，可在自己的电脑浏览器完成登录，无需在服务器上安装浏览器。已有有效 GitHub 登录时会复用。

公开入口可以通过管道运行；个人自包含包本身仍需先下载成文件再运行。SHA-256 用于发现传输损坏，不替代下载来源认证。无人值守环境可通过 `GH_TOKEN` 提供私有仓库的读取权限。

## Skills 的 Linux 依赖

安装器同步 Skills 文件，不自动猜测和安装每个 Skill 的外部应用、硬件驱动、串口工具或交叉编译器。发现 Windows 路径或 `.exe` 引用时，导出报告会提示。Windows 专用 Skill 不会因为文件同步而变成 Linux 版本。

18 项测试已在本机 Git Bash 和 GitHub Actions Ubuntu 中通过，涵盖配置合并、备份、重复执行、写入失败回滚、路径穿越/符号链接拒绝、安装包校验、Bash 启动、uv 回退以及 GitHub Release 下载流程。Ubuntu 还实际运行官方安装器下载安装 Codex 并验证启动成功。未调用模型推理，也没有更改源电脑的 Codex 配置。

测试记录：https://github.com/Yang9008-max/super-agent-bootstrap/actions/runs/43725744770

## 官方参考

- [Codex CLI 安装](https://learn.chatgpt.com/docs/codex/cli)
- [Codex 配置](https://learn.chatgpt.com/docs/config-file/config-basic)
- [Codex 认证](https://learn.chatgpt.com/docs/auth)
- [Skills](https://learn.chatgpt.com/docs/build-skills)
- [uv 安装选项](https://docs.astral.sh/uv/reference/installer/)
