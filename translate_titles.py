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
        prefixes, texts = [], []
        for title in titles:
            prefix = ""
            if title.startswith("Show HN:"):
                prefix, title = "项目展示：", title[len("Show HN:"):].strip()
            elif title.startswith("Ask HN:"):
                prefix, title = "社区提问：", title[len("Ask HN:"):].strip()
            else:
                repository = re.match(r"^([\w.-]+/[\w.-]+ — )(.*)$", title)
                if repository:
                    prefix, title = repository.groups()
            prefixes.append(prefix)
            texts.append(title)
        encoded = [self.tokenizer.encode(title, out_type=str) for title in texts]
        batches = self.model.translate_batch(encoded, beam_size=4, max_batch_size=8,
                                             max_input_length=512, max_decoding_length=384,
                                             replace_unknowns=True, length_penalty=0.2)
        return [prefix + clean_translation(self.tokenizer.decode(result.hypotheses[0])).replace("AI代理", "AI 智能体").replace("AI 代理", "AI 智能体")
                for prefix, result in zip(prefixes, batches)]


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
            if item.get("title_zh"):
                known[item["title"]] = clean_translation(item["title_zh"])
    for report in reports:
        for item in report["items"]:
            title = item["title"]
            if already_chinese(title):
                item.update(title_zh=title, translation_status="original_zh")
            elif title in known:
                item.update(title_zh=known[title], translation_status="translated")
            else:
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
                valid = bool(re.search(r"[\u4e00-\u9fff]", chinese))
                for item in pending[title]:
                    if valid:
                        item.update(title_zh=chinese, translation_status="translated")
                    else:
                        item["translation_status"] = "unavailable"
        except (ImportError, RuntimeError, ValueError, OSError) as exc:
            reason = type(exc).__name__
            for items in pending.values():
                for item in items:
                    item["translation_status"] = "unavailable"
    for report in reports:
        ready = sum(bool(i.get("title_zh")) for i in report["items"])
        report["translation"] = {"provider": PROVIDER, "ready": ready, "total": len(report["items"]),
                                 "status": "complete" if ready == len(report["items"]) else "partial" if ready else "unavailable",
                                 "error": reason}
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
