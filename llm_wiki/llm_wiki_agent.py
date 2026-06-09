#!/usr/bin/env python3
"""
LLM Wiki Agent for SwikiSwiki
Karpathy LLM Wiki 패턴 (2026년 4월) 구현
Ollama gemma4:latest + SwikiSwiki XML 스토리지
웹 UI: http://localhost:8001
"""

import json
import re
import xml.etree.ElementTree as ET
import xml.sax.saxutils as saxutils
from pathlib import Path
from datetime import datetime
import urllib.request
import urllib.parse
import http.server
import socketserver
import sys
import html as html_module
import shutil
import os
import base64
import subprocess

# ── 설정 (config.json 우선, 없으면 기본값) ───────────────
def _load_config():
    # 실행 위치의 config.json 읽기
    cfg_path = Path(sys.argv[0]).parent / "config.json"
    cfg = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return cfg

_CFG = _load_config()

OLLAMA_URL   = _CFG.get("ollama_url",   "http://localhost:11434")
OLLAMA_MODEL = _CFG.get("ollama_model", "gemma4:latest")
SWIKI_PAGES  = Path(_CFG.get("swiki_pages", "/home/gromit/Music/ComSwiki/swiki/AIC_Wiki/pages"))
LLM_WIKI_DIR = Path(sys.argv[0]).parent
RAW_DIR      = LLM_WIKI_DIR / "raw"
WEB_PORT     = _CFG.get("web_port",    8001)
WIKI_NAME    = _CFG.get("wiki_name",   "LLM Wiki")

INDEX_PAGE = _CFG.get("index_page", 300)
LOG_PAGE   = _CFG.get("log_page",   301)
PAGE_START = _CFG.get("page_start", 302)


# ── Ollama 클라이언트 ─────────────────────────────────
class OllamaClient:
    def __init__(self):
        self.url   = OLLAMA_URL
        self.model = OLLAMA_MODEL

    def generate(self, prompt, timeout=300, images=None):
        body = {
            "model":  self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_ctx": 8192},
        }
        if images:
            body["images"] = images   # base64 문자열 리스트
        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self.url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read()).get("response", "")
        except Exception as e:
            return f"[Ollama 오류: {e}]"


# ── SwikiSwiki 페이지 관리 ────────────────────────────
class SwikiPageManager:
    """
    각 위키 인스턴스가 pages.json으로 자신의 페이지 번호를 관리.
    - 페이지 수 무제한
    - 다른 위키와 번호 충돌 없음 (shared 디렉토리에서 빈 번호를 찾아 사용)
    """

    def __init__(self):
        self.dir           = SWIKI_PAGES
        self._reg_path     = LLM_WIKI_DIR / "pages.json"
        self._registry     = self._load_registry()

    # ── 레지스트리 ──────────────────────────────────────
    def _load_registry(self):
        if self._reg_path.exists():
            try:
                return set(json.loads(self._reg_path.read_text(encoding="utf-8")))
            except Exception:
                pass
        # 최초 실행 또는 파일 없음: INDEX_PAGE 범위를 스캔해 기존 페이지 수집
        reg = set()
        missing = 0
        for n in range(INDEX_PAGE, INDEX_PAGE + 2000):
            if (self.dir / f"{n}.xml").exists():
                reg.add(n)
                missing = 0
            else:
                missing += 1
                if missing > 50:   # 연속 50개 없으면 종료
                    break
        self._reg_path.write_text(json.dumps(sorted(reg)), encoding="utf-8")
        return reg

    def _save(self):
        self._reg_path.write_text(
            json.dumps(sorted(self._registry)), encoding="utf-8"
        )

    # ── 다음 빈 번호 (전체 디렉토리 기준으로 충돌 방지) ────
    def next_num(self):
        used = set()
        for f in self.dir.glob("*.xml"):
            try:
                used.add(int(f.stem))
            except ValueError:
                pass
        n = PAGE_START
        while n in used:
            n += 1
        return n

    # ── XML 생성 ────────────────────────────────────────
    def _xml(self, name, text):
        now  = datetime.now()
        date = now.strftime("%-d/%-m/%Y")
        time = now.strftime("%-I:%M:%S %p").lower()
        return (f'<?xml version="1.0"?><page>'
                f'<version date="{date}" time="{time}" user="llm-wiki" />'
                f'<settings><s name="referenceCache" type="coll"></s></settings>'
                f'<name>{saxutils.escape(name)}</name>'
                f'<text>{saxutils.escape(text)}</text>'
                f'</page>')

    # ── 읽기 / 쓰기 ─────────────────────────────────────
    def write(self, num, name, text):
        path = self.dir / f"{num}.xml"
        old  = self.dir / f"{num}.old"
        if path.exists():
            shutil.copy2(path, old)
        path.write_text(self._xml(name, text), encoding="utf-8")
        self._registry.add(num)
        self._save()

    def read(self, num):
        path = self.dir / f"{num}.xml"
        if not path.exists():
            return None
        try:
            root = ET.parse(path).getroot()
            return {
                "number": num,
                "name":   root.findtext("name") or "",
                "text":   root.findtext("text") or "",
            }
        except ET.ParseError:
            return None

    # ── 목록 / 검색 ─────────────────────────────────────
    def all_llm_pages(self):
        return [p for n in sorted(self._registry) if (p := self.read(n))]

    def find_by_name(self, name):
        nl = name.strip().lower()
        for n in sorted(self._registry):
            p = self.read(n)
            if p and p["name"].strip().lower() == nl:
                return p
        return None


