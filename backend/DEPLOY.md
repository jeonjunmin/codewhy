# CodeWhy 백엔드 — AWS EC2 배포 가이드

FastAPI + Uvicorn 백엔드를 **EC2 + RDS(PostgreSQL) + Bedrock + (선택)S3** 구성으로 배포한다.
AWS 자격증명은 키를 박지 않고 **EC2 IAM Instance Profile** 로 주입한다.

---

## 0. 사전 준비 체크리스트

- [ ] AWS Console → **Bedrock → Model access** 에서 사용할 모델 활성화 (`ap-northeast-2`)
- [ ] 도메인 (HTTPS 쓸 경우) — Route 53 또는 외부 DNS
- [ ] 리포지토리 접근 토큰/SSH 키

---

## 1. RDS (PostgreSQL) 생성

1. RDS → 데이터베이스 생성 → **PostgreSQL 16**, 리전 `ap-northeast-2`
2. 초기 DB 이름 `codewhy`, 마스터 사용자/비밀번호 설정
3. **퍼블릭 액세스 아니오** — EC2에서만 접근
4. 보안그룹: 인바운드 **5432** 를 *EC2 보안그룹* 에서만 허용
5. 엔드포인트로 접속 URL 구성:
   ```
   postgresql+asyncpg://<user>:<pass>@<rds-endpoint>:5432/codewhy
   ```

---

## 2. IAM Role (Instance Profile)

EC2에 붙일 역할을 만들면 boto3가 키 없이 자동 인증한다.

신뢰 주체: **EC2**, 연결 정책(최소 권한):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "Bedrock", "Effect": "Allow",
      "Action": ["bedrock:InvokeModel"],
      "Resource": "*" }
    // Knowledge Base(RAG) 사용 시 추가:
    //   "bedrock:Retrieve", "bedrock:RetrieveAndGenerate", "bedrock:StartIngestionJob"
    // S3 문서 인덱싱 사용 시 별도 Statement 로 해당 버킷 s3:GetObject/PutObject
  ]
}
```

> `BEDROCK_KNOWLEDGE_BASE_ID`, `DOC_INDEX_S3_BUCKET` 를 .env 에서 비워두면 해당 권한은 불필요.

---

## 3. EC2 인스턴스

- AMI: Amazon Linux 2023 (또는 Ubuntu 22.04), 타입 **t3.small** 이상
- **2번 IAM Role 연결**
- 보안그룹 인바운드: `80`, `443` (외부), `22` (관리 IP만). **8000 은 외부 공개 금지** — Nginx 뒤에 둔다.

### Docker 설치 (Amazon Linux 2023)
```bash
sudo dnf -y install docker git
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user   # 재로그인 후 적용
# docker compose v2 플러그인
sudo dnf -y install docker-compose-plugin || \
  (sudo mkdir -p /usr/libexec/docker/cli-plugins && \
   sudo curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
   -o /usr/libexec/docker/cli-plugins/docker-compose && \
   sudo chmod +x /usr/libexec/docker/cli-plugins/docker-compose)
```

---

## 4. 코드 배포 & 실행

```bash
git clone https://github.com/jeonjunmin/codewhy.git
cd codewhy/backend

# .env 작성 — AWS_ACCESS_KEY_ID / SECRET 는 비워둔다(IAM Role이 처리)
cp .env.example .env
vi .env
#   DATABASE_URL=postgresql+asyncpg://<user>:<pass>@<rds-endpoint>:5432/codewhy
#   BEDROCK_MODEL_ID=...
#   ANTHROPIC_API_KEY=...   (blame/trace 기능 사용 시)

# (1) DB 마이그레이션 — 최초 1회 + 스키마 변경 시
docker compose run --rm migrate

# (2) 앱 빌드 & 기동
docker compose up -d --build

# 확인
docker compose ps
docker compose logs -f api          # "PostgreSQL 연결 성공" 로그 확인
curl localhost:8000/docs            # Swagger UI 200 이면 정상
```

---

## 5. Nginx + HTTPS (권장)

```bash
sudo dnf -y install nginx
sudo cp deploy/nginx.conf /etc/nginx/conf.d/codewhy.conf
sudo vi /etc/nginx/conf.d/codewhy.conf      # server_name 을 실제 도메인으로
sudo nginx -t && sudo systemctl enable --now nginx

# TLS (도메인이 이 EC2를 가리킨 뒤)
sudo dnf -y install certbot python3-certbot-nginx
sudo certbot --nginx -d api.example.com
```

이후 compose 의 포트 매핑을 `"127.0.0.1:8000:8000"` 으로 좁혀 8000을 외부에서 닫는다.

---

## 6. 운영 메모

- **CORS**: `app/main.py` 가 `allow_origins=["*"]`. 프로덕션은 실제 확장/프론트 origin 으로 좁힐 것.
- **재배포**: `git pull && docker compose up -d --build` (스키마 변경 시 `migrate` 먼저).
- **로그**: `docker compose logs -f api`
- **헬스체크**: compose healthcheck 가 `/docs` 를 폴링. `/health` 엔드포인트를 별도로 두면 더 깔끔.
- **재부팅 내성**: `restart: always` + `systemctl enable docker` 로 인스턴스 재시작 시 자동 기동.
