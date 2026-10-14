# 离线翻译模型与依赖

- 模型：Argos Translate 的 `translate-en_zh-1_9`，使用 OPUS-MT 英译中模型。
- 作者：Jörg Tiedemann、Santhosh Thottingal。
- 原始模型许可：CC-BY 4.0，见随模型提供的 README。
- 论文：OPUS-MT — Building open translation services for the World，EAMT 2020，Lisbon, Portugal。
- 模型索引：https://github.com/argosopentech/argospm-index
- 模型下载：https://data.argosopentech.com/argospm/v1/translate-en_zh-1_9.argosmodel （Argos 官方数据站；与索引原链接的包校验值一致）
- SHA-256：`433e7c4f034d87fbe2353161e05f18646d7999452f801a4e1f0378522b9850ab`
- 来源项目：https://github.com/argosopentech/argos-translate
- 推理引擎：https://github.com/OpenNMT/CTranslate2 ，MIT。
- 分词器：https://github.com/google/sentencepiece ，Apache-2.0。

本项目不修改模型权重，仅用它在免费 runner 的 CPU 上生成中文标题。生成结果为机器翻译，界面保留原文供核对。没有调用在线翻译或收费模型 API。
