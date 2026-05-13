# CodeWhy - 지능형 코드 컨텍스트 확장

> "코드의 이름표 대신 사유서를, 커밋 로그 대신 요점 정리를, 코드에서 기획서로 바로 가는 지름길을"

CodeWhy는 AI 기반 코드 이해 도구로, 개발자가 코드의 맥락과 의도를 쉽게 파악할 수 있도록 돕는 VSCode 확장 프로그램입니다.

## 🚀 핵심 기능

### 1. 컨텍스트 블레임 (Context Blame)
**기존의 한계:** `git blame`은 "누가, 언제"만 알려줍니다.
**CodeWhy의 해결:** AI가 "왜"를 설명합니다.

```
기존: 홍길동이 3개월 전에 수정함
→ CodeWhy: 홍길동 님이 3월 15일에 '해외 결제 시 수수료 3% 적용'이라는 
           기획 내용을 반영하기 위해 이 코드를 추가했습니다.
```

### 2. 타임라인 요약 (Timeline Summary)
**기존의 한계:** 파일 변경 히스토리를 파악하려면 수백 개의 커밋을 읽어야 합니다.
**CodeWhy의 해결:** AI가 파일의 일생을 한 눈에 요약합니다.

```
이 파일은 1월에 처음 만들어졌고, 2월에는 로그인 기능이 추가됐으며, 
3월에는 보안 강화를 위해 검증 로직이 한 번 엎어졌습니다.
```

### 3. 요구사항 역추적 (Requirement Traceability)
**기존의 한계:** 코드가 어떤 기획서의 내용인지 찾으려면 수많은 문서를 검색해야 합니다.
**CodeWhy의 해결:** 코드에서 원본 기획서로 바로 연결합니다.

- 코드의 티켓 번호나 키워드를 분석
- 로컬 컴퓨터의 관련 문서를 AI가 자동 검색
- 해당 페이지를 팝업으로 즉시 표시

## 📋 사용 시나리오

### 개발자 A의 하루
```
09:00 - 레거시 코드 수정 요청이 들어옴
09:01 - 문제의 코드 라인에 마우스 오버
09:02 - CodeWhy가 "이 코드는 2023년 블랙프라이데이 이벤트 대응을 위해 추가된 임시 로직입니다" 알려줌
09:03 - 관련 기획서 링크 클릭으로 전체 맥락 파악 완료
09:05 - 안전하게 수정 작업 시작 ✨
```

### 코드 리뷰어 B의 경험
```
14:00 - 신입 개발자의 PR 리뷰 시작
14:01 - 복잡한 비즈니스 로직 발견
14:02 - CodeWhy 타임라인 요약으로 해당 파일의 변경 히스토리 확인
14:03 - "아, 이 로직은 작년 개인정보보호법 개정 대응이었구나" 이해 완료
14:04 - 정확하고 건설적인 피드백 작성 ✨
```

## 🛠 기술 스택

- **Frontend:** TypeScript, VSCode Extension API
- **Backend:** Node.js
- **AI Integration:** OpenAI API / Claude API
- **Git Integration:** Node-Git, Git CLI
- **Document Processing:** PDF.js, SheetJS
- **Search Engine:** Elasticsearch (로컬 문서 검색)

## 📦 설치 및 설정

### 사전 요구사항
- Visual Studio Code 1.118.0 이상
- Node.js 18.x 이상
- Git 2.25 이상

### 설치 방법
```bash
# 개발 환경에서 직접 설치:

git clone https://github.com/jeonjunmin/codewhy.git
cd codewhy
npm install
npm run compile
code .
# F5 키를 눌러 개발 환경에서 실행
```

### 초기 설정
1. **AI API 키 설정**
   - VSCode 설정에서 `codewhy.apiKey` 입력
   - OpenAI 또는 Claude API 키 선택 가능

2. **문서 폴더 연결**
   - `codewhy.documentPaths` 설정에 기획서/문서 폴더 경로 추가
   - 지원 형식: PDF, DOCX, XLSX, MD

3. **프로젝트 설정**
   - `.codewhy.json` 파일로 프로젝트별 세부 설정 가능




## 📄 라이선스

MIT License - 자세한 내용은 [LICENSE](LICENSE) 파일을 참조하세요.

## 👥 팀

- **Lead Developer:** [전준민](https://github.com/jeonjunmin)
- **Project Vision:** AI 기반 코드 이해 도구의 혁신

## 📞 문의

- GitHub Issues: [프로젝트 이슈 페이지](https://github.com/jeonjunmin/codewhy/issues)
- Email: 프로젝트 관련 문의

---

**"코드를 읽는 것이 코드를 쓰는 것보다 어렵다면, 그 이유를 AI가 알려드립니다."**