#!/usr/bin/env python3
"""
새 LLM Wiki 인스턴스 생성기
사용법: python3 new_llm_wiki.py <폴더명> [--port 포트] [--model 모델명] [--name "위키이름"]
예시:  python3 new_llm_wiki.py security_wiki --port 8003 --name "보안 LLM Wiki"
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

BASE      = Path("/home/gromit/Music/ComSwiki")
AGENT_SRC = BASE / "llm_wiki" / "llm_wiki_agent.py"
SWIKI_PAGES = BASE / "swiki" / "AIC_Wiki" / "pages"

# 현재 사용 중인 포트와 페이지 범위 조회
def used_ports():
    ports = set()
    for svc in Path.home().glob(".config/systemd/user/llm-wiki*.service"):
        txt = svc.read_text()
        for line in txt.splitlines():
            if "web_port" in line.lower() or "--port" in line.lower():
                pass
        # config.json에서 읽기
        wiki_dir = BASE / svc.stem.replace("llm-wiki-", "").replace("llm-wiki", "llm_wiki")
        cfg = wiki_dir / "config.json"
        if cfg.exists():
            try:
                ports.add(json.loads(cfg.read_text()).get("web_port", 0))
            except Exception:
                pass
    # 직접 스캔
    for cfg in BASE.glob("*/config.json"):
        try:
            ports.add(json.loads(cfg.read_text()).get("web_port", 0))
        except Exception:
            pass
    ports.discard(0)
    return ports

def next_port(start=8002):
    used = used_ports()
    p = start
    while p in used:
        p += 1
    return p

def next_page_range(gap=100):
    """
    사용 중인 index_page 중 최댓값 + gap 을 새 위키의 시작 번호로 사용.
    각 위키는 pages.json으로 자신의 페이지를 관리하므로 범위 제한 없음.
    gap은 인덱스/로그 페이지 간격 확보용.
    """
    used = set()
    for cfg in BASE.rglob("config.json"):
        try:
            d = json.loads(cfg.read_text())
            s = d.get("index_page")
            if s is not None:
                used.add(s)
        except Exception:
            pass
    if not used:
        return 300
    return max(used) + gap

def main():
    ap = argparse.ArgumentParser(description="새 LLM Wiki 인스턴스 생성")
    ap.add_argument("name",           help="폴더 이름 (영문, 예: security_wiki)")
    ap.add_argument("--port",  type=int, default=None, help="웹 포트 (기본: 자동)")
    ap.add_argument("--model", default="gemma4:latest", help="Ollama 모델")
    ap.add_argument("--wiki-name", default=None, help='위키 표시 이름 (기본: 폴더명)')
    args = ap.parse_args()

    folder    = BASE / args.name
    wiki_name = args.wiki_name or args.name.replace("_", " ").title()
    port      = args.port or next_port()
    pg_start  = next_page_range()

    if folder.exists():
        print(f"❌ 이미 존재합니다: {folder}")
        sys.exit(1)

    if not AGENT_SRC.exists():
        print(f"❌ 에이전트 스크립트 없음: {AGENT_SRC}")
        sys.exit(1)

    # 디렉토리 생성
    folder.mkdir(parents=True)
    (folder / "raw").mkdir()

    # 에이전트 스크립트 심볼릭 링크 (원본 업데이트 시 자동 반영)
    (folder / "llm_wiki_agent.py").symlink_to(AGENT_SRC)

    # config.json
    config = {
        "wiki_name":   wiki_name,
        "web_port":    port,
        "ollama_url":  "http://localhost:11434",
        "ollama_model": args.model,
        "swiki_pages": str(SWIKI_PAGES),
        "index_page":  pg_start,
        "log_page":    pg_start + 1,
        "page_start":  pg_start + 2,
    }
    (folder / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # AGENTS.md
    (folder / "AGENTS.md").write_text(
        f"# {wiki_name} — LLM Wiki Agent 스키마\n\n"
        "Karpathy LLM Wiki 패턴 기반\n"
        f"SwikiSwiki 페이지 {pg_start}번~\n\n"
        "## 페이지 형식 규칙\n"
        "- 목록: `- 항목`\n"
        "- 하위 목록: `-- 항목`\n"
        "- 위키 링크: `*페이지이름*` 또는 `*페이지번호*`\n"
        "- 마크다운 # 헤딩 사용 금지\n\n"
        "## 품질 기준\n"
        "- 페이지당 200~500자\n"
        "- 관련 페이지 상호 링크 필수\n"
        "- 한국어 우선\n",
        encoding="utf-8",
    )

    # start.sh
    start_sh = folder / "start.sh"
    start_sh.write_text(
        f"#!/bin/bash\ncd \"{folder}\"\npython3 llm_wiki_agent.py\n",
        encoding="utf-8",
    )
    start_sh.chmod(0o755)

    # systemd 서비스
    svc_name = f"llm-wiki-{args.name.replace('_','-')}"
    svc_content = f"""[Unit]
Description=LLM Wiki — {wiki_name}
After=network.target

[Service]
Type=simple
WorkingDirectory={folder}
ExecStart=/usr/bin/python3 {folder}/llm_wiki_agent.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""
    svc_path = Path.home() / ".config/systemd/user" / f"{svc_name}.service"
    svc_path.write_text(svc_content, encoding="utf-8")

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", svc_name], check=True)
    subprocess.run(["systemctl", "--user", "start",  svc_name], check=True)

    print(f"""
✅ 새 LLM Wiki 생성 완료!
   이름       : {wiki_name}
   폴더       : {folder}
   웹 UI      : http://localhost:{port}
   SwikiSwiki : http://localhost:8000/AIC_Wiki/{pg_start} (인덱스)
   시작 페이지: {pg_start} (이후 페이지 수 무제한)
   모델       : {args.model}
   서비스     : {svc_name}

관리 명령:
   systemctl --user status {svc_name}
   systemctl --user restart {svc_name}
   journalctl --user -u {svc_name} -f
""")

if __name__ == "__main__":
    main()
