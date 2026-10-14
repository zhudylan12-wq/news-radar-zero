# 零成本资讯雷达

定时整理 **AI 工具、智能体、开源、出海产品**的公开资讯。采集使用 Python 标准库，标题用离线模型译成中文。没有付费 API、付费模型接口、数据库、自购服务器或账号 Cookie。

**已在 [zhudylan12-wq/news-radar-zero](https://github.com/zhudylan12-wq/news-radar-zero) 启用 GitHub Actions；逐次云端验证结果见验证记录（2026-10-14）。**

[每日简报](https://zhudylan12-wq.github.io/news-radar-zero/brief.html) · [打开在线资讯雷达](https://zhudylan12-wq.github.io/news-radar-zero/) · [查看最新运行与报告附件](https://github.com/zhudylan12-wq/news-radar-zero/actions/workflows/radar.yml) · [验证记录](context/verification.md)

直接打开上面的在线链接即可阅读，无需登录或下载文件。每天北京时间 **08:17 / 14:17 / 20:17** 采集成功后自动部署，重新打开或刷新页面即可查看最新一期；顶部可按完整采集时间选择各期报告，保留最近 7 天、最多 30 期。页面公开可见，使用 GitHub Pages 免费默认域名。

需要离线保存时，仍可进入最新成功运行，在页面下方 **Artifacts → news-radar** 下载并解压，再打开 `index.html`。附件保留 1 天，需要登录 GitHub 下载；下载副本不会自动更新。若采集全部失败、翻译引擎故障或部署失败，线上保留上一份成功部署的报告，请以页面“最新采集”时间为准。

## 第一版能力

| 来源 | 获取方式 | 能证明什么 / 不能证明什么 |
|---|---|---|
| Reddit：LocalLLaMA、ChatGPTCoding、SaaS、SideProject | 社区公开 RSS / Atom | 读取本次能访问的近期帖子；403、429、超时均明确显示，不保证云端 IP 可访问 |
| X：AI 工具、Agent / 开源、出海产品 | Google News 公开 RSS 中的 X 搜索索引 | 只提供被索引的标题与跳转链接；不是直接爬 X，不保证完整、实时或全文；索引时间不是原帖发布时间 |
| GitHub：新 Agent 仓库 | 公开搜索 API，无个人令牌 | 近 14 天创建且至少 10 星，按当前星数取样；不是增速榜或质量推荐 |
| Hacker News：AI、SaaS | HN Algolia 公开搜索索引 | 近 48 小时的帖子入口；补充来源单独标记，不充当 X / Reddit 抓取成功 |

每次生成 `site/index.html`、`site/latest.md`、`site/latest.json` 和 `site/history.json`。默认最近 48 小时，每来源最多 8 条，总计最多 60 条。GitHub 使用独立的 14 天发现窗口。

页面和标题以中文展示，原始标题可展开核对。英译中使用 Argos / OPUS-MT 1.9 的离线模型，在免费 runner 的 CPU 上运行。模型包约 71 MB，从 Argos 官方数据站下载，通过固定 SHA-256 校验和实际翻译检查后缓存；不用在线翻译服务或 API key。分类仍使用关键词规则，不生成额外新闻事实。机器翻译可能不准确；模型不可用时明确标注并保留原文，翻译引擎故障会让工作流报错；个别译文未通过质量检查时保留原文并明确提示。许可与作者见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 本地运行

需要 Python 3.9+（GitHub Actions 固定使用 3.12）。macOS / Linux 通常自带时区库。

```bash
python3 radar.py --check
python3 -m unittest discover -s tests -v
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-translation.txt
.venv/bin/python translate_titles.py --prepare
.venv/bin/python radar.py
python3 -m http.server 8765 --directory site
```

打开 `http://localhost:8765`，或直接打开生成的 HTML / Markdown。

编辑 `config.json` 可调整社区、来源和结果数量；精确主题判断位于 `relevance.py`。仅出现 AI、local、launch、growth 等泛词不再直接入选，所有来源（含 Reddit 与 GitHub）都经过同一套主题筛选。`source_interval_seconds` 最低 1 秒，避免紧密请求。单次响应最多 3 MB，每个来源最多尝试两次；401 / 403 / 410 / 429 不立即重试。

## GitHub 运行逻辑

```text
定时 / 手动 / main 上代码变更
  → 校验配置 + 离线测试
  → 恢复去重指纹、限流退避、历史报告与离线翻译模型
  → 逐来源低频读取公开 RSS / 搜索接口
  → 校验时间、链接、来源归属 → 分类 → 去重与数量限制
  → 本地模型生成中文标题，保留原文
  → 按真实采集时间归档最近 7 天、最多 30 期
  → 生成可按时间选择的 HTML / Markdown / JSON（包括失败来源）
  → 上传 1 天有效的 news-radar 报告附件
  → 分别缓存哈希指纹、退避时间和近期历史报告
  → 自动发布 GitHub Pages（本仓库已开启）
```

- 计划每天北京时间 / 台北时间 **08:17、14:17、20:17** 运行三次，对应 UTC `17 0,6,12 * * *`。HTML 显示实际采集时间，包含日期、时分秒，不伪装成计划时间。GitHub 可能延迟或丢弃排队任务，不承诺准点。
- 首次推送到 `main` 会自动运行，之后可在 Actions → News radar → Run workflow 手动执行。
- 只允许在**公开仓库**运行云端任务。私有仓库直接跳过，避免使用共享付费额度。标准 `ubuntu-latest` runner，不用 larger runner。
- 本仓库经用户授权已开启 Pages：Settings → Pages 的构建类型为 GitHub Actions，Actions Variable `ENABLE_PAGES=true`。每次成功运行自动发布至同一网址，无需手动下载。Fork 的仓库默认不带此变量；需要自行启用 Pages 才会公开报告。
- 离线附件仍保留 1 天，包含最近 7 天的报告；源码和运行统计不含第三方帖子正文。
- Actions 不写仓库，无需 PAT 或平台密钥。抓取代码不读取 Cookie、会话或本机浏览器资料。
- 定时任务只运行默认分支。公开仓库 60 天没有活动时，GitHub 可能自动停用定时任务；届时手动重新启用。这里没有空提交或保活刷活动逻辑。

## 去重、失败和存储

- 同一 URL 去除追踪参数后去重；X 索引还按归一化标题合并重复收录。不同真实帖子的相同标题不合并。
- **NEW = 近 30 天没有展示过**，不等于刚发布。旧条目仍可出现在近期快照中，方便阅读；新条目优先。
- 只给实际进入报告的条目标记已展示，避免数量上限吞掉未展示的新条目。
- 去重状态缓存只含哈希、时间、来源 ID 和限流域名，最多 10,000 个指纹、30 天。缓存被驱逐或修改配置后“首次展示”可能重置。
- 历史报告另存 Actions cache，包含原始标题、中文译文、来源链接、时间和来源状态，保留最近 7 天、最多 30 期，不保存帖子全文或 Cookie。同一翻译版本内复用已有译文；翻译版本升级后重新处理旧标题。历史报告也重新执行当前主题筛选，原始采集时间保持不变。缓存丢失时尝试从最近 3 次成功运行的未过期附件恢复，不能承诺完整历史。
- 429 尊重 `Retry-After`，至少退避 1 小时；无该字段时 6 小时，更长的服务端等待要求不会被缩短。同一域名其他订阅源也停止请求，跨次保留退避。401 / 403 / 410 退避 6 小时。不会换代理、绕过验证码、伪造身份或使用付费兜底。
- 来源状态分别为：可用、空列表、无近期匹配、访问受限、限流、失败、退避中。部分失败仍产出报告；全部不可用时保留诊断附件并让工作流失败。
- `site/`、`.state/` 和 `.models/` 均被 Git 忽略，避免内容和模型进入 Git 历史。附件 1 天过期；Pages 的最新部署包含近期报告，不提供永久内容归档。源帖删除不会实时同步到历史快照。
- Pages 是静态快照：调度停止后页面不会自行删除。需要清理时停用 Pages；源帖删除不会实时同步到已生成的快照。
- 当前没有邮件、飞书或 Telegram 推送。工作流失败通知由 GitHub 账号的通知设置决定；是否送达未经验证。

## 零成本边界

代码只访问公开免费入口，不调用 X 付费 API、不用付费模型 API、不启动收费服务。公开仓库标准 Actions 计算时间免费，附件和缓存仍有额度限制：本项目限制单次响应、结果规模、1 天附件、最近 7 天报告和小型指纹状态；模型使用一个固定缓存。实际占用可在仓库 Actions 中查看。**不调整账号账单、付款方式或预算。**

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

## 中文质量与每日简报

- 中文处理：分句、限制单段长度、去掉链接/账号/话题标签和空泛宣传语，抑制连续重复，按原文语境统一 Agent、Gemini、Claude、Token 等术语。过短或异常重复的译文回退原文。仍为机器摘译，不保证人工翻译水平。
- 主题过滤：AI 工具需要具体工具或工程信息；Agent 需要智能体/MCP/开发语境；开源需要具体项目信息；出海需要 SaaS、独立开发、SEO 等业务语境。移除 Saas 电视剧同名结果、体育 free agent、泛宏观 AI 新闻和只有话题标签的广告。保守规则可能漏收没有充分标题信息的帖子。
- 每日北京时间 **10:20** 在 GitHub 整理已采集的材料，**10:30** 由本对话的 Codex 定时任务提醒并附链接。GitHub 提前生成是为了给排队和部署留出时间，不新增付费 API。
- 简报链接：`brief.html`；程序可读数据：`brief.json`；历史链接：`brief-YYYY-MM-DD.html`。最多 10 条，按四个主题、来源上限去重整理，不生成未经原文支持的总结。Reddit / X / HN 按过去 24 小时原帖或索引时间；GitHub 按首次采集发现时间。
- 普通三次采集不会改写当天已生成的正式简报。简报最多保留 7 天；没有正式简报时显示明确标注的即时预览。材料超过 12 小时未更新时，日报流程报错并保留上一版。
- 手动验证简报流程：Actions → News radar → Run workflow → 勾选 `daily_brief`。这会使用已有历史，不额外请求资讯来源。
- 对话提醒只在当日正式简报可用时链接当日内容；日期不符、预览或材料过期时明确报告延迟，不能把旧简报称为今日简报。提醒由 Codex 执行，与 GitHub 免费托管是两个独立环节。
