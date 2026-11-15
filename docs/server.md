# 通过自己的服务器安装，无需 GitHub 登录

个人安装包现在也可以通过自建 HTTPS 站点分发。用户拿到一条包含专属下载码的命令，在 Linux 终端粘贴即可。

实际地址保存在本地忽略目录 `.private/portable-agent/server-deployment.json` 和 `install-command.txt`。专属地址不写进公开 GitHub 仓库。

使用方式如下，其中地址应替换成自己的完整下载链接：

```bash
curl -fsSL 'https://你的服务器/专属下载码/install.sh' | bash
```

安装器直接从同一 HTTPS 站点下载个人安装包和校验文件，验证成功后安装 Codex 并恢复个人设置。无需 GitHub 账号或 GitHub CLI。模型 API Key 继续单独提供。

## 服务器结构

- `deploy/portable-agent-server-install.sh`：客户端入口模板，准备发布时替换 `__BUNDLE_BASE_URL__`。
- `deploy/portable-agent-host.py`：针对已经安装 Caddy 的 Ubuntu 主机，添加独立下载站点；通过 SSH 以 root 执行。
- `/srv/portable-agent/<下载码>/`：仅包含 `install.sh`、`super-agent-personal.sh` 和 `super-agent-personal.sh.sha256`。
- `/etc/caddy/portable-agent.caddy`：root 和 caddy 组可读，包含匹配这三个文件的路由。
- `/var/backups/portable-agent/<UTC 时间>/`：仅 root 可读的原始配置备份和校验记录。

站点只开放上述三个明确匹配的文件；根目录、未带下载码的路径、目录列表和其他文件均返回 404。下载响应不缓存，访问日志丢弃。保留原有其他 Caddy 站点，QQ 机器人服务独立运行。

下载链接本身相当于取件码，知道完整地址的人就能取回配置。需要撤销时，可删除对应路由和目录或更换下载码。API Key 不包含在安装包中。

## HTTPS 域名

有自己的域名时，将它的 DNS A 记录指向服务器 IP。没有域名时，可以使用 sslip.io 提供的 IP 映射域名；它依赖第三方 DNS 服务，日后可迁移到自有域名。服务器的 80、443 端口需要可以从外部访问，以便 Caddy 申请和续期证书。

## 检查

只校验安装包、暂不恢复配置：

```bash
curl -fsSL 'https://你的服务器/专属下载码/install.sh' | bash -s -- --check-only
```

如果 Codex 已经装好，只同步配置：

```bash
curl -fsSL 'https://你的服务器/专属下载码/install.sh' | bash -s -- --skip-codex
```

## 发布更新

源电脑重新导出、打包后，通过 SSH 更新上述个人安装包和 SHA-256 文件。先在独立目录写齐新文件并校验，再替换在线文件；不要把个人配置或专属链接提交到公开仓库。当前托管使用的 Caddy 2.6.2 关闭了管理接口，因此首次加入新站点需要校验配置后短暂重启 Caddy；更新同一路由下的文件不需要重启。

## 参考

- [Caddy 自动 HTTPS](https://caddyserver.com/docs/automatic-https)
- [sslip.io 的 IP 映射 DNS](https://sslip.io/)
