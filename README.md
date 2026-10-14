# 零成本资讯雷达

定时整理 **AI 工具、Agent、开源、出海产品**的公开资讯。Python 标准库实现，没有付费 API、模型调用、数据库、服务器或账号 Cookie。

**已在 [zhudylan12-wq/news-radar-zero](https://github.com/zhudylan12-wq/news-radar-zero) 启用 GitHub Actions，两次云端验证运行成功（2026-10-14）。**

[查看最新运行与报告附件](https://github.com/zhudylan12-wq/news-radar-zero/actions/workflows/radar.yml) · [验证记录](context/verification.md)

打开最新成功运行，在页面下方 **Artifacts → news-radar** 下载并解压，打开 `index.html` 阅读。附件保留 1 天，需要登录 GitHub 下载。当前没有开启独立 Pages 网站。

## 第一版能力

| 来源 | 获取方式 | 能证明什么 / 不能证明什么 |
|---|---|---|
| Reddit：LocalLLaMA、ChatGPTCoding、SaaS、SideProject | 社区公开 RSS / Atom | 读取本次能访问的近期帖子；403、429、超时均明确显示，不保证云端 IP 可访问 |
| X：AI 工具、Agent / 开源、出海产品 | Google News 公开 RSS 中的 X 搜索索引 | 只提供被索引的标题与跳转链接；不是直接爬 X，不保证完整、实时或全文；索引时间不是原帖发布时间 |
| GitHub：新 Agent 仓库 | 公开搜索 API，无个人令牌 | 近 14 天创建且至少 10 星，按当前星数取样；不是增速榜或质量推荐 |
| Hacker News：AI、SaaS | HN Algolia 公开搜索索引 | 近 48 小时的帖子入口；补充来源单独标记，不充当 X / Reddit 抓取成功 |

每次生成 `site/index.html`、`site/latest.md`、`site/latest.json`。默认最近 48 小时，每来源最多 8 条，总计最多 60 条。GitHub 使用独立的 14 天发现窗口。

页面为中文界面，标题保留原文。分类使用关键词规则，没有 AI 摘要或自动翻译，避免引入模型费用或虚构信息。

## 本地运行

需要 Python 3.9+（GitHub Actions 固定使用 3.12）。macOS / Linux 通常自带时区库。

```bash
python3 radar.py --check
python3 -m unittest discover -s tests -v
python3 radar.py
python3 -m http.server 8765 --directory site
```

打开 `http://localhost:8765`，或直接打开生成的 HTML / Markdown。

编辑 `config.json` 可调整关键词、社区、来源和结果数量。`source_interval_seconds` 最低 1 秒，避免紧密请求。单次响应最多 3 MB，每个来源最多尝试两次；401 / 403 / 410 / 429 不立即重试。

## GitHub 运行逻辑

```text
定时 / 手动 / main 上代码变更
  → 校验配置 + 离线测试
  → 恢复去重指纹与限流退避状态
  → 逐来源低频读取公开 RSS / 搜索接口
  → 校验时间、链接、来源归属 → 分类 → 去重与数量限制
  → 生成 HTML / Markdown / JSON（包括失败来源）
  → 上传 1 天有效的 news-radar 报告附件
  → 缓存哈希指纹和退避时间
  → 可选发布 GitHub Pages
```

- 计划每天北京时间 / 台北时间 **08:00** 运行一次，对应 UTC `0 0 * * *`。GitHub 可能延迟或丢弃排队任务，不承诺准点。
- 首次推送到 `main` 会自动运行，之后可在 Actions → News radar → Run workflow 手动执行。
- 只允许在**公开仓库**运行云端任务。私有仓库直接跳过，避免使用共享付费额度。标准 `ubuntu-latest` runner，不用 larger runner。
- 默认不开启 Pages。运行成功后在 Actions 的本次运行里下载 `news-radar` 附件，解压查看。附件保留 1 天；源码和运行统计不含第三方帖子正文。
- 需要公开阅读页面时，在仓库 Settings → Pages 中选择 GitHub Actions，并设置 Actions Variable `ENABLE_PAGES=true`，随后手动运行一次。开启意味着报告可公开访问。
- Actions 不写仓库，无需 PAT 或平台密钥。抓取代码不读取 Cookie、会话或本机浏览器资料。
- 定时任务只运行默认分支。公开仓库 60 天没有活动时，GitHub 可能自动停用定时任务；届时手动重新启用。这里没有空提交或保活刷活动逻辑。

## 去重、失败和存储

- 同一 URL 去除追踪参数后去重；X 索引还按归一化标题合并重复收录。不同真实帖子的相同标题不合并。
- **NEW = 近 30 天没有展示过**，不等于刚发布。旧条目仍可出现在近期快照中，方便阅读；新条目优先。
- 只给实际进入报告的条目标记已展示，避免数量上限吞掉未展示的新条目。
- 跨次状态保存于 Actions cache，只含哈希、时间、来源 ID 和限流域名，不缓存帖子标题、正文、作者或 Cookie。状态最多 10,000 个指纹、30 天。缓存被驱逐或修改配置后 NEW 可能重置。
- 429 尊重 `Retry-After`，至少退避 1 小时；无该字段时 6 小时，更长的服务端等待要求不会被缩短。同一域名其他订阅源也停止请求，跨次保留退避。401 / 403 / 410 退避 6 小时。不会换代理、绕过验证码、伪造身份或使用付费兜底。
- 来源状态分别为：可用、空列表、无近期匹配、访问受限、限流、失败、退避中。部分失败仍产出报告；全部不可用时保留诊断附件并让工作流失败。
- `site/` 和 `.state/` 均被 Git 忽略，避免帖子内容进入 Git 历史。附件 1 天过期；可选 Pages 仅保留最近一次部署的快照，不提供永久内容归档。
- Pages 是静态快照：调度停止后页面不会自行删除。需要清理时停用 Pages；源帖删除不会实时同步到已生成的快照。
- 当前没有邮件、飞书或 Telegram 推送。工作流失败通知由 GitHub 账号的通知设置决定；是否送达未经验证。

## 零成本边界

代码只访问公开免费入口，不调用 X 付费 API、不用 LLM API、不启动收费服务。公开仓库标准 Actions 计算时间免费，附件和缓存仍有额度限制：本项目限制单次响应、结果规模、1 天附件和小型指纹状态，实际占用可在仓库 Actions 中查看。**不调整账号账单、付款方式或预算。**

这是一套仓库内的资讯报告生成流程，不是全天候免费云服务器，也没有代理、远程桌面或常驻服务。平台免费政策和可访问性可能变化；失败会显式记录。

## 参考与来源

实现借鉴 [craftsail/awesome-early-radar](https://github.com/craftsail/awesome-early-radar) 的“定时生成资讯报告”思路。原仓库未声明可识别的开源许可证，本项目未复制其代码或下载执行其 Release 二进制。

规则核验日期：2026-10-14。

- [GitHub Actions 计费与免费额度](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [GitHub 定时任务限制](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [GitHub Actions 使用条款](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features#actions)
- [GitHub Pages 工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
- [X 官方接口计费](https://docs.x.com/x-api/getting-started/pricing)
- [Reddit 数据接口规则](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki)

验证结果见 [context/verification.md](context/verification.md)。
