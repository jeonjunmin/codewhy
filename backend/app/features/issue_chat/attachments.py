"""이슈/댓글 첨부 URL → Bedrock Converse 멀티모달 블록 변환.

첨부는 URL 만으로 들어오므로 직접 내려받아야 한다. 내려받기는 vcs.py 와 동일한
보안 자세를 따른다:
  - 호스트 화이트리스트(github / githubusercontent / gitlab(+사내) / 첨부 도메인 허용목록)
    에만 요청을 보낸다 — 위장 도메인으로의 토큰 유출·SSRF 차단(vcs._host_matches 재사용).
  - 토큰은 해당 호스트에만 실어 보낸다(GitHub Bearer / GitLab PRIVATE-TOKEN).

Bedrock Converse 제약:
  - image  format ∈ {png, jpeg, gif, webp},        한 장 ≤ 3.75MB
  - document format ∈ {pdf, csv, doc, docx, xls, xlsx, html, txt, md}, 한 개 ≤ 4.5MB
  - document name 은 영숫자/공백/하이픈/괄호/대괄호만, 연속 공백 불가, 요청 내 유일해야 함.
미지원 포맷(.hwp 등)·용량 초과·다운로드 실패는 블록을 만들지 않고 건너뛴다.
컨텍스트 텍스트에 이미 첨부 목록(이름·링크)이 들어가므로, 건너뛴 첨부도 모델은 존재를 안다.

👤 담당: 이슈 챗봇
"""

import io
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from html import unescape

from app.core.config import (
    get_attachment_domain_allowlist,
    get_github_token,
    get_gitlab_token,
    get_self_hosted_gitlab_hosts,
)
from app.core.vcs import _host_matches  # 정확 일치/정식 하위 도메인 판별(SSRF 안전)

logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT = 10        # 초 — 첨부 다운로드 한 건 상한
_MAX_IMAGE_BYTES = 3_750_000  # Converse image 한도
_MAX_DOC_BYTES = 4_500_000    # Converse document 한도(모델에 바이트째 보내는 포맷)
_MAX_EXTRACT_BYTES = 15_000_000  # 텍스트만 뽑는 포맷(pptx 등)은 더 크게 받아도 됨(바이트를 모델로 안 보냄)
_MAX_IMAGES = 8               # 요청당 이미지 수 상한(토큰·비용 방어)
_MAX_DOCS = 5                 # 요청당 문서 수 상한
_MAX_TOTAL_BYTES = 25_000_000 # 요청당 첨부 총 바이트 상한

# 확장자 → Converse 포맷 (이미지)
_IMAGE_EXT = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg", "gif": "gif", "webp": "webp"}
# 확장자 → Converse 포맷 (문서). htm 은 html 로 정규화.
_DOC_EXT = {
    "pdf": "pdf", "csv": "csv", "doc": "doc", "docx": "docx",
    "xls": "xls", "xlsx": "xlsx", "html": "html", "htm": "html",
    "txt": "txt", "md": "md",
}
# Content-Type → Converse 포맷 (확장자 없는 user-attachments 대비)
_CTYPE_MAP = {
    "image/png": ("image", "png"),
    "image/jpeg": ("image", "jpeg"),
    "image/gif": ("image", "gif"),
    "image/webp": ("image", "webp"),
    "application/pdf": ("document", "pdf"),
    "text/plain": ("document", "txt"),
    "text/markdown": ("document", "md"),
    "text/html": ("document", "html"),
    "text/csv": ("document", "csv"),
    "application/msword": ("document", "doc"),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ("document", "docx"),
    "application/vnd.ms-excel": ("document", "xls"),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ("document", "xlsx"),
}

_NAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9 ()\[\]-]+")
_MULTISPACE_RE = re.compile(r"\s+")


def _ext_of(url: str) -> str:
    """URL 경로의 확장자(소문자, 점 제외). 없으면 빈 문자열."""
    path = urllib.parse.urlparse(url).path
    _, _, ext = path.rpartition(".")
    return ext.lower() if "." in path and len(ext) <= 5 else ""


