# SwikiSwiki — 역사, 장단점, LLM Wiki와의 궁합

---

## 1. 탄생 배경과 역사

### Squeak (1995~)

**Squeak**은 1995년 애플 컴퓨터의 Alan Kay, Ted Kaehler, Dan Ingalls, John Maloney 등이 시작한 오픈소스 Smalltalk 구현체입니다.  
Alan Kay의 오랜 비전인 **Dynabook** — "어린이도 스스로 프로그래밍할 수 있는 개인용 컴퓨터" — 를 실현하기 위한 플랫폼으로 설계됐습니다.  
Squeak의 핵심 특징은 **이미지 기반 실행 환경**입니다. 프로그램 상태 전체가 하나의 `.image` 파일에 보존되며, VM(가상머신)만 교체하면 어느 OS에서도 동일하게 실행됩니다.

### Swiki (1997~)

**Mark Guzdial** (조지아공과대학교, Georgia Tech)이 1997년 Ward Cunningham의 **WikiWikiWeb** 개념을 Squeak으로 구현했습니다.  
이름 그대로 **Squeak + Wiki = Swiki**입니다.

> "Using Wikis for undergraduate courses was invented at Georgia Tech, starting in 1997 — long before Wikipedia."  
> — Mark Guzdial

처음에는 수업 협업 도구(CoWeb, Collaborative Web)로 출발했으나, 이후 인트라넷 지식 관리 도구로 폭넓게 사용됐습니다.  
Squeak 커뮤니티 공식 위키([wiki.squeak.org](https://wiki.squeak.org))도 Swiki로 운영됩니다.

### Comanche + SwikiSwiki

**Comanche**는 Squeak 이미지 내부에서 직접 실행되는 경량 HTTP 서버입니다. Apache나 nginx 같은 외부 프로세스 없이 Squeak 자체가 웹 서버 역할을 합니다.  
**SwikiSwiki**(ComSwiki)는 Comanche 위에서 돌아가는 Swiki의 완성형으로, 현재 이 프로젝트가 사용하는 버전은 다음과 같습니다:

```
Squeak 3.7  /  Comanche 7.0.2  /  Swiki 1.5
```

2011년 Georgia Tech는 학생 개인정보 보호 이슈로 공개 SwikiSwiki 서비스를 종료했지만, **인트라넷 폐쇄망 환경**에서는 지금도 탁월한 선택입니다.

---

## 2. 기술 구조

```
squeak.image          ← Squeak VM 이미지 (전체 런타임 + 코드 포함)
squeak.exe / squeakvm ← OS별 가상머신
swiki/
├── AIC_Wiki/
│   ├── pages/
│   │   ├── 1.xml     ← 페이지 본문 (XML)
│   │   ├── 1.old     ← 이전 버전 (자동 백업)
│   │   ├── 2.xml
│   │   └── ...
│   ├── uploads/      ← 첨부파일
│   ├── settings.xml  ← 위키 설정
│   ├── security.xml  ← 접근 권한
│   └── setup.xml     ← 스토리지 설정
└── refs/             ← 공통 템플릿/참조
```

### 페이지 XML 형식

```xml
<?xml version="1.0"?>
<page>
  <version date="9/6/2026" time="10:58:18 am" user="10.50.2.85" />
  <settings><s name="referenceCache" type="coll">35 40</s></settings>
  <name>페이지 제목</name>
  <text>본문 내용. 링크는 *다른페이지* 또는 *35* 형식.</text>
</page>
```

### SwikiSwiki 마크업 문법

| 문법 | 결과 |
|------|------|
| `*페이지이름*` | 위키 내부 링크 |
| `*35*` | 번호로 직접 링크 |
| `- 항목` | 목록 |
| `-- 하위항목` | 중첩 목록 |
| `*http://url*` | 외부 URL |
| `<?file src="file.pdf"?>` | 첨부파일 삽입 |
| `<?calendar month=6 year=2026?>` | 달력 삽입 |

---

## 3. 장점

### ✅ 완전 자급자족 (Zero Dependencies)
- 외부 DB, 웹서버, 런타임 없음
- `squeak.image` + `squeakvm` 두 파일만으로 실행
- 방화벽 내부 폐쇄망에서 완벽히 작동

### ✅ XML 파일 기반 스토리지
- 페이지 하나 = XML 파일 하나 (`1.xml`, `2.xml`, ...)
- 사람이 읽고 편집 가능
- Git으로 버전 관리 가능
- Python 표준 라이브러리로 파싱 가능 (`xml.etree.ElementTree`)
- 파일시스템 직접 접근으로 외부 도구 연계 용이

### ✅ 자동 버전 관리
- 페이지 수정 시 이전 버전을 `.old` 파일로 자동 보존
- 별도 버전 관리 시스템 없이 변경 이력 추적 가능

### ✅ 다중 위키 지원
- 하나의 Squeak 이미지에서 여러 위키 동시 운영
- 각 위키별 독립적인 권한 설정

### ✅ 크로스플랫폼
- OS별 VM만 교체하면 Windows/Linux/macOS 동일 작동
- 이미지 파일 자체는 플랫폼 무관

### ✅ 단순하고 안정적인 페이지 번호 체계
- 모든 페이지에 고유한 정수 번호 부여
- URL이 `/AIC_Wiki/302` 처럼 단순하고 영구적
- 번호 기반 상호 참조가 LLM이 다루기 쉬운 형태

### ✅ Smalltalk 라이브 환경
- 서버가 켜진 채로 코드 수정·디버깅 가능 (Squeak IDE 내장)
- 재시작 없이 기능 추가 가능

---

## 4. 단점

### ❌ 단일 이미지 메모리 아키텍처
- 모든 페이지가 시작 시 메모리에 로드
- 페이지 수가 많아질수록 메모리 증가
- 수평 확장(클러스터링) 불가능

### ❌ 스쿼크 이미지 노후화
- 현재 사용 중인 Squeak 3.7은 2003년 기준 버전
- 최신 Squeak(6.x)과 호환되지 않는 코드
- 수정/업그레이드에 Smalltalk 전문 지식 필요

### ❌ 현대적 웹 기능 부재
- REST API 없음 → 외부 도구와 연계 시 파일시스템 직접 접근 필요
- JSON/GraphQL 지원 없음
- 전문(全文) 검색 기능 없음
- 모바일 반응형 UI 없음
- 마크다운 미지원 (고유 문법 사용)

### ❌ 인증/보안 시스템이 단순
- 사용자별 세분화된 권한 관리 어려움
- HTTPS 직접 지원 안 함 (리버스 프록시 필요)
- 세션 관리가 단순

### ❌ 파일 쓰기 잠금(Lock) 없음
- 동시 편집 시 충돌 감지 기능 미흡
- 여러 프로세스가 동시에 XML 파일을 수정하면 데이터 손상 위험

### ❌ 생태계 단절
- 개발이 사실상 중단된 레거시 소프트웨어
- 커뮤니티 지원 희박
- 플러그인/확장 생태계 없음

---

## 5. LLM Wiki와의 궁합 분석

### 왜 SwikiSwiki가 LLM Wiki의 이상적인 백엔드인가

SwikiSwiki의 단점들이 **LLM Wiki가 정확히 보완하는 영역**과 일치합니다.

```
SwikiSwiki의 약점              LLM Wiki의 보완
─────────────────────────────────────────────────────
전문 검색 없음          →  LLM 기반 의미적 Q&A
마크다운 미지원          →  LLM이 SwikiSwiki 문법으로 변환
수동 페이지 생성         →  LLM이 자동 생성·갱신
연결 관계 수동 관리      →  LLM이 교차 참조 자동 추가
REST API 없음           →  XML 파일 직접 읽기·쓰기로 우회
```

### XML 파일 직접 접근의 이점

SwikiSwiki가 API를 제공하지 않아도, LLM Wiki는 파일시스템을 직접 읽고 씁니다:

```python
# XML 파싱
root = ET.parse("302.xml").getroot()
text = root.findtext("text")

# XML 쓰기 (새 페이지 생성)
path.write_text('<?xml version="1.0"?><page>...<text>내용</text></page>')
```

이는 **DB나 API가 없어도 완전한 읽기/쓰기 통합**이 가능함을 의미합니다.

### 번호 기반 페이지 체계와 LLM

`*302*`, `[페이지 302]` 같은 숫자 참조 방식은 LLM이 출처를 명시하기에 이상적입니다:

- 모호성 없음: 번호는 유일한 식별자
- LLM 프롬프트에 "페이지 번호로 인용하라" 지시 가능
- 브라우저 하이퍼링크로 원클릭 이동 (`http://host:8000/AIC_Wiki/302`)

### pages.json 레지스트리 패턴

SwikiSwiki는 파일 잠금 없이 XML을 공유하므로, LLM Wiki는 `pages.json`으로 자신이 소유한 페이지를 추적합니다. 이로써 **여러 LLM Wiki 인스턴스가 동일한 SwikiSwiki를 공유**해도 충돌 없이 동작합니다:

```
SwikiSwiki AIC_Wiki/pages/
├── 1~299    : 인간이 직접 작성한 페이지
├── 300~454  : LLM Wiki 인스턴스 #1 소유 (pages.json으로 추적)
├── 600~636  : LLM Wiki 인스턴스 #2 소유 (pages.json으로 추적)
└── ...
```

### 궁합 점수 요약

| 항목 | 점수 | 비고 |
|------|:----:|------|
| 스토리지 접근성 | ⭐⭐⭐⭐⭐ | XML 파일 직접 읽기·쓰기 |
| 페이지 번호 체계 | ⭐⭐⭐⭐⭐ | LLM 인용에 최적 |
| 검색 기능 보완 | ⭐⭐⭐⭐⭐ | LLM Q&A가 완전 대체 |
| 폐쇄망 독립성 | ⭐⭐⭐⭐⭐ | 외부망 없이 완전 작동 |
| 동시 편집 안전성 | ⭐⭐⭐ | 잠금 없음, 주의 필요 |
| 확장성 | ⭐⭐ | 단일 이미지 한계 |
| 현대화 가능성 | ⭐⭐ | 레거시 코드베이스 |

**결론:** SwikiSwiki는 API도 검색도 없는 "수동 위키"의 한계를 갖고 있지만, 그 단순한 XML 파일 구조가 역설적으로 LLM 에이전트와 최고의 궁합을 만들어냅니다. LLM이 파일을 직접 읽고 쓰면서 지식을 자동으로 축적하는 Karpathy LLM Wiki 패턴의 이상적인 저장소입니다.

---

## 6. 참고 자료

- [Swiki - Wikipedia](https://en.wikipedia.org/wiki/Swiki)
- [Mark Guzdial - Wikipedia](https://en.wikipedia.org/wiki/Mark_Guzdial)
- [Squeak 공식 사이트](https://squeak.org/)
- [Squeak Swiki (커뮤니티 위키)](https://wiki.squeak.org/squeak/)
- [No More Swikis at Georgia Tech (2011)](https://computinged.wordpress.com/2011/11/15/no-more-swikis-end-of-the-constructionist-web-at-georgia-tech/)
- [CoWeb: Collaborative Web Spaces (논문)](https://www.researchgate.net/publication/230877238_CoWeb-experiences_with-collaborative_Web_spaces)
- [Karpathy LLM Wiki Pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
