import os
import glob


def search_documents(keyword: str, document_paths: list[str]) -> list[dict]:
    """키워드로 문서 폴더를 검색하고 연관 문서 목록을 반환한다."""
    results = []
    extensions = ["*.pdf", "*.docx", "*.xlsx", "*.md", "*.txt"]

    for base_path in document_paths:
        for ext in extensions:
            for file_path in glob.glob(os.path.join(base_path, "**", ext), recursive=True):
                excerpt = _extract_excerpt(file_path, keyword)
                if excerpt:
                    results.append({
                        "path": file_path,
                        "page": 1,
                        "excerpt": excerpt,
                    })

    return results[:5]


def _extract_excerpt(file_path: str, keyword: str) -> str | None:
    """텍스트 기반 파일에서 키워드가 포함된 발췌문을 반환한다."""
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".md" or ext == ".txt":
        try:
            with open(file_path, encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if keyword.lower() in content.lower():
                idx = content.lower().find(keyword.lower())
                return content[max(0, idx - 100): idx + 200].strip()
        except Exception:
            pass

    # PDF / DOCX / XLSX 파싱은 추후 pypdf, python-docx, openpyxl로 확장
    return None
