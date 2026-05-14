"""Requirement Trace 비즈니스 로직.

git blame 으로 얻은 커밋 메시지에서 키워드를 뽑고, 설정된 문서 폴더에서 매칭 문서를 찾는다.

TODO (개발자 C):
 - PDF / DOCX / XLSX 파서 추가 (pypdf, python-docx, openpyxl)
 - 단순 키워드 검색을 임베딩 기반 검색으로 확장
 - Elasticsearch 연동 옵션화

👤 담당: 개발자 C
"""

import glob
import os

from app.core import git


def trace(repo_path: str, file_path: str, line: int, document_paths: list[str]) -> list[dict]:
    """라인을 기준으로 연관 기획 문서 후보 목록을 반환한다."""
    keyword = _extract_keyword(repo_path, file_path, line)
    return _search_documents(keyword, document_paths)


def _extract_keyword(repo_path: str, file_path: str, line: int) -> str:
    try:
        info = git.get_blame_info(repo_path, file_path, line)
        return info.message.split("\n")[0][:50]
    except Exception:
        return os.path.basename(file_path)


def _search_documents(keyword: str, document_paths: list[str]) -> list[dict]:
    results: list[dict] = []
    extensions = ["*.pdf", "*.docx", "*.xlsx", "*.md", "*.txt"]

    for base_path in document_paths:
        for ext in extensions:
            for file_path in glob.glob(os.path.join(base_path, "**", ext), recursive=True):
                excerpt = _extract_excerpt(file_path, keyword)
                if excerpt:
                    results.append({"path": file_path, "page": 1, "excerpt": excerpt})

    return results[:5]


def _extract_excerpt(file_path: str, keyword: str) -> str | None:
    """텍스트 기반 파일에서 키워드가 포함된 발췌문을 반환한다."""
    ext = os.path.splitext(file_path)[1].lower()

    if ext in {".md", ".txt"}:
        try:
            with open(file_path, encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            return None
        if keyword.lower() in content.lower():
            idx = content.lower().find(keyword.lower())
            return content[max(0, idx - 100): idx + 200].strip()

    # TODO: PDF / DOCX / XLSX 파싱 확장
    return None
