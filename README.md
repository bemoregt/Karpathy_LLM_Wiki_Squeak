# LLM Wiki Agent

Karpathy의 LLM Wiki 패턴(2026년 4월)을 기반으로 구현한 **컴파운딩 지식 베이스** 시스템.  
기존 SwikiSwiki(Comanche/Squeak) 인트라넷 위키에 Ollama LLM을 연결하여, 문서·PDF·이미지를 수집하면 LLM이 자동으로 위키 페이지를 생성·관리합니다.

---

## 배경: RAG vs LLM Wiki

| | RAG | LLM Wiki |
|---|---|---|
| 방식 | 질문마다 원본 문서를 재검색 | 소스를 읽어 구조화된 위키로 한 번만 컴파일 |
| 지식 | 매번 재발견 | 누적·연결되며 **복리처럼 성장** |
| 검색 | 벡터 DB 필요 | 마크다운/XML 파일, 추가 인프라 없음 |

---

## 시스템 구성

```
ComSwiki/
├── squeak.image              # SwikiSwiki 서버 (Comanche, port 8000)
├── swiki/AIC_Wiki/pages/     # 위키 XML 페이지 저장소 (공유)
│
├── llm_wiki/                 # 기본 LLM Wiki 인스턴스 (port 8001)
│   ├── llm_wiki_agent.py     # 메인 에이전트 + 웹 서버
│   ├── config.json           # 인스턴스별 설정
│   ├── AGENTS.md             # LLM 행동 지침 (스키마)
│   ├── pages.json            # 이 위키가 소유한 페이지 번호 레지스트리
│   └── raw/                  # 수집된 원본 파일 (텍스트, PDF, 이미지)
│
├── security_wiki/            # 추가 인스턴스 예시 (port 8003)
│   └── ...                   # 동일 구조, llm_wiki_agent.py는 심볼릭 링크
│
└── new_llm_wiki.py           # 새 위키 인스턴스 생성 헬퍼
```

### 실행 중인 서비스

| 위키 이름 | 웹 UI | SwikiSwiki 시작 페이지 | systemd 서비스 |
|-----------|-------|----------------------|----------------|
| LLM Wiki | http://localhost:8001 | 300번 | `llm-wiki` |
| 보안 LLM Wiki | http://localhost:8003 | 600번 | `llm-wiki-security-wiki` |

---

## 주요 기능

### 1. 텍스트 문서 수집 (Ingest)
- 텍스트를 붙여넣으면 LLM이 핵심 **엔티티·컨셉 페이지**를 자동 생성
- 기존 페이지와 중복 시 업데이트, 페이지 간 상호 링크 자동 생성
- 인덱스 페이지·작업 로그 자동 갱신

### 2. PDF 분석 및 수집
- PDF 업로드 → `pdftotext`로 텍스트 추출 → LLM 분석 → 위키 페이지 생성
- **8,000자 청크** 단위로 분할 처리하여 대용량 문서도 누락 없이 수집
- 최대 60페이지 처리, 원본 PDF는 `raw/`에 보관

### 3. 이미지 분석 및 수집 (멀티모달)
- JPG·PNG·GIF·WebP·BMP 업로드
- **gemma4 비전**이 이미지 내용을 상세 분석 (수치, 텍스트, 구조 등)
- 분석 설명 → 위키 페이지 자동 생성, 원본 이미지는 `raw/`에 보관

### 4. 질문하기 (Query)
- 자연어 질문 → LLM이 위키 페이지 + 원본 문서를 함께 참조하여 마크다운으로 답변
- 답변 내 `[페이지 302]` 클릭 → SwikiSwiki 해당 페이지로 이동
- 답변 내 `[원본: image.png]` → 이미지 인라인 표시 (클릭으로 확대/축소)
- 한국어 조사 처리(prefix 확장)로 `부채널공격이란` → `부채널공격` 매칭

### 5. 위키 유지보수 (Lint)
- 모순 정보 감지
- 누락된 교차 참조 식별
- 고아 페이지(링크 없는 페이지) 찾기
- 미완성 페이지 보고

---

## 아키텍처

```
브라우저
  │  HTTP (port 8001)
  ▼
WikiHandler (ThreadingMixIn — 동시 요청 처리)
  │
  ├── /ingest          → LLMWikiAgent.ingest()
  ├── /upload-pdf      → LLMWikiAgent.extract_pdf() → pdftotext → _ingest_chunked()
  ├── /upload-image    → LLMWikiAgent.analyze_image() → Ollama Vision
  ├── /query           → LLMWikiAgent.query()
  ├── /lint            → LLMWikiAgent.lint()
  └── /raw-image/<fn>  → raw/ 이미지 파일 서빙
         │
         ▼
  OllamaClient (gemma4:latest, localhost:11434)
         │
         ▼
  SwikiPageManager
    pages.json  ← 이 인스턴스 소유 페이지 번호 레지스트리
    AIC_Wiki/pages/*.xml  ← 실제 위키 페이지 (SwikiSwiki와 공유)
```

