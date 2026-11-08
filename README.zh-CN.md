# Agent Web Reader

给 Agent 使用的公开网页检索与阅读工具：搜索 → 选择一个候选 → 返回有来源、长度限制和截断标记的文本。真实路径将获取、扫描、离线解析分开；API Key 留在可信宿主机，解析器运行在无网卡 Linux 虚拟机中。

这是实验性源码候选。先运行不需要密钥或虚拟机的离线示例：

```console
python -I -S -B demo.py
python -I -S -B poc/applications/public_web_agent_v01/test_agent.py
python -I -S -B -O poc/applications/public_web_agent_v01/test_agent.py
```

需要 Python 3.10+。示例返回 `mode=fixture / source.mode=synthetic`，只读包内合成数据；不搜索、不访问示例网站、不启动虚拟机，也不伪造真实执行回执。

真实运行见 [Windows 配置说明](docs/LIVE_SETUP.md)，要自行准备 VirtualBox、Alpine、签名 APK、Defender 和本机 curl 指纹。没有自动安装器，本发布副本尚未重跑真实服务/虚拟机；原私有版本的联通结果不作为新版实测。

Agent 调用 `python -I -S -B agent_web.py`，stdin 一个 JSON：

```json
{"op":"search","query":"公开问题"}
```

保存返回的 `search_id`，再发送：

```json
{"op":"read","search_id":"返回的32位十六进制ID","rank":1}
```

最多五个候选；每个搜索只预约一次正文读取；失败不自动重试；会话两小时过期。本地每日最多预约 20 次搜索，不等于整个账户的计费上限。离线演示命令额外加 `--fixture`。

它的价值是把现有搜索 API、解析器、扫描与虚拟机组装成可检查的 Agent 接口。不是自建搜索引擎，也没有证明优于商业工具。支持公开关键词和选定静态 HTTPS UTF-8 HTML，不支持登录、PDF、重定向、动态脚本或任意 URL。正文最多 4,000 字符，可能带导航或截断代码。

当前严格查询绑定会拒绝供应商改写过的查询回显，例如删除 `site:` 操作符；请求可能已计费，不能因接口停止就盲目重发。

网页内容仍可能含提示注入；Agent 若有完整宿主权限，也能绕过工具读取本地文件或密钥。需要靠另外的 OS/harness 权限限制 Agent。本工具的本地哈希不是签名审计链，扫描也不保证内容无害。

MIT 许可，署名 **Agent Web Reader Contributors**。包内不含 Key、个人路径、真实响应、截图、虚拟机或二进制依赖。详见 [英文说明](README.md)、[架构](docs/ARCHITECTURE.md)、[测试范围](docs/VALIDATION.md)及 [第三方说明](THIRD_PARTY_NOTICES.md)。
