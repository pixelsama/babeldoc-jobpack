from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymupdf

from babeldoc.const import close_process_pool
from babeldoc.docvision.doclayout import DocLayoutModel
from babeldoc.format.pdf import high_level
from babeldoc.format.pdf.document_il.backend.pdf_creater import PDFCreater
from babeldoc.format.pdf.document_il.midend.detect_scanned_file import DetectScannedFile
from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
from babeldoc.format.pdf.document_il.midend.il_translator import ParagraphTranslateTracker
from babeldoc.format.pdf.document_il.midend.layout_parser import LayoutParser
from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder
from babeldoc.format.pdf.document_il.midend.styles_and_formulas import StylesAndFormulas
from babeldoc.format.pdf.document_il.midend.table_parser import TableParser
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
from babeldoc.format.pdf.document_il.xml_converter import XMLConverter
from babeldoc.format.pdf.translation_config import TranslationConfig
from babeldoc.progress_monitor import ProgressMonitor

from . import SCHEMA_VERSION
from .translators import EchoTranslator


class JobPackError(RuntimeError):
    pass


def export_jobs(
    input_pdf: Path,
    job_dir: Path,
    lang_in: str,
    lang_out: str,
    pages: str | None = None,
    rpc_doclayout: str | None = None,
    skip_scanned_detection: bool = True,
    debug: bool = False,
) -> dict[str, Any]:
    input_pdf = input_pdf.expanduser().resolve()
    job_dir = job_dir.expanduser().resolve()
    if not input_pdf.exists():
        raise JobPackError(f"input PDF does not exist: {input_pdf}")
    if input_pdf.suffix.lower() != ".pdf":
        raise JobPackError(f"input is not a PDF: {input_pdf}")

    job_dir.mkdir(parents=True, exist_ok=True)
    working_dir = job_dir / "working"
    working_dir.mkdir(parents=True, exist_ok=True)

    translator = EchoTranslator(
        lang_in=lang_in,
        lang_out=lang_out,
        ignore_cache=True,
    )
    doc_layout_model = _load_doc_layout_model(rpc_doclayout)
    config = _new_translation_config(
        translator=translator,
        input_pdf=input_pdf,
        lang_in=lang_in,
        lang_out=lang_out,
        output_dir=job_dir,
        working_dir=working_dir,
        doc_layout_model=doc_layout_model,
        pages=pages,
        skip_scanned_detection=skip_scanned_detection,
        debug=debug,
    )
    _maybe_init_font_mapper(doc_layout_model, config)

    docs, copied_input_pdf = _prepare_docs_for_jobpack(config)
    jobs = _extract_jobs(docs, config)

    xml_converter = XMLConverter()
    document_xml = job_dir / "document.xml"
    jobs_json = job_dir / "jobs.json"
    manifest_json = job_dir / "manifest.json"

    xml_converter.write_xml(docs, str(document_xml))
    jobs_json.write_text(
        json.dumps(jobs, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "lang_in": lang_in,
        "lang_out": lang_out,
        "input_pdf": copied_input_pdf.name,
        "document_xml": document_xml.name,
        "jobs_json": jobs_json.name,
        "job_count": len(jobs),
    }
    manifest_json.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return {
        "job_dir": str(job_dir),
        "manifest": str(manifest_json),
        "document_xml": str(document_xml),
        "jobs_json": str(jobs_json),
        "job_count": len(jobs),
    }


def apply_jobs(
    job_dir: Path,
    translations_file: Path,
    output_dir: Path,
    no_dual: bool = False,
    no_mono: bool = False,
    debug: bool = False,
    fail_on_missing: bool = True,
) -> dict[str, Any]:
    job_dir = job_dir.expanduser().resolve()
    translations_file = translations_file.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = job_dir / "manifest.json"
    if not manifest_path.exists():
        raise JobPackError(f"manifest.json was not found in {job_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    input_pdf = job_dir / manifest["input_pdf"]
    document_xml = job_dir / manifest["document_xml"]
    jobs_json = job_dir / manifest["jobs_json"]
    if not input_pdf.exists():
        raise JobPackError(f"missing input PDF in job pack: {input_pdf}")
    if not document_xml.exists():
        raise JobPackError(f"missing document XML in job pack: {document_xml}")
    if not jobs_json.exists():
        raise JobPackError(f"missing jobs.json in job pack: {jobs_json}")

    jobs: list[dict[str, Any]] = json.loads(jobs_json.read_text(encoding="utf-8"))
    translations = _load_translations(translations_file)

    translator = EchoTranslator(
        lang_in=manifest["lang_in"],
        lang_out=manifest["lang_out"],
        ignore_cache=True,
    )
    config = _new_translation_config(
        translator=translator,
        input_pdf=input_pdf,
        lang_in=manifest["lang_in"],
        lang_out=manifest["lang_out"],
        output_dir=output_dir,
        working_dir=output_dir / "working",
        doc_layout_model=None,
        pages=None,
        skip_scanned_detection=True,
        debug=debug,
        no_dual=no_dual,
        no_mono=no_mono,
    )

    xml_converter = XMLConverter()
    docs = xml_converter.read_xml(str(document_xml))
    il_translator = ILTranslator(translator, config)

    missing_job_ids: list[str] = []
    font_maps_cache: dict[int, tuple[dict[str, Any], dict[int, dict[str, Any]]]] = {}
    applied = 0

    for job in jobs:
        job_id = job["id"]
        translated_text = translations.get(job_id)
        if translated_text is None:
            if fail_on_missing:
                missing_job_ids.append(job_id)
                continue
            translated_text = job.get("source_text", "")

        page_index = int(job["page_index"])
        paragraph_index = int(job["paragraph_index"])
        try:
            page = docs.page[page_index]
            paragraph = page.pdf_paragraph[paragraph_index]
        except (IndexError, KeyError) as exc:
            raise JobPackError(
                f"job {job_id} points to a missing page/paragraph ({page_index}/{paragraph_index})"
            ) from exc

        if page_index not in font_maps_cache:
            font_maps_cache[page_index] = _build_font_maps(page)
        page_font_map, xobj_font_map = font_maps_cache[page_index]

        tracker = ParagraphTranslateTracker()
        text, translate_input = il_translator.pre_translate_paragraph(
            paragraph,
            tracker,
            page_font_map,
            xobj_font_map,
        )
        if text is None or translate_input is None:
            continue

        il_translator.post_translate_paragraph(
            paragraph,
            tracker,
            translate_input,
            translated_text,
        )
        applied += 1

    if missing_job_ids:
        raise JobPackError(
            "translations missing for job IDs: " + ", ".join(missing_job_ids[:20])
        )

    Typesetting(config).typesetting_document(docs)
    input_doc = pymupdf.open(str(input_pdf))
    try:
        mediabox_data = high_level.fix_media_box(input_doc)
    finally:
        input_doc.close()

    pdf_creater = PDFCreater(str(input_pdf), docs, config, mediabox_data)
    result = pdf_creater.write(config)

    outputs = _collect_result_paths(result)
    return {
        "job_dir": str(job_dir),
        "translations_file": str(translations_file),
        "applied_jobs": applied,
        "outputs": outputs,
    }


def _new_translation_config(
    *,
    translator: EchoTranslator,
    input_pdf: Path,
    lang_in: str,
    lang_out: str,
    output_dir: Path,
    working_dir: Path,
    doc_layout_model: Any,
    pages: str | None,
    skip_scanned_detection: bool,
    debug: bool,
    no_dual: bool = False,
    no_mono: bool = False,
) -> TranslationConfig:
    return TranslationConfig(
        translator=translator,
        term_extraction_translator=translator,
        input_file=str(input_pdf),
        lang_in=lang_in,
        lang_out=lang_out,
        doc_layout_model=doc_layout_model,
        pages=pages,
        output_dir=str(output_dir),
        working_dir=str(working_dir),
        debug=debug,
        no_dual=no_dual,
        no_mono=no_mono,
        qps=1,
        table_model=None,
        skip_scanned_detection=skip_scanned_detection,
        auto_extract_glossary=False,
        skip_translation=True,
    )


def _prepare_docs_for_jobpack(config: TranslationConfig):
    input_pdf = Path(config.input_file).resolve()
    copied_input_pdf = Path(config.get_working_file_path("jobpack_input.pdf"))
    shutil.copy2(input_pdf, copied_input_pdf)

    temp_pdf_path = config.get_working_file_path("input.pdf")
    doc_pdf = pymupdf.open(str(copied_input_pdf))
    high_level.safe_save(doc_pdf, temp_pdf_path)

    try:
        _ = high_level.fix_null_page_content(doc_pdf)
        high_level.fix_filter(doc_pdf)
        high_level.fix_null_xref(doc_pdf)
    except Exception:
        # Keep processing even if auto-fix fails, consistent with BabelDOC behavior.
        pass

    high_level.safe_save(doc_pdf, temp_pdf_path)
    il_creater = high_level.ILCreater(config)
    il_creater.mupdf = doc_pdf

    with Path(temp_pdf_path).open("rb") as file_obj:
        high_level.start_parse_il(
            file_obj,
            doc_zh=doc_pdf,
            resfont=None,
            il_creater=il_creater,
            translation_config=config,
        )

    docs = il_creater.create_il()
    if high_level.check_cid_char(docs):
        raise JobPackError("document contains too many CID chars")

    if not config.skip_scanned_detection:
        DetectScannedFile(config).process(docs, temp_pdf_path, high_level.fix_media_box(doc_pdf))

    docs = LayoutParser(config).process(docs, doc_pdf)
    close_process_pool()
    if config.table_model:
        docs = TableParser(config).process(docs, doc_pdf)
    ParagraphFinder(config).process(docs)
    StylesAndFormulas(config).process(docs)
    doc_pdf.close()

    final_input_copy = Path(config.working_dir).parent / "input.pdf"
    shutil.copy2(copied_input_pdf, final_input_copy)
    return docs, final_input_copy


def _extract_jobs(docs, config: TranslationConfig) -> list[dict[str, Any]]:
    il_translator = ILTranslator(config.translator, config)
    jobs: list[dict[str, Any]] = []

    for page_index, page in enumerate(docs.page):
        page_font_map, xobj_font_map = _build_font_maps(page)
        for paragraph_index, paragraph in enumerate(page.pdf_paragraph):
            tracker = ParagraphTranslateTracker()
            source_text, translate_input = il_translator.pre_translate_paragraph(
                paragraph,
                tracker,
                page_font_map,
                xobj_font_map,
            )
            if source_text is None or translate_input is None:
                continue

            job_id = f"p{page_index}-q{paragraph_index}"
            jobs.append(
                {
                    "id": job_id,
                    "page_index": page_index,
                    "paragraph_index": paragraph_index,
                    "paragraph_debug_id": getattr(paragraph, "debug_id", None),
                    "layout_label": getattr(paragraph, "layout_label", None),
                    "source_text": source_text,
                    "source_unicode": getattr(paragraph, "unicode", None),
                    "token_count": il_translator.calc_token_count(source_text),
                    "placeholders": [item.to_dict() for item in translate_input.placeholders],
                    "original_placeholder_tokens": getattr(
                        translate_input,
                        "original_placeholder_tokens",
                        {},
                    ),
                }
            )
    return jobs


def _build_font_maps(page) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    page_font_map: dict[str, Any] = {}
    for font in page.pdf_font:
        page_font_map[font.font_id] = font

    xobj_font_map: dict[int, dict[str, Any]] = {}
    for xobj in page.pdf_xobject:
        merged_font_map = page_font_map.copy()
        for font in xobj.pdf_font:
            merged_font_map[font.font_id] = font
        xobj_font_map[xobj.xobj_id] = merged_font_map
    return page_font_map, xobj_font_map


def _load_doc_layout_model(rpc_doclayout: str | None):
    if rpc_doclayout:
        from babeldoc.docvision.rpc_doclayout import RpcDocLayoutModel

        return RpcDocLayoutModel(host=rpc_doclayout)
    return DocLayoutModel.load_onnx()


def _maybe_init_font_mapper(doc_layout_model, config: TranslationConfig) -> None:
    if doc_layout_model is None:
        return
    init_fn = getattr(doc_layout_model, "init_font_mapper", None)
    if callable(init_fn):
        init_fn(config)


def _load_translations(translations_file: Path) -> dict[str, str]:
    if not translations_file.exists():
        raise JobPackError(f"translations file does not exist: {translations_file}")
    raw = json.loads(translations_file.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items()}
    if isinstance(raw, list):
        result: dict[str, str] = {}
        for item in raw:
            if not isinstance(item, dict):
                raise JobPackError("invalid translations list item, expected object")
            job_id = item.get("id")
            translated_text = item.get("translated_text")
            if job_id is None or translated_text is None:
                raise JobPackError("each translation item needs id and translated_text")
            result[str(job_id)] = str(translated_text)
        return result
    raise JobPackError("translations file must be an object or an array")


def _collect_result_paths(result) -> list[str]:
    paths = []
    for attr in (
        "mono_pdf_path",
        "dual_pdf_path",
        "no_watermark_mono_pdf_path",
        "no_watermark_dual_pdf_path",
    ):
        path = getattr(result, attr, None)
        if path:
            resolved = str(Path(path).resolve())
            if resolved not in paths:
                paths.append(resolved)
    return paths

