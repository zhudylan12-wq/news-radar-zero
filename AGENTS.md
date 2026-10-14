# News Radar Zero

本项目独立于相邻的工位德州、OfficeDex 等项目，不改动其他项目文件。

## 当前有效边界

- 用户明确要求严格 0 成本，主题为 AI 工具、Agent、开源与出海。
- 2026-10-14 用户已明确授权使用本人 GitHub 账号创建公开仓库、上传源码并验证定时采集；随后明确要求“直接弄成在线页面……定时更新后我打开链接就能看……保持 0 成本”，允许启用本仓库 GitHub Pages 和自动部署。
- 不添加付费 API、LLM、服务器、代理、Cookie 或密钥读取，不修改付款或账单配置。
- X 是搜索索引覆盖，不能描述为直接、完整、实时抓取。Reddit 为公开 RSS，抓取失败不能等同没有新内容。
- 配置、抓取、分类、去重、状态和渲染都在 `radar.py` / `config.json`；GitHub 流程位于 `.github/workflows/`。
- 生成报告和状态不进入 Git 历史。Pages 使用默认 github.io 免费域名，公开展示报告；不得添加付费托管或自购域名。发布验证状态见 context/verification.md。
- 当前调度：每天北京时间 08:17、14:17、20:17 三次；用户要求 HTML 按真实采集时间区分各期，并显示中文标题。
- 历史保留最近 7 天、最多 30 期，在 HTML 顶部选择采集时间。标题用离线 Argos / OPUS-MT 模型翻译，保留原文；不调用付费翻译接口。模型及报告均不提交 Git。
- 在线入口：https://zhudylan12-wq.github.io/news-radar-zero/ 。Pages 已开启，每次成功采集后自动部署；失败保留上次成功部署，页面时间是新鲜度依据。

## 上下文检索目录

| 主题 | 触发条件 | 文件 |
|---|---|---|
| 数据入口、费用、调度、去重与证据边界 | 采集、来源、配置、Github | README.md |
| 验证与发布状态 | 测试、验收、上线 | context/verification.md |

运行 `python3 radar.py --check` 和 `python3 -m unittest discover -s tests -v`。真实源验证要保留失败状态，不能仅用模拟测试声称云端通过。