# ── LLM Wiki 에이전트 ─────────────────────────────────
class LLMWikiAgent:
    def __init__(self):
        self.ollama = OllamaClient()
        self.pages  = SwikiPageManager()
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        self._bootstrap()

    def _bootstrap(self):
        if not self.pages.read(INDEX_PAGE):
            self.pages.write(INDEX_PAGE, "LLM Wiki 인덱스",
                f"LLM Wiki - 컴파운딩 지식 베이스\n\n"
                f"Karpathy LLM Wiki 패턴 (2026년 4월) 구현\n"
                f"Ollama {OLLAMA_MODEL} 사용\n\n"
                f"페이지 목록:\n"
                f"- *{LOG_PAGE}* : 작업 로그\n")
        if not self.pages.read(LOG_PAGE):
            self.pages.write(LOG_PAGE, "LLM Wiki 로그",
                f"## {datetime.now().strftime('%Y-%m-%d')} | 시스템 초기화\n"
                f"LLM Wiki 에이전트 설치 완료.\n")

    def _agents_md(self):
        p = LLM_WIKI_DIR / "AGENTS.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def _index_text(self):
        p = self.pages.read(INDEX_PAGE)
        return p["text"] if p else ""

    def _log(self, entry):
        p = self.pages.read(LOG_PAGE)
        existing = p["text"] if p else ""
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.pages.write(LOG_PAGE, "LLM Wiki 로그",
                         f"## {stamp} | {entry}\n\n" + existing)

    def _append_index(self, new_pages):
        text = self._index_text()
        additions = "\n".join(f"- *{p['number']}* : {p['name']}" for p in new_pages)
        self.pages.write(INDEX_PAGE, "LLM Wiki 인덱스", text + "\n" + additions)

    # ── Ingest ──────────────────────────────────────
    def ingest(self, document, source_name="Unknown"):
        raw_file = RAW_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{source_name[:40]}.txt"
        raw_file.write_text(document, encoding="utf-8")

        existing_summary = "\n".join(
            f"[{p['number']}] {p['name']}: {p['text'][:150]}"
            for p in self.pages.all_llm_pages()[:15]
        )
        agents_md = self._agents_md()

        prompt = f"""{agents_md}

당신은 LLM Wiki 에이전트입니다. 아래 문서를 분석하여 SwikiSwiki 위키 페이지를 생성하세요.

현재 위키 인덱스:
{self._index_text()[:1000]}

기존 페이지 요약:
{existing_summary}

새 소스 문서 ({source_name}):
{document[:3500]}

아래 JSON 형식으로만 응답하세요. 다른 텍스트 없이 JSON만:
{{
  "summary": "문서 핵심 내용 1-2문장",
  "pages": [
    {{
      "name": "페이지 이름",
      "action": "create",
      "content": "SwikiSwiki 형식 내용 (마크다운 # 헤딩 금지, 목록은 - 사용, 링크는 *페이지이름*)"
    }}
  ]
}}

규칙:
- 핵심 엔티티(사람/조직/시스템/제품)마다 페이지 1개
- 핵심 개념마다 페이지 1개
- 페이지당 내용 200~500자
- 반드시 유효한 JSON만 출력"""

        response = self.ollama.generate(prompt)

        created = []
        try:
            m = re.search(r'\{[\s\S]*\}', response)
            if m:
                data = json.loads(m.group())
                for pd in data.get("pages", []):
                    name    = pd.get("name", "Untitled").strip()
                    content = pd.get("content", "").strip()
                    if not name or not content:
                        continue
                    existing = self.pages.find_by_name(name)
                    if existing:
                        num = existing["number"]
                        self.pages.write(num, name, content)
                        created.append({"number": num, "name": name, "action": "updated"})
                    else:
                        num = self.pages.next_num()
                        self.pages.write(num, name, content)
                        created.append({"number": num, "name": name, "action": "created"})

                summary = data.get("summary", "")
                self._log(f"수집: {source_name} — {summary}")
                new_ones = [p for p in created if p["action"] == "created"]
                if new_ones:
                    self._append_index(new_ones)
                return {"success": True, "summary": summary, "pages": created, "raw": ""}
        except (json.JSONDecodeError, AttributeError, KeyError):
            pass

        # 폴백: 원문 그대로 단일 페이지 생성
        num = self.pages.next_num()
        self.pages.write(num, f"수집: {source_name}", response[:2000])
        self._log(f"수집(폴백): {source_name}")
        return {"success": False,
                "error": "JSON 파싱 실패 — 원문 페이지 생성됨",
                "pages": [{"number": num, "name": f"수집: {source_name}", "action": "created"}],
                "raw": response[:600]}

    # ── Query ───────────────────────────────────────
    def query(self, question):
        all_pages = self.pages.all_llm_pages()

        wiki_ctx = "\n\n".join(
            f"=== [{p['number']}] {p['name']} ===\n{p['text'][:500]}"
            for p in all_pages
        )

        # 질문 키워드로 raw 원본 문서도 검색
        keywords = [w.lower() for w in re.split(r'\s+', question) if len(w) > 1]
        raw_ctx  = self._raw_context(keywords, max_chars=3000)

        context_parts = []
        if wiki_ctx:
            context_parts.append(f"## 위키 페이지\n{wiki_ctx[:5500]}")
        if raw_ctx:
            context_parts.append(f"## 원본 문서 (위키 미생성 내용 포함)\n{raw_ctx}")

        if not context_parts:
            return "위키가 비어 있습니다. 먼저 문서를 수집하세요."

        prompt = f"""당신은 위키 지식 베이스와 원본 문서를 기반으로 질문에 답하는 에이전트입니다.

{chr(10).join(context_parts)}

질문: {question}

위의 내용을 바탕으로 구체적으로 답변하세요.
- 위키 페이지 참조: [페이지 302] 형식
- 일반 원본 문서 참조: [원본: 파일명] 형식
- **이미지 원본 참조: [원본: 파일명.png] 형식으로 반드시 명시** (컨텍스트에 [원본 이미지: xxx.png] 가 있으면 반드시 [원본: xxx.png] 로 인용)
- **마크다운 형식** 사용: 헤딩(## ###), 굵게(**텍스트**), 목록(- 항목)
- 한국어로 답변하세요"""

        return self.ollama.generate(prompt)

    # ── PDF 추출 ────────────────────────────────────
    def extract_pdf(self, pdf_bytes, source_name="document.pdf"):
        """PDF bytes → 텍스트 추출 후 ingest. (pdftotext 사용)"""
        pdf_path = RAW_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{Path(source_name).stem[:40]}.pdf"
        pdf_path.write_bytes(pdf_bytes)

        # 페이지 수 확인
        info = subprocess.run(
            ["pdfinfo", str(pdf_path)],
            capture_output=True, text=True, timeout=30,
        )
        pages_total = 0
        for line in info.stdout.splitlines():
            if line.lower().startswith("pages:"):
                try:
                    pages_total = int(line.split(":")[-1].strip())
                except ValueError:
                    pass

        # 텍스트 추출 (최대 60페이지)
        cmd = ["pdftotext", "-layout", "-enc", "UTF-8"]
        if pages_total > 60:
            cmd += ["-l", "60"]
        cmd += [str(pdf_path), "-"]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        text = result.stdout.strip()

        if not text:
            return {
                "success": False,
                "error": "텍스트를 추출할 수 없습니다. 스캔 이미지 PDF이거나 보안 잠금된 파일일 수 있습니다.",
                "pages": [],
                "pdf_info": {"total_pages": pages_total, "extracted_chars": 0},
            }

        extracted_chars = len(text)
        ingest_result = self._ingest_chunked(text, source_name)
        ingest_result["pdf_info"] = {
            "total_pages":     pages_total,
            "extracted_chars": extracted_chars,
            "chunks":          ingest_result.pop("chunks", 1),
            "pdf_path":        str(pdf_path),
        }
        return ingest_result

    def _ingest_chunked(self, document, source_name, chunk_size=8000, overlap=400):
        """긴 문서를 청크로 나눠 순차 수집. 모든 내용이 위키 페이지로 변환됨."""
        if len(document) <= chunk_size:
            r = self.ingest(document, source_name)
            r["chunks"] = 1
            return r

        # 문단 경계에서 자르기
        chunks, pos = [], 0
        while pos < len(document):
            end = min(pos + chunk_size, len(document))
            if end < len(document):
                for sep in ["\n\n", "\n", "。", ". ", " "]:
                    b = document.rfind(sep, pos + chunk_size // 2, end)
                    if b != -1:
                        end = b + len(sep)
                        break
            chunks.append(document[pos:end])
            pos = end - overlap if end < len(document) else len(document)

        all_pages, summaries = [], []
        for i, chunk in enumerate(chunks, 1):
            r = self.ingest(chunk, f"{source_name} ({i}/{len(chunks)})")
            all_pages.extend(r.get("pages", []))
            if r.get("summary"):
                summaries.append(r["summary"])

        return {
            "success":  True,
            "summary":  summaries[0] if summaries else "",
            "pages":    all_pages,
            "chunks":   len(chunks),
            "raw":      "",
        }

    # ── 이미지 분석 ──────────────────────────────────────
    SUPPORTED_IMG = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

    def analyze_image(self, img_bytes, source_name="image.png"):
        ext = Path(source_name).suffix.lower()
        if ext not in self.SUPPORTED_IMG:
            return {"success": False,
                    "error": f"지원하지 않는 형식: {ext}. 지원: {', '.join(self.SUPPORTED_IMG)}",
                    "pages": []}

        # 원본 저장
        img_path = RAW_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{Path(source_name).stem[:40]}{ext}"
        img_path.write_bytes(img_bytes)

        b64 = base64.b64encode(img_bytes).decode()

        # 1단계: 이미지 상세 분석 (비전)
        vision_prompt = (
            "이 이미지를 한국어로 상세히 분석하세요.\n\n"
            "다음 항목을 포함해 구체적으로 서술하세요:\n"
            "1. 이미지 전체 개요 및 목적\n"
            "2. 보이는 요소들의 상세 묘사\n"
            "3. 수치, 라벨, 텍스트 등 정보 추출\n"
            "4. 이 이미지가 나타내는 핵심 개념이나 결론\n\n"
            "가능한 한 풍부하고 구체적으로 작성하세요."
        )
        description = self.ollama.generate(vision_prompt, images=[b64], timeout=180)

        if description.startswith("[Ollama 오류"):
            return {"success": False, "error": description, "pages": []}

        # 설명을 raw txt로 저장
        txt_path = img_path.with_suffix(".txt")
        txt_path.write_text(f"[이미지 분석: {source_name}]\n\n{description}", encoding="utf-8")

        # 2단계: 위키 페이지 생성 (분석 결과를 일반 ingest로)
        result = self.ingest(description, source_name)
        result["image_info"] = {
            "source":      source_name,
            "img_path":    str(img_path),
            "description": description[:400],
        }
        return result

    def _snippet(self, text, keywords, width=220):
        tl = text.lower()
        pos = next((tl.find(kw) for kw in keywords if tl.find(kw) != -1), 0)
        start = max(0, pos - 80)
        end   = min(len(text), start + width)
        return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")

    # 한국어 의문사/조사 — 검색 키워드에서 제외
    _STOP = frozenset(["무엇", "어떻게", "왜", "언제", "어디서", "어디", "누가",
                        "어떤", "몇", "얼마나", "이란", "인가", "인가요", "습니까",
                        "인지", "하는", "하나요", "알려줘", "설명해", "뭔가"])

    def _content_keywords(self, keywords):
        """의문사·조사를 제거한 실질 키워드만 반환."""
        result = []
        for kw in keywords:
            if not any(sw in kw for sw in self._STOP):
                result.append(kw)
        return result or keywords   # 전부 걸러지면 원본 사용

    def _expand_keywords(self, keywords):
        """한국어 조사/어미 제거를 위해 prefix도 검색어에 포함."""
        terms = set()
        for kw in keywords:
            terms.add(kw)
            for n in (5, 4, 3):        # 최소 3자 prefix
                if len(kw) > n:
                    terms.add(kw[:n])
        return [t for t in terms if len(t) >= 3]

    def _raw_context(self, keywords, max_chars=3000):
        """질문 관련 raw 파일 스니펫을 컨텍스트로 반환. 스코어 높은 순 선택."""
        ckw   = self._content_keywords(keywords)
        terms = self._expand_keywords(ckw)
        if not terms:
            return ""

        _img_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
        scored = []
        for raw_file in sorted(RAW_DIR.glob("*.txt"), reverse=True):
            try:
                raw_text = raw_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            text_l = raw_text.lower()
            score  = sum(text_l.count(t) for t in terms)
            if score == 0:
                continue
            m   = re.match(r'^\d{8}_\d{6}_(.+)', raw_file.stem)
            src = (m.group(1) if m else raw_file.stem).replace("_", " ")
            # 동일 타임스탬프 이미지 파일이 있는지 확인
            img_name = None
            for ext in _img_exts:
                candidate = raw_file.with_suffix(ext)
                if candidate.exists():
                    img_name = candidate.name   # 20260609_vangle.png
                    break
            scored.append((score, src, raw_text, img_name))

        scored.sort(key=lambda x: -x[0])
        parts, used = [], 0
        for score, src, raw_text, img_name in scored:
            if used >= max_chars:
                break
            snippet = self._snippet(raw_text, terms, width=800)
            # 이미지 원본이면 파일명 명시
            header = f"[원본 이미지: {img_name}]" if img_name else f"[원본: {src}]"
            part   = f"{header}\n{snippet}"
            parts.append(part)
            used += len(part)
        return "\n\n".join(parts)

    # ── Lint ────────────────────────────────────────
    def lint(self):
        all_pages = self.pages.all_llm_pages()
        if not all_pages:
            return "위키가 비어 있습니다."

        pages_text = "\n\n".join(
            f"[{p['number']}] {p['name']}:\n{p['text'][:300]}"
            for p in all_pages
        )

        prompt = f"""위키 유지보수 검사를 수행하세요.

위키 페이지들:
{pages_text[:5500]}

다음을 확인하고 **마크다운 형식**으로 한국어 보고서를 작성하세요:
1. 모순되거나 충돌하는 정보
2. 누락된 교차 참조 (연결되어야 하는데 링크가 없는 페이지)
3. 업데이트가 필요해 보이는 페이지
4. 고아 페이지 (다른 페이지에서 링크되지 않는 페이지)
5. 내용 보강이 필요한 미완성 페이지

헤딩(## ###), 목록(- 항목), 굵게(**텍스트**) 형식을 사용하세요."""

        result = self.ollama.generate(prompt)
        self._log("Lint 실행")
        return result


# ── 웹 핸들러 ─────────────────────────────────────────
class WikiHandler(http.server.BaseHTTPRequestHandler):
    agent: LLMWikiAgent = None

    def log_message(self, fmt, *args):
        pass

    def _swiki_base(self):
        """요청 Host 헤더에서 서버 IP를 추출해 SwikiSwiki URL 생성.
        config.json에 swiki_base_url 이 있으면 그것을 우선 사용."""
        override = _CFG.get("swiki_base_url", "")
        if override:
            return override.rstrip("/")
        host = self.headers.get("Host", "localhost")
        hostname = host.split(":")[0]   # 포트 제거, IP만
        swiki_port = _CFG.get("swiki_port", 8000)
        return f"http://{hostname}:{swiki_port}/AIC_Wiki"

    def _send_html(self, body, status=200):
        b = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _send_json(self, data, status=200):
        b = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _read_form(self):
        n = int(self.headers.get("Content-Length", 0))
        return dict(urllib.parse.parse_qsl(self.rfile.read(n).decode("utf-8")))

    def do_GET(self):
        if self.path in ("/", "/index"):
            self._send_html(self._page_main())
        elif self.path.startswith("/raw-image/"):
            self._serve_raw_image(urllib.parse.unquote(self.path[len("/raw-image/"):]))
        else:
            self._send_html("<h1>404</h1>", 404)

    _IMG_MIME = {".jpg":"image/jpeg",".jpeg":"image/jpeg",".png":"image/png",
                 ".gif":"image/gif",".webp":"image/webp",".bmp":"image/bmp"}

    def _serve_raw_image(self, name):
        """소스 이름(확장자 있/없)으로 raw/ 이미지 파일을 탐색해 서빙."""
        name = Path(name).name          # 경로 탐색 공격 방지
        name_lower = name.lower()
        found = None
        for f in RAW_DIR.iterdir():
            if f.suffix.lower() not in self._IMG_MIME:
                continue
            # 1) 파일명 완전 일치
            if f.name.lower() == name_lower:
                found = f; break
            # 2) 타임스탬프 제거 후 일치 (20260609_120000_vangle.png → vangle.png)
            m = re.match(r'^\d{8}_\d{6}_(.+)', f.stem)
            src_stem = m.group(1) if m else f.stem
            src_name = (src_stem + f.suffix).lower()
            if src_name == name_lower or src_stem.lower() == name_lower:
                found = f; break
        if not found:
            self._send_html("<h1>404</h1>", 404)
            return
        data = found.read_bytes()
        mime = self._IMG_MIME.get(found.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        form = self._read_form()
        if self.path == "/ingest":
            text   = form.get("text", "").strip()
            source = form.get("source", "Unknown").strip() or "Unknown"
            if not text:
                self._send_json({"error": "텍스트 없음"}, 400)
                return
            self._send_json(self.agent.ingest(text, source))

        elif self.path == "/query":
            q = form.get("question", "").strip()
            if not q:
                self._send_json({"error": "질문 없음"}, 400)
                return
            self._send_json({"answer": self.agent.query(q)})

        elif self.path == "/lint":
            self._send_json({"report": self.agent.lint()})

        elif self.path == "/upload-image":
            b64    = form.get("b64", "").strip()
            source = form.get("source", "image.png").strip() or "image.png"
            if not b64:
                self._send_json({"error": "이미지 데이터 없음"}, 400)
                return
            # data URL 프리픽스 제거 (브라우저 FileReader 결과)
            if "," in b64:
                b64 = b64.split(",", 1)[1]
            try:
                img_bytes = base64.b64decode(b64)
            except Exception as e:
                self._send_json({"error": f"base64 디코딩 실패: {e}"}, 400)
                return
            self._send_json(self.agent.analyze_image(img_bytes, source))

        elif self.path == "/upload-pdf":
            b64    = form.get("b64", "").strip()
            source = form.get("source", "document.pdf").strip() or "document.pdf"
            if not b64:
                self._send_json({"error": "PDF 데이터 없음"}, 400)
                return
            try:
                pdf_bytes = base64.b64decode(b64)
            except Exception as e:
                self._send_json({"error": f"base64 디코딩 실패: {e}"}, 400)
                return
            self._send_json(self.agent.extract_pdf(pdf_bytes, source))

        else:
            self._send_html("<h1>404</h1>", 404)

    def _page_main(self):
        swiki_base = self._swiki_base()
        pages = self.agent.pages.all_llm_pages()
        rows = "".join(
            f'<tr>'
            f'<td><a href="{swiki_base}/{p["number"]}" target="_blank">{p["number"]}</a></td>'
            f'<td>{html_module.escape(p["name"])}</td>'
            f'<td style="color:#555">{html_module.escape(p["text"][:120])}…</td>'
            f'</tr>'
            for p in pages
        ) or '<tr><td colspan="3" style="color:#888">아직 페이지 없음. 문서를 수집하세요.</td></tr>'

        return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{WIKI_NAME}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',sans-serif;background:#f0f2f5;color:#333;padding:20px}}
h1{{font-size:1.6em;margin-bottom:4px}}
.sub{{color:#666;font-size:.85em;margin-bottom:20px}}
.card{{background:#fff;border-radius:10px;padding:22px;margin-bottom:18px;box-shadow:0 1px 4px rgba(0,0,0,.1)}}
h2{{font-size:1.1em;margin-bottom:12px;color:#444}}
textarea{{width:100%;height:140px;font-family:monospace;font-size:13px;border:1px solid #ddd;border-radius:6px;padding:10px;resize:vertical}}
input[type=text]{{width:100%;padding:9px 12px;font-size:14px;border:1px solid #ddd;border-radius:6px;margin-bottom:8px}}
.btn{{display:inline-block;padding:9px 20px;border:none;border-radius:6px;cursor:pointer;font-size:14px;font-weight:500}}
.btn-blue{{background:#3b82f6;color:#fff}}.btn-blue:hover{{background:#2563eb}}
.btn-green{{background:#10b981;color:#fff}}.btn-green:hover{{background:#059669}}
.btn-orange{{background:#f59e0b;color:#fff}}.btn-orange:hover{{background:#d97706}}
.spinner{{display:none;color:#888;font-size:13px;margin-left:10px}}
.result{{display:none;margin-top:14px;background:#f8f9fa;border-left:4px solid #3b82f6;padding:16px 20px;border-radius:0 6px 6px 0;font-size:13.5px;line-height:1.7}}
.result.green{{border-color:#10b981}}.result.red{{border-color:#ef4444}}
.result h1,.result h2,.result h3{{font-weight:600;color:#1e3a5f;margin:14px 0 6px}}
.result h1{{font-size:1.25em}}.result h2{{font-size:1.1em;border-bottom:1px solid #dde}}.result h3{{font-size:1em}}
.result p{{margin:6px 0}}
.result ul,.result ol{{margin:6px 0 6px 22px}}
.result li{{margin:3px 0}}
.result code{{background:#e8f0fe;color:#1d4ed8;padding:1px 6px;border-radius:3px;font-family:monospace;font-size:.9em}}
.result pre{{background:#1e2d3d;color:#e8f4f8;padding:14px 16px;border-radius:6px;overflow-x:auto;margin:10px 0;font-size:12.5px}}
.result pre code{{background:none;color:inherit;padding:0}}
.result hr{{border:none;border-top:1px solid #dde;margin:12px 0}}
.result strong{{font-weight:600;color:#111}}
.result em{{font-style:italic;color:#444}}
.result a{{color:#3b82f6}}
.wiki-link{{display:inline-flex;align-items:center;gap:3px;background:#dbeafe;color:#1d4ed8;padding:1px 8px;border-radius:10px;font-size:.85em;font-weight:600;text-decoration:none;border:1px solid #bfdbfe}}
.wiki-link:hover{{background:#bfdbfe;text-decoration:none}}
.raw-ref{{display:inline-flex;align-items:center;gap:3px;background:#fef3c7;color:#92400e;padding:1px 8px;border-radius:10px;font-size:.85em;font-weight:500}}
.inline-img-wrap{{margin:10px 0;display:block}}
.inline-img{{max-width:100%;max-height:260px;border-radius:8px;border:1px solid #ddd;cursor:zoom-in;transition:max-height .25s ease;display:block}}
.inline-img.expanded{{max-height:none;cursor:zoom-out}}
.inline-img-caption{{font-size:11.5px;color:#888;margin-top:3px}}
.result blockquote{{border-left:3px solid #cbd5e1;margin:8px 0;padding:4px 12px;color:#555;background:#f1f5f9;border-radius:0 4px 4px 0}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{background:#3b82f6;color:#fff;padding:9px 10px;text-align:left;font-weight:500}}
td{{padding:7px 10px;border-bottom:1px solid #eee;vertical-align:top}}
tr:last-child td{{border-bottom:none}}
a{{color:#3b82f6;text-decoration:none}}a:hover{{text-decoration:underline}}
.tag{{background:#dbeafe;color:#1d4ed8;padding:1px 7px;border-radius:10px;font-size:11px}}
</style>
</head>
<body>
<h1>🧠 {WIKI_NAME}</h1>
<p class="sub">Karpathy LLM Wiki 패턴 · Ollama <strong>{OLLAMA_MODEL}</strong> · SwikiSwiki 연동</p>

<div class="card">
  <h2>📥 문서 수집 (Ingest)</h2>
  <p style="font-size:13px;color:#555;margin-bottom:10px">소스 문서를 붙여넣으면 LLM이 분석하여 위키 페이지를 자동 생성합니다.</p>
  <input type="text" id="src" placeholder="소스 이름 (예: 프로젝트보고서, 논문제목)">
  <textarea id="doc" placeholder="문서 내용을 여기에 붙여넣으세요..."></textarea>
  <button class="btn btn-blue" onclick="doIngest()">📊 수집 및 위키 생성</button>
  <span class="spinner" id="s-ingest">⏳ gemma4가 분석 중... (수십 초 소요)</span>
  <div class="result" id="r-ingest"></div>
</div>

<div class="card">
  <h2>🖼️ 이미지 분석 및 수집</h2>
  <p style="font-size:13px;color:#555;margin-bottom:10px">이미지를 업로드하면 gemma4가 내용을 분석하여 위키 페이지를 생성합니다. (JPG·PNG·GIF·WebP·BMP)</p>
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
    <input type="file" id="img-file" accept="image/*" style="flex:1;padding:7px;border:1px solid #ddd;border-radius:6px;font-size:13px;background:#fff" onchange="onImgSelect(this)">
    <input type="text" id="img-src" placeholder="소스 이름 (자동 입력)" style="flex:1;margin:0;min-width:160px">
  </div>
  <div id="img-preview-wrap" style="display:none;margin-top:8px">
    <img id="img-preview" style="max-height:160px;max-width:100%;border-radius:6px;border:1px solid #ddd">
  </div>
  <div style="margin-top:8px">
    <button class="btn btn-blue" onclick="doUploadImage()">🔍 이미지 분석 및 위키 생성</button>
    <span class="spinner" id="s-img">⏳ gemma4 비전 분석 중...</span>
  </div>
  <div class="result" id="r-img"></div>
</div>

<div class="card">
  <h2>📄 PDF 분석 및 수집</h2>
  <p style="font-size:13px;color:#555;margin-bottom:10px">PDF 파일을 업로드하면 텍스트를 추출하여 위키에 자동 수집합니다. (최대 60페이지)</p>
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
    <input type="file" id="pdf-file" accept=".pdf" style="flex:1;padding:7px;border:1px solid #ddd;border-radius:6px;font-size:13px;background:#fff" onchange="document.getElementById('pdf-src').value=this.files[0]?.name||''">
    <input type="text" id="pdf-src" placeholder="소스 이름 (자동 입력)" style="flex:1;margin:0;min-width:160px">
  </div>
  <div style="margin-top:8px">
    <button class="btn btn-blue" onclick="doUploadPDF()">📊 PDF 분석 및 위키 생성</button>
    <span class="spinner" id="s-pdf">⏳ PDF 추출 및 gemma4 분석 중...</span>
  </div>
  <div class="result" id="r-pdf"></div>
</div>

<div class="card">
  <h2>❓ 질문하기 (Query)</h2>
  <p style="font-size:13px;color:#555;margin-bottom:10px">자연어로 질문하면 LLM이 위키를 종합해 답변합니다. (수십 초 소요)</p>
  <input type="text" id="q" placeholder="위키 지식 베이스에 질문하세요..." onkeydown="if(event.key==='Enter')doQuery()">
  <button class="btn btn-green" onclick="doQuery()">🔍 질문</button>
  <span class="spinner" id="s-query">⏳ 답변 생성 중...</span>
  <div class="result green" id="r-query"></div>
</div>

<div class="card">
  <h2>🔧 위키 유지보수 (Lint)</h2>
  <p style="font-size:13px;color:#555;margin-bottom:10px">모순·누락 링크·오래된 정보 검사</p>
  <button class="btn btn-orange" onclick="doLint()">🔍 Lint 실행</button>
  <span class="spinner" id="s-lint">⏳ 검사 중...</span>
  <div class="result" id="r-lint"></div>
</div>

<div class="card">
  <h2>📚 위키 페이지 <span class="tag">{len(pages)}개</span></h2>
  <table>
    <tr><th>번호</th><th>페이지 이름</th><th>내용 미리보기</th></tr>
    {rows}
  </table>
  <p style="margin-top:12px;font-size:12px;color:#888">
    SwikiSwiki에서 보기:
    <a href="{swiki_base}/{INDEX_PAGE}" target="_blank">LLM Wiki 인덱스 ({INDEX_PAGE})</a> ·
    <a href="{swiki_base}/{LOG_PAGE}" target="_blank">로그 ({LOG_PAGE})</a>
  </p>
</div>

<script>
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

/* ── 경량 Markdown 렌더러 ── */
/* ── Wiki 참조 → 하이퍼링크 변환 ── */
function linkifyWikiRefs(html) {{
  const base = '{swiki_base}/';
  // [페이지 302] 또는 [페이지302]
  html = html.replace(/\\[페이지\\s*(\\d+)\\]/g,
    (_, n) => `<a href="${{base}}${{n}}" target="_blank" class="wiki-link">📄 페이지 ${{n}}</a>`);
  // *302* SwikiSwiki 형식
  html = html.replace(/[*](\\d+)[*]/g,
    (_, n) => `<a href="${{base}}${{n}}" target="_blank" class="wiki-link">📄 ${{n}}</a>`);
  // [원본 이미지: ...] 또는 [원본: ...] — 이미지면 인라인 표시
  const imgExts = new Set(['jpg','jpeg','png','gif','webp','bmp']);
  html = html.replace(/\\[원본(?:\\s*이미지)?:\\s*([^\\[\\]<]+)\\]/g, (_, raw) => {{
    const name = raw.trim();
    const ext  = name.split('.').pop().toLowerCase();
    const badge = `<span class="raw-ref">📁 ${{name}}</span>`;
    if (imgExts.has(ext)) {{
      const url = '/raw-image/' + encodeURIComponent(name);
      return badge + `<span class="inline-img-wrap">` +
        `<img src="${{url}}" class="inline-img" alt="${{name}}" title="클릭하면 크게 봅니다" ` +
        `onerror="this.parentElement.style.display='none'" ` +
        `onclick="this.classList.toggle('expanded')">` +
        `<span class="inline-img-caption">${{name}}</span></span>`;
    }}
    return badge;
  }});
  return html;
}}

function inl(s) {{
  s = s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  s = s.replace(/[*]{{3}}(.+?)[*]{{3}}/g,'<strong><em>$1</em></strong>');
  s = s.replace(/[*]{{2}}(.+?)[*]{{2}}/g,'<strong>$1</strong>');
  s = s.replace(/`([^`]+)`/g,'<code>$1</code>');
  s = s.replace(/\\[([^\\]]+)\\]\\(([^)]+)\\)/g,'<a href="$2" target="_blank">$1</a>');
  return s;
}}
function renderMD(text) {{
  if (!text) return '';
  const lines = text.split('\\n');
  let html = '', inCode = false, codeLang = '', codeLines = [], inList = false, listType = 'ul', inPara = false;
  const flushCode = () => {{
    const body = codeLines.join('\\n').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    html += `<pre><code class="lang-${{codeLang}}">${{body}}</code></pre>`;
    codeLines = []; inCode = false; codeLang = '';
  }};
  const flushList = () => {{ if (inList) {{ html += `</${{listType}}>`;  inList = false; }} }};
  const flushPara = () => {{ if (inPara) {{ html += '</p>'; inPara = false; }} }};
  for (const line of lines) {{
    const fence = line.match(/^```([\\w]*)/);
    if (fence) {{
      if (inCode) {{ flushCode(); }} else {{ flushPara(); flushList(); inCode = true; codeLang = fence[1]||''; }}
      continue;
    }}
    if (inCode) {{ codeLines.push(line); continue; }}
    const hm = line.match(/^(#{{1,4}}) (.+)/);
    if (hm) {{
      flushPara(); flushList();
      const lv = Math.min(hm[1].length, 4);
      html += `<h${{lv}}>${{inl(hm[2])}}</h${{lv}}>`;
      continue;
    }}
    if (/^---+$/.test(line.trim())) {{ flushPara(); flushList(); html += '<hr>'; continue; }}
    if (/^> /.test(line)) {{
      flushPara(); flushList();
      html += `<blockquote>${{inl(line.slice(2))}}</blockquote>`;
      continue;
    }}
    const oli = line.match(/^[\\d]+[.] (.+)/);
    const uli = line.match(/^[-*+] (.+)/);
    if (uli || oli) {{
      const t = oli ? 'ol' : 'ul', item = oli ? oli[1] : uli[1];
      flushPara();
      if (!inList || listType !== t) {{ if (inList) flushList(); html += `<${{t}}>`; inList = true; listType = t; }}
      html += `<li>${{inl(item)}}</li>`;
      continue;
    }}
    if (!line.trim()) {{ flushList(); flushPara(); continue; }}
    flushList();
    if (!inPara) {{ html += '<p>'; inPara = true; }} else {{ html += '<br>'; }}
    html += inl(line);
  }}
  if (inCode) flushCode();
  flushList(); flushPara();
  return html;
}}

async function post(url, data) {{
  const r = await fetch(url, {{
    method:'POST',
    headers:{{'Content-Type':'application/x-www-form-urlencoded'}},
    body: new URLSearchParams(data)
  }});
  return r.json();
}}

function onImgSelect(input) {{
  const file = input.files[0];
  if (!file) return;
  document.getElementById('img-src').value = file.name;
  const reader = new FileReader();
  reader.onload = e => {{
    const preview = document.getElementById('img-preview');
    preview.src = e.target.result;
    document.getElementById('img-preview-wrap').style.display = 'block';
  }};
  reader.readAsDataURL(file);
}}

async function doUploadImage() {{
  const fileInput = document.getElementById('img-file');
  const file = fileInput.files[0];
  if (!file) {{ alert('이미지 파일을 선택하세요'); return; }}
  const source = document.getElementById('img-src').value.trim() || file.name;
  show('s-img'); hide('r-img');
  const reader = new FileReader();
  reader.onload = async (e) => {{
    const b64 = e.target.result;   // data:image/...;base64,... 포함 전송
    const d = await post('/upload-image', {{b64, source}});
    hide('s-img');
    const el = document.getElementById('r-img');
    if (d.success) {{
      const plist = d.pages.map(p=>`  [${{p.number}}] ${{esc(p.name)}} — ${{p.action}}`).join('\\n');
      const preview = d.image_info?.description ? `\\n\\n분석 미리보기:\\n${{esc(d.image_info.description)}}` : '';
      el.innerHTML = `✅ <strong>분석 완료</strong>\\n\\n요약: ${{esc(d.summary)}}\\n생성/갱신 페이지:\\n${{plist}}${{preview}}`;
      el.className = 'result green';
      setTimeout(() => location.reload(), 1500);
    }} else {{
      el.innerHTML = `⚠️ <strong>${{esc(d.error || '오류')}}</strong>`;
      el.className = 'result red';
    }}
    el.style.display = 'block';
  }};
  reader.onerror = () => {{ hide('s-img'); alert('파일 읽기 실패'); }};
  reader.readAsDataURL(file);
}}

async function doUploadPDF() {{
  const fileInput = document.getElementById('pdf-file');
  const file = fileInput.files[0];
  if (!file) {{ alert('PDF 파일을 선택하세요'); return; }}
  const source = document.getElementById('pdf-src').value.trim() || file.name;
  show('s-pdf'); hide('r-pdf');
  const reader = new FileReader();
  reader.onload = async (e) => {{
    const b64 = e.target.result.split(',')[1];
    const d = await post('/upload-pdf', {{b64, source}});
    hide('s-pdf');
    const el = document.getElementById('r-pdf');
    const info = d.pdf_info || {{}};
    const infoLine = info.total_pages
      ? `📄 ${{info.total_pages}}페이지 | 추출 ${{(info.extracted_chars||0).toLocaleString()}}자` +
        (info.truncated ? ` (LLM 처리는 앞 ${{(info.used_chars||0).toLocaleString()}}자)` : '')
      : '';
    if (d.success) {{
      const plist = d.pages.map(p => `  [${{p.number}}] ${{esc(p.name)}} — ${{p.action}}`).join('\\n');
      el.innerHTML = `✅ <strong>수집 완료</strong>  <span style="color:#888;font-size:12px">${{infoLine}}</span>\\n\\n요약: ${{esc(d.summary)}}\\n\\n생성/갱신 페이지:\\n${{plist}}`;
      el.className = 'result green';
      setTimeout(() => location.reload(), 1500);
    }} else {{
      el.innerHTML = `⚠️ <strong>${{esc(d.error || '오류')}}</strong>  ${{infoLine}}\\n${{esc(d.raw || '')}}`;
      el.className = 'result red';
    }}
    el.style.display = 'block';
  }};
  reader.onerror = () => {{ hide('s-pdf'); alert('파일 읽기 실패'); }};
  reader.readAsDataURL(file);
}}

async function doIngest() {{
  const text = document.getElementById('doc').value.trim();
  const source = document.getElementById('src').value.trim() || 'Unknown';
  if (!text) {{ alert('문서 내용을 입력하세요'); return; }}
  show('s-ingest'); hide('r-ingest');
  const d = await post('/ingest', {{text, source}});
  hide('s-ingest');
  const el = document.getElementById('r-ingest');
  if (d.success) {{
    const plist = d.pages.map(p=>`  [${{p.number}}] ${{esc(p.name)}} — ${{p.action}}`).join('\\n');
    el.innerHTML = `✅ <strong>수집 완료</strong>\\n\\n요약: ${{esc(d.summary)}}\\n\\n생성/갱신된 페이지:\\n${{plist}}`;
    el.className = 'result green';
    setTimeout(()=>location.reload(), 1500);
  }} else {{
    el.innerHTML = `⚠️ <strong>${{esc(d.error||'오류')}}</strong>\\n\\n${{esc(d.raw||'')}}`;
    el.className = 'result red';
  }}
  el.style.display = 'block';
}}

async function doQuery() {{
  const q = document.getElementById('q').value.trim();
  if (!q) {{ alert('질문을 입력하세요'); return; }}
  show('s-query'); hide('r-query');
  const d = await post('/query', {{question:q}});
  hide('s-query');
  const el = document.getElementById('r-query');
  el.innerHTML = linkifyWikiRefs(renderMD(d.answer || d.error || ''));
  el.style.display = 'block';
}}

async function doLint() {{
  show('s-lint'); hide('r-lint');
  const d = await post('/lint', {{}});
  hide('s-lint');
  const el = document.getElementById('r-lint');
  el.innerHTML = linkifyWikiRefs(renderMD(d.report || ''));
  el.style.display = 'block';
}}

function show(id) {{ document.getElementById(id).style.display = 'inline'; }}
function hide(id) {{ document.getElementById(id).style.display = 'none'; }}
</script>
</body>
</html>"""


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main():
    LLM_WIKI_DIR.mkdir(parents=True, exist_ok=True)

    # CLI 모드
    if len(sys.argv) > 1:
        agent = LLMWikiAgent()
        cmd = sys.argv[1]
        if cmd == "ingest" and len(sys.argv) > 2:
            path = Path(sys.argv[2])
            result = agent.ingest(path.read_text(encoding="utf-8"), path.name)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif cmd == "query" and len(sys.argv) > 2:
            print(agent.query(" ".join(sys.argv[2:])))
        elif cmd == "lint":
            print(agent.lint())
        else:
            print("사용법: llm_wiki_agent.py [ingest <파일> | query <질문> | lint]")
        return

    # 웹 서버 모드
    print("LLM Wiki Agent 초기화 중...")
    agent = LLMWikiAgent()
    WikiHandler.agent = agent

    server = ThreadedServer(("0.0.0.0", WEB_PORT), WikiHandler)
    print(f"✅ LLM Wiki Agent 실행: http://localhost:{WEB_PORT}")
    print(f"   Ollama 모델 : {OLLAMA_MODEL}")
    print(f"   SwikiSwiki  : http://localhost:8000/AIC_Wiki")
    print(f"   인덱스 페이지: http://localhost:8000/AIC_Wiki/{INDEX_PAGE}")
    print("   Ctrl+C 로 종료")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")


if __name__ == "__main__":
    main()
