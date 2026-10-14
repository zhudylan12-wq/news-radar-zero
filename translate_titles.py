"""Offline English -> Chinese titles using a pinned Argos / OPUS-MT model."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib import request
import zipfile

MODEL_URL = "https://data.argosopentech.com/argospm/v1/translate-en_zh-1_9.argosmodel"
MODEL_SHA256 = "433e7c4f034d87fbe2353161e05f18646d7999452f801a4e1f0378522b9850ab"
MODEL_FOLDER = "translate-en_zh-1_9"
PROVIDER = "Argos / OPUS-MT 英译中 1.9（离线）"
TRANSLATION_VERSION = 3


def translation_parts(title):
    prefix = ""
    if title.startswith("Show HN:"):
        prefix, title = "项目展示：", title[8:].strip()
    elif title.startswith("Ask HN:"):
        prefix, title = "社区提问：", title[7:].strip()
    else:
        repository = re.match(r"^([\w.-]+/[\w.-]+ — )(.*)$", title)
        if repository:
            prefix, title = repository.groups()
    title = re.sub(r"https?://\S+|www\.\S+", "", title)
    title = re.sub(r"(?<!\w)#[\w]+", "", title)
    # Handles often name the author or product; dropping them changes attribution.
    title = re.sub(r"(?<!\w)@([\w]+)", r"\1", title)
    title = re.sub(r"[\U0001F000-\U0001FFFF\u2600-\u27ff\u200b\ufe0f]", " ", title)
    title = re.sub(r"\s*[→►|]\s*", "; ", title)
    title = re.sub(r"\s+", " ", title).strip()
    # Remove empty promotional interjections, never generate a replacement claim.
    title = re.sub(r"^(This is (bananas|gold|wild|amazing)|Love this|No+\s+Way+|Goated)[!.\s]+", "", title, flags=re.I)
    title = re.sub(r"\b(read more|learn more|how.to below|take a look)[^.!?]*[.!?]?$", "", title, flags=re.I)
    parts = []
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z\u4e00-\u9fff])|;\s+", title):
        sentence = sentence.strip(" -;,")
        if not sentence or re.fullmatch(r"(goated|this is (bananas|wild|amazing)|love this)[.!\s]*", sentence, re.I):
            continue
        # Bound each segment to avoid long social posts overwhelming the small model.
        words = sentence.split()
        for start in range(0, len(words), 38):
            parts.append(" ".join(words[start:start + 38]))
    return prefix, parts or [title]


def polish_translation(text, original):
    text = clean_translation(text)
    replacements = {"开放源代码": "开源", "开放源码": "开源", "AI编码": "AI 编程", "AI 编码": "AI 编程"}
    contextual = {
        r"\b(agents?|agentic|subagents?)\b": {"人工智能特工": "AI 智能体", "人工智能代理": "AI 智能体", "AI代理": "AI 智能体", "AI 代理": "AI 智能体", "子代理": "子智能体", "代理人": "智能体", "代理商": "智能体", "经纪人": "智能体", "特工": "智能体", "代理": "智能体"},
        r"\bGemini\b": {"双子座": "Gemini"}, r"\bClaude\b": {"克洛德": "Claude", "克劳德": "Claude"},
        r"\bRouterPlus\b": {"路由器Plus": "RouterPlus", "路由加": "RouterPlus"},
        r"\bPrized\b": {"获奖的": "Prized", "被授予的": "Prized"},
        r"\bfree\b": {"自由视频": "免费视频", "自由开放": "免费开源"},
        r"\blead generation\b": {"铅组": "获客流程", "铅生成": "获客"},
        r"\btokens?\b": {"代币": "Token", "令牌": "Token"},
        r"\bVS Code\b": {"VS代码": "VS Code", "VS 代码": "VS Code"},
        r"\bHome Assistant\b": {"Home Associate": "Home Assistant", "家庭助理": "Home Assistant", "家庭助手": "Home Assistant"},
        r"\b(LoRA|GGUFs?)\b": {"列车": "训练", "量产": "量化", "家长": "基础模型", "父母": "基础模型", "GGOF": "GGUF"},
        r"\b(ship|ships|shipping|shipped)\b": {"运送": "发布", "船只": "发布", "代码船": "代码发布"},
        r"\blaunch(es|ed)?\b": {"发射": "发布"},
    }
    for pattern, terms in contextual.items():
        if re.search(pattern, original, re.I):
            replacements.update(terms)
    for source, target in replacements.items():
        text = text.replace(source, target)
    if re.search(r"\bsubagents?\b", original, re.I):
        text = re.sub(r"(\d+\s*个)潜质", r"\1子智能体", text)
    if re.search(r"\bagents?\b", original, re.I):
        text = text.replace("探员", "智能体")
    if re.search(r"\bSaaS\b", original, re.I):
        text = text.replace("萨阿斯", "SaaS")
    if re.search(r"\bMIT\b", original) and (re.match(r"^[\w.-]+/[\w.-]+ — ", original) or re.search(r"\blicen[cs]e\b", original, re.I)):
        text = text.replace("麻省理工学院", "MIT 许可证")
    if re.search(r"\bskill for\b", original, re.I):
        text = text.replace("技艺", "技能")
    text = re.sub(r"\{[^{}]*\\[^{}]*\}", "", text)
    text = re.sub(r"\s+([，。！？；：])", r"\1", text)
    return text.strip()


def valid_translation(text, original):
    if not re.search(r"[\u4e00-\u9fff]", text):
        return False
    if re.search(r"([^\s])\1{5,}", text) or re.search(r"(.{2,12}?)\1{3,}", text) or "\\fn" in text:
        return False
    # Catch obvious decoder collapse, not claim to assess semantic accuracy.
    return len(text) >= min(12, len(original) * .12) and len(text) <= max(100, len(original) * 2.2)


def prepare_model(root=Path(".models")):
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "en-zh.argosmodel"
    if not archive.exists():
        temporary = archive.with_suffix(".download")
        try:
            with request.urlopen(MODEL_URL, timeout=90) as response, temporary.open("wb") as out:
                total = 0
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > 90_000_000:
                        raise ValueError("Translation model download exceeded its size limit")
                    out.write(block)
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != MODEL_SHA256:
                raise ValueError("Translation model checksum mismatch")
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != MODEL_SHA256:
        raise ValueError("Cached translation model checksum mismatch")
    # Re-extract the verified data files; never load pickle / executable model code.
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            relative = Path(member.filename)
            allowed = (relative.parts[:2] == (MODEL_FOLDER, "model") or
                       relative.as_posix() in {f"{MODEL_FOLDER}/sentencepiece.model", f"{MODEL_FOLDER}/metadata.json", f"{MODEL_FOLDER}/README.md"})
            target = root / relative
            if not allowed or member.is_dir():
                continue
            if not target.resolve().is_relative_to(root.resolve()) or member.file_size > 200_000_000:
                raise ValueError("Unexpected model archive member")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(package.read(member))
    return root / MODEL_FOLDER


class OfflineTranslator:
    def __init__(self, root=Path(".models")):
        import ctranslate2
        import sentencepiece
        folder = root / MODEL_FOLDER
        self.tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(folder / "sentencepiece.model"))
        self.model = ctranslate2.Translator(str(folder / "model"), device="cpu", compute_type="int8", intra_threads=2)

    def __call__(self, titles):
        prefixes, ranges, texts = [], [], []
        for title in titles:
            prefix, segments = translation_parts(title)
            prefixes.append(prefix)
            ranges.append((len(texts), len(texts) + len(segments)))
            texts.extend(segments)
        encoded = [self.tokenizer.encode(title, out_type=str) for title in texts]
        batches = self.model.translate_batch(encoded, beam_size=4, max_batch_size=8,
                                             max_input_length=256, max_decoding_length=192,
                                             replace_unknowns=True, length_penalty=1,
                                             no_repeat_ngram_size=3, repetition_penalty=1.05)
        decoded = [clean_translation(self.tokenizer.decode(result.hypotheses[0])) for result in batches]
        results = []
        for prefix, (start, end), title in zip(prefixes, ranges, titles):
            # A surviving later sentence must not mask a collapsed earlier one.
            collapsed = any(len(source) > 60 and len(target) < len(source) * .20
                            for source, target in zip(texts[start:end], decoded[start:end]))
            results.append("" if collapsed else prefix + polish_translation(" ".join(decoded[start:end]), title))
        return results


def clean_translation(title):
    # Some model outputs retain SentencePiece's word boundary marker after decoding.
    return re.sub(r"\s+", " ", str(title).replace("▁", " ")).strip()[:640]


def already_chinese(title):
    chinese = len(re.findall(r"[\u4e00-\u9fff]", title))
    latin = len(re.findall(r"[a-zA-Z]", title))
    return chinese >= 4 and chinese * 3 >= latin


def localize_reports(reports, translator=None, model_ready=True):
    """Translate each unique title once; preserve raw titles and earlier timestamps."""
    known, pending = {}, {}
    for report in reports:
        for item in report["items"]:
            if item.get("title_zh") and item.get("translation_version") == TRANSLATION_VERSION:
                known[item["title"]] = clean_translation(item["title_zh"])
    for report in reports:
        for item in report["items"]:
            title = item["title"]
            if already_chinese(title):
                item.update(title_zh=title, translation_status="original_zh", translation_version=TRANSLATION_VERSION)
            elif title in known:
                item.update(title_zh=known[title], translation_status="translated", translation_version=TRANSLATION_VERSION)
            else:
                item.pop("title_zh", None)
                pending.setdefault(title, []).append(item)
    reason = ""
    if pending:
        try:
            if not model_ready:
                raise RuntimeError("Offline model setup did not complete")
            backend = translator or OfflineTranslator()
            titles = list(pending)
            translated = backend(titles)
            if len(translated) != len(titles):
                raise ValueError("Translation batch size mismatch")
            for title, chinese in zip(titles, translated):
                chinese = clean_translation(chinese)
                valid = valid_translation(chinese, title)
                for item in pending[title]:
                    if valid:
                        item.update(title_zh=chinese, translation_status="translated", translation_version=TRANSLATION_VERSION)
                    else:
                        item["translation_status"] = "needs_review"
        except (ImportError, RuntimeError, ValueError, OSError) as exc:
            reason = type(exc).__name__
            for items in pending.values():
                for item in items:
                    item["translation_status"] = "unavailable"
    for report in reports:
        ready = sum(bool(i.get("title_zh")) for i in report["items"])
        report["translation"] = {"provider": PROVIDER, "ready": ready, "total": len(report["items"]),
                                 "status": "complete" if ready == len(report["items"]) else "partial" if ready else "unavailable",
                                 "error": reason, "version": TRANSLATION_VERSION}
    return reports


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        folder = prepare_model()
        print(f"Offline translation model verified and prepared: {folder}")
        result = OfflineTranslator()(["An open source AI tool"])
        if not result or not re.search(r"[\u4e00-\u9fff]", result[0]):
            raise RuntimeError("Offline translation smoke check failed")
        print("Offline translation smoke check passed")