def _download_allowed(host: str) -> bool:
    host = (host or "").lower()
    if _host_matches(host, "github.com") or _host_matches(host, "githubusercontent.com"):
        return True
    if _host_matches(host, "gitlab.com") or host in get_self_hosted_gitlab_hosts():
        return True
    return any(_host_matches(host, d) for d in get_attachment_domain_allowlist())


def _download_headers(host: str) -> dict:
    """호스트에 맞는 인증 헤더 — 토큰은 그 호스트에만 실어 보낸다."""
    host = (host or "").lower()
    headers = {"User-Agent": "codewhy"}
    if _host_matches(host, "github.com") or _host_matches(host, "githubusercontent.com"):
        token = get_github_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    elif _host_matches(host, "gitlab.com") or host in get_self_hosted_gitlab_hosts():
        token = get_gitlab_token()
        if token:
            headers["PRIVATE-TOKEN"] = token
    return headers


def _download(url: str, max_bytes: int) -> tuple[bytes, str] | None:
    """허용 호스트면 바이트와 content-type 을 내려받는다. 실패/비허용/초과 시 None."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    host = parsed.hostname or ""
    if not _download_allowed(host):
        logger.info("[issue_chat] 첨부 다운로드 비허용 호스트 — %s", host)
        return None
    try:
        req = urllib.request.Request(url, headers=_download_headers(host))
        with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            # 한도+1 까지만 읽어 초과 여부를 판별(메모리 폭주 방지).
            data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            return None
        return data, ctype
    except (urllib.error.URLError, OSError, ValueError) as e:
        logger.info("[issue_chat] 첨부 다운로드 실패 — %s : %s", url, e)
        return None


def _classify(url: str, ctype: str) -> tuple[str, str] | None:
    """(kind, format) 판별 — 확장자 우선, 없으면 content-type. 미지원이면 None."""
    ext = _ext_of(url)
    if ext in _IMAGE_EXT:
        return ("image", _IMAGE_EXT[ext])
    if ext in _DOC_EXT:
        return ("document", _DOC_EXT[ext])
    return _CTYPE_MAP.get(ctype)


def _safe_doc_name(label: str, used: set[str]) -> str:
    """Converse document name 규칙에 맞게 정제하고 요청 내 유일성을 보장한다."""
    name = _MULTISPACE_RE.sub(" ", _NAME_SANITIZE_RE.sub(" ", label or "")).strip()
    if not name:
        name = "document"
    base, n = name, 1
    while name.lower() in used:
        n += 1
        name = f"{base} ({n})"
    used.add(name.lower())
    return name


def collect_attachments(req) -> list[dict]:
    """req.attachments + 각 댓글의 attachments 를 url 기준 중복 제거해 모은다."""
    out: list[dict] = []
    seen: set[str] = set()

    def add(items):
        for a in items or []:
            url = (a.get("url") or "").strip() if isinstance(a, dict) else ""
            if not url or url in seen:
                continue
            seen.add(url)
            out.append({"label": (a.get("label") or "").strip(), "url": url})

    add(req.attachments)
    for c in req.comments or []:
        if isinstance(c, dict):
            add(c.get("attachments"))
    return out


def build_blocks(req) -> tuple[list[dict], list[str]]:
    """req.attachments(+댓글 첨부)를 내려받아 Converse 멀티모달 블록으로 변환한다."""
    return build_blocks_from_list(collect_attachments(req))


def build_blocks_from_list(attachments: list[dict]) -> tuple[list[dict], list[str]]:
    """{label,url} 리스트를 Converse 멀티모달 블록으로 변환한다(req 비의존 — blame 도 재사용).

    반환: (blocks, skipped_labels)
      blocks         — converse content 에 끼울 image/document 블록들
      skipped_labels — 미지원/실패/초과로 본문(텍스트)만 남는 첨부 라벨(로그/안내용)
    """
    blocks: list[dict] = []
    skipped: list[str] = []
    used_names: set[str] = set()
    images = docs = 0
    total = 0

    for a in attachments:
        url, label = a["url"], a["label"] or _filename(a["url"])
        budget = _MAX_TOTAL_BYTES - total
        if budget <= 0:
            skipped.append(label)
            continue

        # 텍스트만 추출하는 포맷(pptx)은 바이트를 모델로 안 보내므로 더 크게 받아도 된다.
        per_file_cap = _MAX_EXTRACT_BYTES if _ext_of(url) == "pptx" else _MAX_DOC_BYTES
        downloaded = _download(url, min(budget, per_file_cap))
        if downloaded is None:
            skipped.append(label)
            continue
        data, ctype = downloaded

        kind_fmt = _classify(url, ctype)
        if kind_fmt is None:
            # Converse 가 직접 못 읽는 포맷(pptx 등)은 우리가 텍스트를 추출해 본문으로 투입.
            extracted = _extract_text_fallback(url, ctype, data)
            if extracted and docs < _MAX_DOCS:
                blocks.append({"text": f"[첨부 문서 내용: {label}]\n{extracted}"})
                docs += 1
                total += len(extracted)
            else:
                skipped.append(label)  # .hwp 등 추출 불가 → 링크만
            continue
        kind, fmt = kind_fmt

        if kind == "image":
            if images >= _MAX_IMAGES or len(data) > _MAX_IMAGE_BYTES:
                skipped.append(label)
                continue
            blocks.append({"image": {"format": fmt, "source": {"bytes": data}}})
            images += 1
        else:
            if docs >= _MAX_DOCS or len(data) > _MAX_DOC_BYTES:
                skipped.append(label)
                continue
            blocks.append({"document": {"format": fmt, "name": _safe_doc_name(label, used_names),
                                        "source": {"bytes": data}}})
            docs += 1
        total += len(data)

    if blocks or skipped:
        logger.info("[attachments] 첨부 멀티모달 변환 — 이미지 %d · 문서 %d · 건너뜀 %d",
                    images, docs, len(skipped))
    return blocks, skipped


def _filename(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    return path.rsplit("/", 1)[-1] or url


# ── Converse 미지원 포맷의 텍스트 추출 ──────────────────────────────────────
# Bedrock Converse document 블록은 pdf/csv/doc/docx/xls/xlsx/html/txt/md 만 지원한다.
# pptx 처럼 그 밖의 Office 포맷은 모델이 직접 못 읽으므로, 우리가 본문 텍스트를 뽑아
# 텍스트로 넣는다(이미지/도형 텍스트·발표자 노트는 빠질 수 있음 — 슬라이드 본문 위주).
_PPTX_CTYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_MAX_EXTRACTED_CHARS = 6000
_PPTX_TEXT_RE = re.compile(r"<a:t>(.*?)</a:t>", re.DOTALL)
_SLIDE_NUM_RE = re.compile(r"slide(\d+)\.xml$")


def _extract_text_fallback(url: str, ctype: str, data: bytes) -> str:
    """Converse 가 못 읽는 포맷에서 텍스트를 추출한다. 불가하면 ""."""
    ext = _ext_of(url)
    if ext == "pptx" or ctype == _PPTX_CTYPE:
        return _extract_pptx_text(data)
    return ""


def _extract_pptx_text(data: bytes, max_chars: int = _MAX_EXTRACTED_CHARS) -> str:
    """pptx(zip) 슬라이드 XML 의 <a:t> 런을 슬라이드 순서대로 모아 텍스트로 반환."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError):
        return ""
    slides = sorted(
        (n for n in zf.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")),
        key=lambda n: int(m.group(1)) if (m := _SLIDE_NUM_RE.search(n)) else 0,
    )
    out: list[str] = []
    total = 0
    for i, name in enumerate(slides, 1):
        try:
            xml = zf.read(name).decode("utf-8", "ignore")
        except (KeyError, OSError):
            continue
        runs = [unescape(t).strip() for t in _PPTX_TEXT_RE.findall(xml)]
        text = " ".join(t for t in runs if t)
        if not text:
            continue
        block = f"[슬라이드 {i}] {text}"
        if total + len(block) > max_chars:
            out.append(block[: max(0, max_chars - total)] + " …(생략)")
            break
        out.append(block)
        total += len(block)
    return "\n".join(out)
