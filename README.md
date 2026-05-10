# codewhy README

"codewhy" 익스텐션에 대한 간단한 설명을 여기에 작성하세요. 설명 작성을 마친 후에는 아래 섹션들을 포함하는 것을 권장합니다.

## Features

익스텐션의 구체적인 기능들을 설명하세요. 실제 동작하는 스크린샷을 포함하면 더욱 좋습니다. 이미지 경로는 이 README 파일을 기준으로 한 상대 경로로 작성합니다.

예를 들어, 프로젝트 워크스페이스 하위에 images 폴더가 있는 경우:

\!\[feature X\]\(images/feature-x.png\)

> Tip: Many popular extensions utilize animations. This is an excellent way to show off your extension! We recommend short, focused animations that are easy to follow.

## Requirements

특정 요구 사항이나 의존성 패키지가 있다면, 해당 내용과 설치 및 설정 방법을 이 섹션에 작성하세요.

## Settings (Windows 환경)

* Node.js 설치
* 프로젝트 생성 (CMD창에 아래 명령어 차례로 입력)
  - npm install -g yo generator-code
  - yo code
     What type of extension? New Extension (TypeScript)
    What's the name of your extension? my-balloon-ext (원하는 이름)
    Identifier? (엔터 - 기본값)
    Description? (엔터 - 기본값)
    Initialize a git repository? Yes (권장)
    Bundle the source code with webpack? No (처음에는 기본 설정이 쉽습니다)
    Which package manager to use? npm
* 프로젝트 플더 열기 후 설정
  - 프로잭트 내 package.json 파일의 Activation Events 확인
  - 터미널에서 "Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process" 명령어 실행
  - 터미널에서 "npm run compile " 명령어 실행
* 실행
- 컴파일: npm run compile
- 디버깅 시작: F5 키 눌러서 새창 띄우고 test.ts 생성 후 hello 입력
- 확인: 새로 뜬 창에서 test.ts 파일 열고 hello 위에 마우스 올리기
  