### 페이지 관리 (pages.json)
각 위키 인스턴스는 `pages.json`으로 자신이 생성한 페이지 번호를 관리합니다.
- 페이지 수 **무제한** (300개 블록 제한 없음)
- 여러 위키 인스턴스가 같은 `AIC_Wiki/pages/` 디렉토리를 공유해도 **번호 충돌 없음**
- 새 페이지 생성 시 전체 디렉토리에서 빈 번호를 찾아 사용

---

## 설치 및 실행

### 전제 조건
- Ubuntu (systemd 사용자 서비스 지원)
- SwikiSwiki (Squeak/Comanche) — port 8000에서 실행 중
- [Ollama](https://ollama.com) — port 11434에서 실행 중
- Python 3.12+ (표준 라이브러리만 사용, pip 불필요)
- `poppler-utils` (`pdftotext`, `pdfinfo`) — PDF 처리용

```bash
# Ollama 모델 다운로드 (멀티모달 비전 모델)
ollama pull gemma4:latest
```

### 기본 위키 시작

```bash
cd /home/gromit/Music/ComSwiki/llm_wiki
python3 llm_wiki_agent.py
# → http://localhost:8001 에서 웹 UI 접속
```

### systemd 서비스 (재시작 시 자동 시작)

```bash
# 상태 확인
systemctl --user status llm-wiki

# 재시작
systemctl --user restart llm-wiki

# 실시간 로그
journalctl --user -u llm-wiki -f
```

### CLI 모드

```bash
cd /home/gromit/Music/ComSwiki/llm_wiki

# 파일에서 수집
python3 llm_wiki_agent.py ingest document.txt

# 질문
python3 llm_wiki_agent.py query "RAG와 LLM Wiki의 차이는?"

# 유지보수 검사
python3 llm_wiki_agent.py lint
```

---

## 새 위키 인스턴스 추가

```bash
cd /home/gromit/Music/ComSwiki

python3 new_llm_wiki.py <폴더명> [--port 포트] [--model 모델명] [--wiki-name "표시이름"]
```

**예시:**
```bash
# 보안 전용 위키 (포트 자동 할당)
python3 new_llm_wiki.py security_wiki --wiki-name "보안 LLM Wiki"

# 다른 모델 사용
python3 new_llm_wiki.py research_wiki --model qwen3.6:latest --wiki-name "연구 Wiki"
```

생성되는 것:
- 독립적인 위키 디렉토리 (`raw/`, `config.json`, `AGENTS.md`, `pages.json`)
- `llm_wiki_agent.py`는 심볼릭 링크 (원본 업데이트 시 모든 인스턴스 자동 반영)
- systemd 사용자 서비스 자동 등록 및 시작

---

## config.json 설정

```json
{
  "wiki_name":    "LLM Wiki",
  "web_port":     8001,
  "ollama_url":   "http://localhost:11434",
  "ollama_model": "gemma4:latest",
  "swiki_pages":  "/home/gromit/Music/ComSwiki/swiki/AIC_Wiki/pages",
  "index_page":   300,
  "log_page":     301,
  "page_start":   302
}
```

---

## AGENTS.md (LLM 지침)

각 위키 인스턴스 디렉토리의 `AGENTS.md`가 LLM의 행동 지침(스키마)으로 사용됩니다.  
수집 워크플로우, 페이지 형식 규칙, 품질 기준 등을 커스터마이즈할 수 있습니다.

---

## 웹 UI 기능 요약

| 섹션 | 기능 |
|------|------|
| 📥 문서 수집 | 텍스트 붙여넣기 → 위키 페이지 자동 생성 |
| 🖼️ 이미지 분석 | 이미지 업로드 → 비전 분석 → 위키 페이지 생성 |
| 📄 PDF 분석 | PDF 업로드 → 텍스트 추출 → 위키 페이지 생성 |
| ❓ 질문하기 | 자연어 Q&A, 마크다운 렌더링, 페이지 링크 클릭 가능 |
| 🔧 유지보수 | 위키 품질 검사 (Lint) |
| 📚 페이지 목록 | 현재 위키의 모든 페이지 목록 |

---

## 기술 스택

| 구성요소 | 내용 |
|----------|------|
| LLM | Ollama + gemma4:latest (멀티모달, 비전 지원) |
| 위키 스토리지 | SwikiSwiki XML (Squeak 3.7 / Comanche 7.0.2) |
| 웹 서버 | Python `http.server` + `ThreadingMixIn` |
| PDF 처리 | `pdftotext` (poppler-utils) |
| 의존성 | Python 3.12+ 표준 라이브러리만 사용 (pip 불필요) |

---

## 현재 현황 (2026-06-09 기준)

- **LLM Wiki**: 181개 페이지, 원본 파일 75개 (이미지 6, PDF 2, 텍스트 67)
- **보안 LLM Wiki**: 37개 페이지

---

*Karpathy LLM Wiki pattern — [GitHub Gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)*
