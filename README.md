# 一行命令恢复你的 AI 工作环境

[![Linux verification](https://github.com/Yang9008-max/super-agent-bootstrap/actions/workflows/verify.yml/badge.svg)](https://github.com/Yang9008-max/super-agent-bootstrap/actions/workflows/verify.yml)

面向 Linux 的个人 Codex CLI 环境安装器。公开仓库只存安装代码，个人模型、接口地址和 Skills 放在私有仓库的 Release 中。

## 无需 GitHub 登录的服务器方式

如果已经拿到自己的专属服务器安装链接，直接使用那条链接即可，无需登录 GitHub。安装包由自己的 HTTPS 服务器提供，模型设置和 Skills 不会放在公开仓库。部署结构和模板见[服务器托管说明](docs/server.md)。

以下 GitHub 安装方式保留作为备用入口。

## 直接安装

在 Linux 终端粘贴：

```bash
curl -fsSL https://raw.githubusercontent.com/Yang9008-max/super-agent-bootstrap/main/install.sh | bash
```

首次运行按提示登录 GitHub：终端会显示一个网址和一次性验证码，在浏览器打开网址、填入验证码，用拥有个人配置仓库权限的账号确认。没有图形界面的服务器也可以在自己的电脑浏览器里完成。

之后会下载个人安装包、安装 Codex，并恢复模型设置和 Skills。已有配置会先备份。模型接口的 API Key 需要单独提供，安装包不包含登录凭据。

默认个人配置仓库是私有的 [`Yang9008-max/super-agent-personal`](https://github.com/Yang9008-max/super-agent-personal)，其他账号需要获得该仓库的访问权限才能使用这份个人环境。

## 安装后

如果使用第三方模型接口，在终端输入密钥：

```bash
read -rsp 'API Key: ' OPENAI_API_KEY; printf '\n'; export OPENAI_API_KEY
~/.local/bin/codex
```

这个环境变量在当前终端有效。OpenAI 官方账号登录则运行 `~/.local/bin/codex login`。

检查当前状态：

```bash
~/.local/bin/super-agent doctor
```

## 工作方式

- 支持 Linux x86_64、aarch64，要求 Bash 和 curl/wget。
- 自动准备 Python 3.11+、GitHub CLI 和 Codex；不需要提前安装 Node.js，不调用 sudo。
- GitHub CLI 缺少时只安装在当前用户目录，按发布校验文件验证下载完整性。
- 个人安装包经过 SHA-256 校验后执行，私有配置始终通过 GitHub 认证下载。
- 模型配置和 Skills 覆盖前备份，相同文件跳过；写入失败会尝试回滚。
- `doctor` 只做本机检查，不发送模型请求。

## 使用自己的配置仓库

下载脚本后可以指定其他仓库：

```bash
curl -fsSL https://raw.githubusercontent.com/Yang9008-max/super-agent-bootstrap/main/install.sh -o install.sh
bash install.sh --repo OWNER/PRIVATE-REPO
```

目标仓库的最新 Release 需要包含 `super-agent-personal.sh` 和 `super-agent-personal.sh.sha256` 两个文件。可以通过 `--tag v0.1.0` 固定版本。

导出和打包命令、认证方式、备份目录和限制见[详细说明](docs/guide.md)。Windows 导出使用 Python 3.11+；目标安装器优先支持 Linux。每个 Skill 所需的桌面应用、硬件驱动和编译器仍需单独检查。

## 开发验证

```bash
python3 -m unittest discover -s tests -v
bash -n install.sh
```

GitHub Actions 运行 Ubuntu 单元/集成测试，以及在一次性运行器中实际下载安装 Codex 的网络验证。该验证不访问个人配置仓库、不包含 API Key，也不调用模型推理。

已验证：18 项测试通过，Ubuntu 实际下载、安装和启动 Codex 成功。[测试记录](https://github.com/Yang9008-max/super-agent-bootstrap/actions/runs/43725744770)。
