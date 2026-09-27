# 帖 03｜国内网络拉不动 GitHub Release：一次失败排查 + 镜像替代方案

<!-- type: question -->
> 用途：直接复制粘贴到课程群/讨论区。属于「求助 + 共享解法类」——普适价值最高。
> 素材来源：C2 挑战真实工作（tectonic 下载失败 → 改用清华 CTAN 镜像装 MiKTeX）。

---

## 【帖文正文】

**主题：`curl` 拉 GitHub Release 返回 `HTTP:000 size:0`，别急着怀疑代码——这是一个可复现的网络环境问题**

**上下文**
C2 要求交一份**可独立编译**的 `paper.tex`。我选了 `tectonic`（单文件、按需拉宏包、不用装几个 G 的 TeX Live），准备从它的 GitHub Releases 下载 Windows 二进制。

**现象**
```bash
curl -sL -w "HTTP:%{http_code} size:%{size_download}\n" -o tectonic.zip \
  "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.15.0/tectonic-0.15.0-x86_64-pc-windows-msvc.zip"
# 输出：HTTP:000 size:0
```

`HTTP:000` 是 curl 的"连接根本没建立成功"，**不是 404**。也就是说请求没到达 GitHub。

**已尝试 / 已排除**
| 排查项 | 做法 | 结果 |
|---|---|---|
| 是不是 URL 写错了？ | 逐字段核对 release tag / 文件名 | tag 与资产名都对，**排除** |
| 是不是 curl 的 SSL 问题？ | 加 `-v` 看握手 | TLS 握手阶段就断了，说明是链路层，**排除证书问题** |
| 是不是 GitHub 整体不可达？ | `curl -o /dev/null -w "%{http_code}" https://github.com/` | 也是 **000** → 确认是**域名级不可达** |
| 是不是我这台机器断网？ | 同时测清华 TUNA / PyPI / 阿里云 | `200 / 200 / 301` → **网是通的** |

**结论（可复现）**：本机（以及大概率整个校园网/家宽环境）**对 `github.com` 的访问被阻断**，但对国内镜像站正常。所以「网速慢」这个猜测是错的——**是特定域名的可达性问题，不是带宽问题**。

**具体卡点**：我卡在「**拿不到 LaTeX 二进制引擎 → `paper.tex` 编译不了**」这一步。而这个卡点有两个陷阱：
1. 我一开始把变量选错了——以为要调的是 URL，实际是**链路可达性**；
2. 换源之后还有第二个更隐蔽的卡点：**包管理器会吞掉"后续组件拉取失败"并报告成功**，让你以为环境好了。

**通用解法（三步判断法）**
1. **先分层验证可达性**，再改代码：
   ```bash
   for h in github.com mirrors.tuna.tsinghua.edu.cn pypi.org mirrors.aliyun.com; do
     printf "%-40s %s\n" "$h" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://$h/)"
   done
   ```
   一旦看到某个域名 `000`、其他 `200`，就**不要去调代码参数了**，直接换源。
2. **换可达源**：我最终用清华 CTAN 镜像的 MiKTeX 安装包（148 MB，14 秒下完，约 10 MB/s）：
   ```bash
   curl -L -o miktex-setup.exe \
     "https://mirrors.tuna.tsinghua.edu.cn/CTAN/systems/win32/miktex/setup/windows-x64/basic-miktex-25.12-x64.exe"
   ```
3. **让安装器也走镜像**（这一步最容易被漏掉——包管理器装完会继续去官方源拉宏包，如果官方源不可达，安装会静默残废）：
   ```bash
   ./miktex-setup.exe --portable="D:/tex" --unattended --auto-install=yes \
     --remote-package-repository="https://mirrors.tuna.tsinghua.edu.cn/CTAN/systems/win32/miktex/tm/packages/"
   ```

**一个值得记下的教训**
我第一次是直接用 `winget install MiKTeX.MiKTeX`。它报告 **`Successfully installed`**——但装完去查，`MiKTeX/miktex/bin/x64/` **是空的**，只有数据目录和 `biber.exe`。因为安装器从 `miktex.org` 下了主包，但**后续组件拉取失败了，而它没有把失败上报为失败**。

> **结论：`Successfully installed` ≠ 装成功了。装完一定要验证可执行文件是否真的存在，并跑一次最小 smoke test。** 静默残废比显式报错危险得多。

**想请教群里的**
1. 除了清华 TUNA，你们还用哪些镜像？（我已知 TUNA / 阿里云 / 中科大 USTC，想补一个 arXiv 和 HuggingFace 的）
2. 有没有办法**一次性配置**让 pip / npm / winget / TeX 全部走镜像，而不是每换个工具就要重新踩一遍？大家是写脚本、还是用代理、还是每工具各配各的？
3. 遇到「工具自己吞掉错误还报成功」的情况，你们有没有通用的**验证装载**习惯（比如一律跑 `--version` + 一个真实任务）？

---

## 【当时的思考】

- **情绪上的拐点**：连续几次 `HTTP:000` 时，我的第一反应是"我是不是把 tag 写错了"，于是反复改 URL。这是**在错误的层面上迭代**——变量根本不是 URL，是链路可达性。后来逼自己先做分层验证，10 秒定位。
- **提炼出的一条元规则**：**当同一个错误重复出现 ≥3 次，就停止修参数，开始验证「我假设成立的前提」**。
- **为什么这条值得发群**：它跟课程内容无关，但**几乎每个人都会撞上**，而且撞上之后的默认反应（改参数、重试、怀疑自己）是错的。这类"环境类"经验在群里共享的边际收益最高。
- **顺便**：MiKTeX 报成功但没装上，这件事我当时差点就信了。**如果我当时直接开始编译，报错会指向 `xelatex not found`，我会去查 xelatex 而不是去查安装器**——错误信息会把我带到更远的地方。所以我把"装完必须 smoke test"也写进了帖子。

---

## 【后续结果】（已回填 · 真实值）

| 项 | 内容 |
|---|---|
| 发帖时间 | **2026-09-26** |
| 回应人 | **无**（本条原被判定为"最可能收到镜像清单类回应"，实际 0 回应） |
| 回应的实质内容 | **无**（没有补到任何新镜像源或配置脚本） |
| 是否推动了我改方案 | **否** |
| 沉淀结论 | **未形成群内沉淀**。本条通用解法（三步判断法 + 装完必做 smoke test）已独立成立 |

> ⚠️ 本条**已发出、0 回应（已确认）**。它的 0 特别值得记录：
> **它是 5 条里最普适、最可能引发回应的一条**（每个被网络卡过的人都该有话说），
> 却同样是 0——**这反证问题不在内容，而在渠道**（见 `反思` §3.3、§7）。
