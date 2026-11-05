"""
docling-md-mcp — PDF/PPTX/DOCX 를 Markdown 으로 변환해 캐시해 두고,
Claude 에게는 '목차 → 필요한 페이지/섹션만' 넘겨서 토큰을 아끼는 MCP 서버.

핵심 원칙
- 변환 결과 전체를 한 번에 돌려주지 않는다. convert_document 는 요약(목차·페이지 수·토큰 추정)만 반환.
- 본문은 read_pages / read_section / search_document 로 필요한 만큼만 꺼낸다.
- 한 번 변환한 파일은 캐시(파일 해시 기준)해서 다시 변환하지 않는다.
- 변환이 오래 걸리면 백그라운드로 넘기고, 나중에 다시 호출하면 결과를 준다(MCP 타임아웃 회피).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

# stdio MCP 에서는 stdout 이 프로토콜 채널이라 로그는 반드시 stderr 로.
logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format="[docling-md] %(levelname)s %(message)s")
log = logging.getLogger("docling-md")

CACHE_DIR = Path(os.environ.get("DOCLING_MD_CACHE",
                                Path.home() / ".docling-md-cache")).expanduser()
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 동기 대기 시간(초). 이보다 오래 걸리면 백그라운드로 계속 변환하고 "진행 중"을 돌려준다.
WAIT_SECONDS = float(os.environ.get("DOCLING_MD_WAIT", "40"))
# 한 번에 돌려줄 본문의 최대 글자 수 (실수로 큰 덩어리를 넘기는 걸 막는 안전장치)
MAX_CHARS = int(os.environ.get("DOCLING_MD_MAX_CHARS", "30000"))
# OCR 언어 (스캔본일 때만 사용)
OCR_LANGS = [s.strip() for s in os.environ.get("DOCLING_MD_OCR_LANGS", "ko,en").split(",") if s.strip()]

SUPPORTED = {".pdf", ".pptx", ".docx", ".xlsx", ".html", ".htm", ".md",
             ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}

mcp = FastMCP("docling-md")

# ---------------------------------------------------------------------------
# 변환기 (무거우므로 지연 로딩 + 옵션별로 재사용)
# ---------------------------------------------------------------------------
_converters: dict[tuple, Any] = {}
_conv_lock = threading.Lock()


def _device_info() -> dict:
    import torch
    available = ["cpu"]
    if torch.cuda.is_available():
        available.append("cuda")
    if hasattr(torch, "xpu") and torch.xpu.is_available():
        available.append("xpu")
    requested = os.environ.get("DOCLING_MD_DEVICE", "auto").lower()
    if requested not in {"auto", "cpu", "cuda", "xpu"}:
        raise ValueError("DOCLING_MD_DEVICE: auto / cpu / cuda / xpu 만 지원합니다")
    selected = next((d for d in ("cuda", "xpu") if d in available), "cpu") if requested == "auto" else requested
    if selected not in available:
        log.warning("%s 사용 불가 → CPU 사용", selected)
        selected = "cpu"
    return {"torch": torch.__version__, "requested": requested,
            "selected": selected, "available": available}


def _get_converter(ocr: bool, formulas: bool, device: str | None = None):
    device = device or _device_info()["selected"]
    key = (ocr, formulas, device)
    with _conv_lock:
        if key in _converters:
            return _converters[key]
        if device == "xpu":
            from xpu_compat import enable_xpu_postprocess_workaround
            enable_xpu_postprocess_workaround()
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        from docling.datamodel.accelerator_options import AcceleratorOptions
        opts = PdfPipelineOptions()
        opts.accelerator_options = AcceleratorOptions(
            device=device,
            num_threads=max(1, int(os.environ.get("DOCLING_MD_THREADS", (os.cpu_count() or 2) // 2))),
        )
        opts.do_ocr = ocr
        opts.do_table_structure = True
        opts.do_formula_enrichment = formulas
        opts.generate_picture_images = False  # 그림은 토큰 절약을 위해 자리표시만
        if ocr:
            # 한국어는 EasyOCR 이 잘 읽는다. 설치돼 있으면 쓰고, 없으면 Docling 기본 OCR.
            import importlib.util
            if importlib.util.find_spec("easyocr") is not None:
                from docling.datamodel.pipeline_options import EasyOcrOptions
                opts.ocr_options = EasyOcrOptions(lang=OCR_LANGS)
            else:
                log.warning("easyocr 미설치 → 기본 OCR 사용 (한국어 인식이 약할 수 있음). "
                            "'pip install easyocr' 권장")

        conv = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )
        _converters[key] = conv
        return conv


# ---------------------------------------------------------------------------
# 캐시
# ---------------------------------------------------------------------------
def _file_hash(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _cache_key(p: Path, ocr: bool, formulas: bool) -> str:
    return f"{_file_hash(p)}-o{int(ocr)}f{int(formulas)}"


def _entry_dir(key: str) -> Path:
    return CACHE_DIR / key


def _estimate_tokens(text: str) -> int:
    # 대략치: 한글은 글자당 토큰이 많고 영어는 적다. 섞인 문서 기준 보수적으로.
    hangul = len(re.findall(r"[\uac00-\ud7a3]", text))
    other = len(text) - hangul
    return int(hangul * 0.9 + other / 3.8)


def _build_cache(src: Path, key: str, ocr: bool, formulas: bool) -> dict:
    """실제 변환. 결과를 캐시 디렉터리에 저장하고 메타데이터를 반환."""
    t0 = time.time()
    device = _device_info()["selected"]
    try:
        result = _get_converter(ocr, formulas, device).convert(str(src))
    except Exception:
        if device == "cpu":
            raise
        log.exception("%s 변환 실패 → CPU로 다시 시도", device)
        device = "cpu"
        result = _get_converter(ocr, formulas, device).convert(str(src))
    doc = result.document

    d = _entry_dir(key)
    tmp = d.with_suffix(".tmp")
    tmp.mkdir(parents=True, exist_ok=True)

    full_md = doc.export_to_markdown()
    (tmp / "full.md").write_text(full_md, encoding="utf-8")

    # 페이지별 Markdown
    try:
        n_pages = doc.num_pages()
    except Exception:
        n_pages = len(getattr(doc, "pages", {}) or {})
    pages: dict[str, str] = {}
    for pno in range(1, n_pages + 1):
        try:
            pages[str(pno)] = doc.export_to_markdown(page_no=pno)
        except Exception as e:
            log.warning("페이지 %d 추출 실패: %s", pno, e)
            pages[str(pno)] = ""
    (tmp / "pages.json").write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")

    # 목차(제목 + 페이지)
    outline = []
    try:
        for item, _level in doc.iterate_items():
            label = str(getattr(item, "label", "")).lower()
            if label.endswith("title") or label.endswith("section_header"):
                text = (getattr(item, "text", "") or "").strip()
                if not text:
                    continue
                prov = getattr(item, "prov", None) or []
                page = prov[0].page_no if prov else None
                lvl = getattr(item, "level", 0 if label.endswith("title") else 1)
                outline.append({"title": text[:120], "page": page, "level": lvl})
    except Exception as e:
        log.warning("목차 추출 실패: %s", e)

    n_pictures = len(getattr(doc, "pictures", []) or [])
    n_tables = len(getattr(doc, "tables", []) or [])

    meta = {
        "source": str(src),
        "name": src.name,
        "key": key,
        "pages": n_pages,
        "chars": len(full_md),
        "est_tokens": _estimate_tokens(full_md),
        "pictures": n_pictures,
        "tables": n_tables,
        "ocr": ocr,
        "formulas": formulas,
        "converted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seconds": round(time.time() - t0, 1),
        "device": device,
    }
    (tmp / "outline.json").write_text(json.dumps(outline, ensure_ascii=False), encoding="utf-8")
    (tmp / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    if d.exists():
        import shutil
        shutil.rmtree(d)
    tmp.rename(d)
    log.info("변환 완료: %s (%d쪽, %.1fs)", src.name, n_pages, meta["seconds"])
    return meta


# 진행 중인 변환 작업: key -> {"thread", "error"}
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
# 원본 경로 -> 마지막으로 쓴 캐시 key (같은 세션에서 경로로 다시 찾기 위함)
_path_index: dict[str, str] = {}


def _index_file() -> Path:
    return CACHE_DIR / "index.json"


def _load_index() -> None:
    try:
        _path_index.update(json.loads(_index_file().read_text(encoding="utf-8")))
    except Exception:
        pass


def _save_index() -> None:
    try:
        _index_file().write_text(json.dumps(_path_index, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
    except Exception as e:
        log.warning("인덱스 저장 실패: %s", e)


_load_index()


def _resolve(path: str) -> Path:
    p = Path(os.path.expandvars(path.strip().strip('"').strip("'"))).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"파일이 없습니다: {p}")
    if p.is_dir():
        raise IsADirectoryError(f"폴더입니다. 파일 경로를 주세요 (폴더는 convert_folder 사용): {p}")
    if p.suffix.lower() not in SUPPORTED:
        raise ValueError(f"지원하지 않는 형식: {p.suffix}  (지원: {', '.join(sorted(SUPPORTED))})")
    return p.resolve()


def _ensure(path: str, ocr: bool = False, formulas: bool = False,
            force: bool = False, wait: float | None = None) -> tuple[str, dict | None, str | None]:
    """
    캐시를 보장한다. 반환: (key, meta 또는 None, 상태메시지 또는 None)
    meta 가 None 이면 아직 변환 중(또는 실패).
    """
    try:
        src = _resolve(path)
    except (FileNotFoundError, IsADirectoryError, ValueError, OSError) as e:
        return "", None, str(e)
    key = _cache_key(src, ocr, formulas)
    d = _entry_dir(key)

    if force and d.exists():
        import shutil
        shutil.rmtree(d)

    if (d / "meta.json").exists():
        _path_index[str(src)] = key
        _save_index()
        return key, json.loads((d / "meta.json").read_text(encoding="utf-8")), None

    with _jobs_lock:
        job = _jobs.get(key)
        if job is None or not job["thread"].is_alive():
            job = {"error": None, "started": time.time()}

            def run():
                try:
                    _build_cache(src, key, ocr, formulas)
                except Exception as e:  # noqa: BLE001
                    log.exception("변환 실패")
                    job["error"] = f"{type(e).__name__}: {e}"

            t = threading.Thread(target=run, daemon=True)
            job["thread"] = t
            _jobs[key] = job
            t.start()

    job["thread"].join(timeout=WAIT_SECONDS if wait is None else wait)
    if job.get("error"):
        return key, None, f"변환 실패: {job['error']}"
    if (d / "meta.json").exists():
        _path_index[str(src)] = key
        _save_index()
        return key, json.loads((d / "meta.json").read_text(encoding="utf-8")), None
    elapsed = int(time.time() - job["started"])
    return key, None, (f"아직 변환 중입니다 ({elapsed}초 경과). 처음 실행이면 모델 다운로드 때문에 더 걸립니다. "
                       f"잠시 후 같은 도구를 같은 경로로 다시 호출하세요.")


def _load(key: str, name: str):
    return json.loads((_entry_dir(key) / name).read_text(encoding="utf-8"))


def _clip(text: str, limit: int | None = None) -> str:
    limit = limit or MAX_CHARS
    if len(text) <= limit:
        return text
    return (text[:limit] +
            f"\n\n…[잘림: 전체 {len(text):,}자 중 {limit:,}자만 표시. 범위를 좁혀서 다시 요청하세요]")


def _outline_text(outline: list[dict], max_items: int = 80) -> str:
    if not outline:
        return "(제목 구조를 찾지 못했습니다 — read_pages 로 페이지 단위로 읽으세요)"
    lines = []
    for o in outline[:max_items]:
        indent = "  " * max(0, int(o.get("level") or 1) - 1)
        pg = f"p.{o['page']}" if o.get("page") else "p.?"
        lines.append(f"{indent}- {o['title']}  ({pg})")
    if len(outline) > max_items:
        lines.append(f"… 외 {len(outline) - max_items}개")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MCP 도구
# ---------------------------------------------------------------------------
@mcp.tool()
def convert_document(path: str, ocr: bool = False, formulas: bool = False,
                     force: bool = False) -> str:
    """
    문서(PDF/PPTX/DOCX 등)를 Markdown 으로 변환해 캐시하고, 본문 대신 '요약 정보'만 반환한다:
    페이지 수, 예상 토큰, 표/그림 수, 목차(제목과 페이지 번호).
    본문은 이 결과를 보고 read_pages / read_section / search_document 로 필요한 부분만 읽을 것.
    전체 본문이 꼭 필요할 때만 read_full 을 쓴다.

    path: 사용자 컴퓨터의 절대 경로 (예: C:\\Users\\me\\Downloads\\week5.pdf)
    ocr: 스캔본(텍스트 선택이 안 되는 PDF)일 때만 True. 느려진다.
    formulas: 수식을 LaTeX 로 뽑아야 할 때만 True. 느려진다.
    force: 캐시를 무시하고 다시 변환.
    """
    key, meta, msg = _ensure(path, ocr, formulas, force)
    if meta is None:
        return msg or "알 수 없는 상태"
    outline = _load(key, "outline.json")
    head = (f"# {meta['name']}\n"
            f"- 페이지: {meta['pages']}쪽 | 표: {meta['tables']} | 그림: {meta['pictures']} (그림 내용은 포함 안 됨)\n"
            f"- 전체 본문: {meta['chars']:,}자, 약 {meta['est_tokens']:,} 토큰 추정\n"
            f"- 변환: {meta['converted_at']} ({meta['seconds']}초), ocr={meta['ocr']}, formulas={meta['formulas']}\n")
    if meta["pages"] and meta["chars"] < 40 * meta["pages"]:
        head += "- ⚠ 페이지당 글자가 매우 적습니다. 스캔본이면 ocr=True, force=True 로 다시 변환하세요.\n"
    return head + "\n## 목차\n" + _outline_text(outline)


@mcp.tool()
def get_outline(path: str) -> str:
    """이미 변환한 문서의 목차(제목·페이지)만 반환. 변환 전이면 변환부터 한다."""
    return convert_document(path)


@mcp.tool()
def read_pages(path: str, start: int, end: int | None = None,
               ocr: bool = False, formulas: bool = False) -> str:
    """
    지정한 페이지 범위의 Markdown 만 반환한다 (1부터 시작, end 포함).
    PPTX 는 슬라이드 번호 = 페이지 번호.
    """
    key, meta, msg = _ensure(path, ocr, formulas)
    if meta is None:
        return msg or "알 수 없는 상태"
    pages = _load(key, "pages.json")
    end = end or start
    if start < 1 or end < start:
        return "잘못된 범위입니다."
    end = min(end, meta["pages"] or end)
    parts = []
    for p in range(start, end + 1):
        body = pages.get(str(p), "").strip()
        parts.append(f"<!-- p.{p} -->\n{body if body else '(텍스트 없음 — 그림 위주 페이지일 수 있음)'}")
    return _clip("\n\n".join(parts))


@mcp.tool()
def read_section(path: str, heading: str, ocr: bool = False, formulas: bool = False) -> str:
    """
    제목(heading)에 해당하는 섹션 본문만 반환한다. 다음 같은/상위 레벨 제목 전까지.
    heading 은 목차에 나온 제목의 일부만 써도 된다 (대소문자 무시).
    """
    key, meta, msg = _ensure(path, ocr, formulas)
    if meta is None:
        return msg or "알 수 없는 상태"
    md = (_entry_dir(key) / "full.md").read_text(encoding="utf-8")
    lines = md.splitlines()
    q = heading.strip().lower()
    hdr = re.compile(r"^(#{1,6})\s+(.*)$")

    start_idx, start_lvl = None, None
    for i, ln in enumerate(lines):
        m = hdr.match(ln)
        if m and q in m.group(2).lower():
            start_idx, start_lvl = i, len(m.group(1))
            break
    if start_idx is None:
        cands = [m.group(2) for ln in lines if (m := hdr.match(ln))]
        near = [c for c in cands if any(w in c.lower() for w in q.split())][:10]
        return ("해당 제목을 찾지 못했습니다." +
                (f" 비슷한 제목: {near}" if near else " get_outline 으로 제목을 확인하세요."))

    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        m = hdr.match(lines[j])
        if m and len(m.group(1)) <= start_lvl:
            end_idx = j
            break
    return _clip("\n".join(lines[start_idx:end_idx]).strip())


@mcp.tool()
def search_document(path: str, query: str, max_results: int = 8, context_chars: int = 220,
                    ocr: bool = False, formulas: bool = False) -> str:
    """
    문서에서 키워드를 찾아 '페이지 번호 + 주변 문장'만 반환한다.
    여러 단어를 공백으로 주면 모두 포함된 위치를 우선한다. 결과를 보고 read_pages 로 해당 페이지를 읽을 것.
    """
    key, meta, msg = _ensure(path, ocr, formulas)
    if meta is None:
        return msg or "알 수 없는 상태"
    pages = _load(key, "pages.json")
    terms = [t.lower() for t in query.split() if t.strip()]
    if not terms:
        return "검색어가 비었습니다."

    hits = []
    for pno, text in pages.items():
        low = text.lower()
        score = sum(low.count(t) for t in terms)
        if score == 0:
            continue
        allin = all(t in low for t in terms)
        pos = low.find(terms[0])
        if pos < 0:
            pos = min((low.find(t) for t in terms if t in low), default=0)
        a, b = max(0, pos - context_chars), min(len(text), pos + context_chars)
        snippet = re.sub(r"\s+", " ", text[a:b]).strip()
        hits.append((allin, score, int(pno), snippet))

    if not hits:
        return f"'{query}' 결과 없음."
    hits.sort(key=lambda h: (not h[0], -h[1], h[2]))
    out = [f"'{query}' — {len(hits)}개 페이지에서 발견 (상위 {min(max_results, len(hits))}개):"]
    for allin, score, pno, snip in hits[:max_results]:
        out.append(f"- p.{pno} ({score}회{'' if allin else ', 일부 단어만'}): …{snip}…")
    return "\n".join(out)


@mcp.tool()
def read_full(path: str, ocr: bool = False, formulas: bool = False) -> str:
    """
    전체 Markdown 을 반환한다. 토큰을 많이 쓰므로 문서가 짧거나(약 5천 토큰 이하)
    사용자가 전체를 명시적으로 원할 때만 쓴다. 길면 MAX_CHARS 에서 잘린다.
    """
    key, meta, msg = _ensure(path, ocr, formulas)
    if meta is None:
        return msg or "알 수 없는 상태"
    md = (_entry_dir(key) / "full.md").read_text(encoding="utf-8")
    return _clip(md)


@mcp.tool()
def export_markdown(path: str, output_path: str | None = None,
                    ocr: bool = False, formulas: bool = False) -> str:
    """
    변환한 Markdown 을 파일로 저장한다. output_path 를 안 주면 원본 옆에 같은 이름의 .md 로 저장.
    (Claude 웹/앱에 md 파일로 직접 첨부하고 싶을 때 사용)
    """
    key, meta, msg = _ensure(path, ocr, formulas)
    if meta is None:
        return msg or "알 수 없는 상태"
    src = Path(meta["source"])
    out = Path(output_path).expanduser() if output_path else src.with_suffix(".md")
    if out.exists() and out.resolve() == src.resolve():
        return "원본과 같은 경로에는 저장할 수 없습니다."
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text((_entry_dir(key) / "full.md").read_text(encoding="utf-8"), encoding="utf-8")
    return f"저장됨: {out}  ({meta['chars']:,}자, 약 {meta['est_tokens']:,} 토큰)"


@mcp.tool()
def convert_folder(folder: str, recursive: bool = False, export_md: bool = False,
                   ocr: bool = False, formulas: bool = False) -> str:
    """
    폴더 안의 지원 문서를 전부 미리 변환해 캐시에 넣는다(백그라운드). 시험 기간 전에 한 번 돌려두면
    이후 질문은 즉시 응답된다. export_md=True 면 각 파일 옆에 .md 도 저장.
    진행 상황은 list_converted 로 확인.
    """
    root = Path(os.path.expandvars(folder.strip().strip('"'))).expanduser()
    if not root.is_dir():
        return f"폴더가 아닙니다: {root}"
    it = root.rglob("*") if recursive else root.glob("*")
    files = [p for p in it if p.is_file() and p.suffix.lower() in SUPPORTED and p.suffix.lower() != ".md"]
    if not files:
        return "변환할 문서가 없습니다."

    def run_all():
        for f in files:
            try:
                key, meta, msg = _ensure(str(f), ocr, formulas, wait=3600)
                if meta and export_md:
                    out = f.with_suffix(".md")
                    out.write_text((_entry_dir(key) / "full.md").read_text(encoding="utf-8"),
                                   encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                log.warning("%s 실패: %s", f.name, e)

    threading.Thread(target=run_all, daemon=True).start()
    names = ", ".join(f.name for f in files[:15]) + (" …" if len(files) > 15 else "")
    return f"{len(files)}개 파일 변환을 백그라운드에서 시작했습니다: {names}"


@mcp.tool()
def list_converted(limit: int = 30) -> str:
    """캐시에 들어 있는 변환 결과와 진행 중인 작업을 보여준다."""
    rows = []
    for d in sorted(CACHE_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        mf = d / "meta.json"
        if d.is_dir() and mf.exists():
            m = json.loads(mf.read_text(encoding="utf-8"))
            rows.append(f"- {m['name']} — {m['pages']}쪽, 약 {m['est_tokens']:,} 토큰 | {m['source']}")
    running = [k for k, j in _jobs.items() if j["thread"].is_alive()]
    out = [f"캐시 위치: {CACHE_DIR}", f"변환 완료 {len(rows)}개" + (f", 진행 중 {len(running)}개" if running else "")]
    out += rows[:limit]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI: MCP 없이 미리 변환해 두기
#   python server.py convert <파일 또는 폴더> [--ocr] [--formulas] [--md]
# ---------------------------------------------------------------------------
def _cli(argv: list[str]) -> None:
    global WAIT_SECONDS
    WAIT_SECONDS = 24 * 3600
    target = Path(argv[0]).expanduser()
    ocr, formulas, md = "--ocr" in argv, "--formulas" in argv, "--md" in argv
    force = "--force" in argv
    files = ([p for p in target.rglob("*") if p.suffix.lower() in SUPPORTED and p.suffix.lower() != ".md"]
             if target.is_dir() else [target])
    failed = 0
    for f in files:
        print(f"→ {f}", file=sys.stderr)
        key, meta, msg = _ensure(str(f), ocr, formulas, force=force)
        if meta is None:
            print(f"  {msg}", file=sys.stderr)
            failed += 1
            continue
        print(f"  {meta['pages']}쪽, 약 {meta['est_tokens']:,} 토큰, {meta['seconds']}초", file=sys.stderr)
        if md:
            out = f.with_suffix(".md")
            if out.resolve() == f.resolve():
                print("원본 Markdown을 덮어쓸 수 없습니다", file=sys.stderr)
                failed += 1
                continue
            out.write_text((_entry_dir(key) / "full.md").read_text(encoding="utf-8"), encoding="utf-8")
            print(f"  저장: {out}", file=sys.stderr)
    if failed:
        sys.exit(1)


def _bench(path: str, repeats: int = 2) -> None:
    """캐시를 사용하지 않고 워밍업 후 실제 변환 시간을 비교한다."""
    import statistics
    src = _resolve(path)
    info = _device_info()
    print(f"[bench] torch={info['torch']}, available={','.join(info['available'])}", flush=True)
    results = {}
    for device in info["available"]:
        print(f"[bench] {device}: 모델 로딩 및 워밍업 중...", flush=True)
        try:
            conv = _get_converter(False, False, device)
            conv.convert(str(src)).document.export_to_markdown()
            times = []
            for i in range(repeats):
                start = time.perf_counter()
                doc = conv.convert(str(src)).document
                doc.export_to_markdown()
                if device != "cpu":
                    import torch
                    getattr(torch, device).synchronize()
                seconds = time.perf_counter() - start
                times.append(seconds)
                print(f"[bench] {device}: {i+1}/{repeats} {seconds:.2f}s", flush=True)
            results[device] = statistics.median(times)
        except Exception as exc:
            print(f"[bench] {device}: 실패 ({type(exc).__name__}: {exc})", flush=True)
            if device == "cpu":
                raise
    for device, seconds in results.items():
        print(f"[bench] {device}: 중앙값 {seconds:.2f}s (캐시 제외, 워밍업 제외)", flush=True)
    if len(info["available"]) == 1:
        print("[bench] GPU 사용 불가: CPU만 측정했습니다. GPU/CPU 속도 비교는 생략합니다.", flush=True)
    for device in ("cuda", "xpu"):
        if device in results:
            print(f"[bench] {device}: CPU 대비 {results['cpu']/results[device]:.2f}배", flush=True)


def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    convert = sub.add_parser("convert", help="문서 또는 폴더 변환")
    convert.add_argument("path")
    for flag in ("ocr", "formulas", "md", "force"):
        convert.add_argument("--" + flag, action="store_true")
    sub.add_parser("device", help="장치 상태 확인")
    bench = sub.add_parser("bench", help="캐시 없이 CPU/GPU 속도 측정")
    bench.add_argument("path")
    bench.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args(argv)
    if args.command == "convert":
        _cli([args.path] + ["--" + f for f in ("ocr", "formulas", "md", "force") if getattr(args, f)])
    elif args.command == "device":
        print(json.dumps(_device_info(), ensure_ascii=False, indent=2))
    elif args.command == "bench":
        if args.repeats < 1:
            parser.error("--repeats 는 1 이상이어야 합니다")
        _bench(args.path, args.repeats)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
