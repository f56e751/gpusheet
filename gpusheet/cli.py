"""gpusheet — GPU 예약 시트를 터미널에서 보고 예약한다. 사람과 AI 코딩 에이전트(Claude Code, Codex) 공용.

  gpusheet status [--json]                       전체 현황
  gpusheet free [--mem GB] [--model NAME] [--here] [--tmr] [--json]
                                                 지금 바로 쓸 수 있는 GPU (예약 없음 + 실제로 놀고 있음)
  gpusheet mine [--json]                         내 예약
  gpusheet reserve <서버:번호>... [--tmr] [--yes]  예약
  gpusheet release <서버:번호>... [--tmr] | --all  해제 (내 예약만)
  gpusheet setup [--name 이름] [--url URL]       처음 한 번: 이름 등록 + Claude/Codex 규칙 설치
  gpusheet guide                                 AI 에이전트용 사용 규칙 출력

서버 표기: server12:1 · gpu12:1 · 12:1 은 모두 같다 (12번 서버의 1번 GPU).
종료 코드: 0 성공 · 1 실패/거절 · 2 사용법 오류 · 3 사용자 확인 필요(--yes 없이 예약)

집계기 주소는 다음 순서로 찾는다:
  환경변수 GPUSHEET_URL → ~/.config/gpusheet/config.json 의 url → /etc/gpusheet.conf 의 url
"""
import argparse
import difflib
import getpass
import json
import os
import re
import socket
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.request

from . import __version__

USER_CONF = os.path.expanduser(os.environ.get("GPUSHEET_CONFIG", "~/.config/gpusheet/config.json"))
SYSTEM_CONF = os.environ.get("GPUSHEET_SYSTEM_CONFIG", "/etc/gpusheet.conf")
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
IDLE_UTIL = 5            # 집계기의 유휴 판정과 같은 기준 (사용률 %)
ACTIVE_MEM_MB = 1500     # 이 이상 메모리를 잡고 있으면 사용 중
DATES = {"today": "오늘", "tmr": "내일"}
CODEX_BEGIN, CODEX_END = "<!-- gpusheet:begin -->", "<!-- gpusheet:end -->"
# 집계기는 사내(교내)망에 있다. 프록시 환경변수를 타지 않게 직통으로 연결한다.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ======================================================================= 설정
def die(msg, code=1):
    sys.stdout.flush()
    print(f"gpusheet: {msg}", file=sys.stderr)
    sys.exit(code)


def read_user_conf():
    try:
        with open(USER_CONF, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def read_system_conf():
    """/etc/gpusheet.conf — `url = http://…` 같은 key = value 줄."""
    out = {}
    try:
        with open(SYSTEM_CONF, encoding="utf-8") as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def base_url(required=True):
    url = os.environ.get("GPUSHEET_URL") or read_user_conf().get("url") or read_system_conf().get("url")
    if not url and required:
        die("GPU 시트 주소를 모릅니다. `gpusheet setup --url <주소>` 로 등록하거나 관리자에게 문의하세요.")
    return (url or "").rstrip("/")


def my_name(required=True):
    name = os.environ.get("GPUSHEET_NAME") or read_user_conf().get("name")
    if not name and required:
        die("이름이 등록되지 않았습니다. 먼저 `gpusheet setup` 으로 시트에 쓰는 이름을 등록하세요.")
    return name


# ======================================================================= 통신
def http(method, path, body=None, url=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request((url or base_url()) + path, data=data, method=method, headers={
        "Content-Type": "application/json",
        "X-Gpusheet-Client": f"gpusheet/{__version__}",
        "X-Gpusheet-Host": socket.gethostname(),
        "X-Gpusheet-Account": getpass.getuser(),
    })
    try:
        with OPENER.open(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}").get("detail")
        except ValueError:
            detail = None
        return e.code, {"detail": detail or f"HTTP {e.code}"}
    except Exception as e:
        die(f"GPU 시트 서버에 연결할 수 없습니다 ({e}). 교내망인지, 주소가 맞는지 확인하세요.")


def fetch():
    st, data = http("GET", "/get_dashboard_info")
    if st != 200 or not isinstance(data, dict):
        die(f"현황을 가져오지 못했습니다 ({data.get('detail') if isinstance(data, dict) else st})")
    return data


# ======================================================================= 이름·표기
def name_keys(name):
    """같은 사람 판정용 키. 시트와 같은 규칙: 공백·대소문자 무시, 한글 3글자는 성/이름 순서 뒤집힘도 같은 사람."""
    n = "".join((name or "").split()).casefold()
    keys = {n}
    if len(n) == 3:
        keys.add(n[1:] + n[0])
        keys.add(n[-1] + n[:-1])
    return keys


def same_person(a, b):
    return bool(a) and bool(b) and bool(name_keys(a) & name_keys(b))


def alias_num(alias):
    m = re.search(r"\d+", alias)
    return int(m.group()) if m else 0


def parse_device(text):
    """server12:1 / gpu12:1 / 12:1 / gpu12_1 → gpu12_1 (영문 접두어는 무엇이든 무시)"""
    m = re.fullmatch(r"(?:[a-z]+)?(\d+)[:_](\d+)", text.strip().lower())
    if not m:
        die(f"GPU 표기를 이해할 수 없습니다: {text!r} (예: 12:1 = 12번 서버의 1번 GPU)", 2)
    return f"gpu{m.group(1)}_{m.group(2)}"


def label(device_id):
    alias, _, idx = device_id.rpartition("_")
    return f"{alias}:{idx}"


def local_alias(data):
    """지금 이 컴퓨터가 시트의 어느 서버인지 (IP 로 판별). 아니면 None."""
    ips = set()
    try:
        out = subprocess.run(["hostname", "-I"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             universal_newlines=True, timeout=3).stdout
        ips.update(out.split())
    except Exception:
        pass
    for alias, blk in data.items():
        ip = blk.get("ip") or ""
        if ip in ips:
            return alias
        if ip and not re.fullmatch(r"[\d.]+", ip):
            try:
                if socket.gethostbyname(ip) in ips:
                    return alias
            except OSError:
                pass
    return None


# ======================================================================= 현황 정리
def rows(data):
    out = []
    for alias in sorted(data, key=alias_num):
        gpus = data[alias].get("gpu") or {}
        if not gpus:
            out.append({"gpu": f"{alias}:-", "server": alias, "index": None, "state": "DOWN"})
            continue
        for idx in sorted(gpus, key=int):
            g = gpus[idx]
            util = g.get("utilization")
            mem = g.get("memory_used") or 0
            busy = (util is not None and util > IDLE_UTIL) or mem >= ACTIVE_MEM_MB
            today, tmr = g.get("user_today"), g.get("user_tmr")
            if today:
                state = "RESERVED" if busy else "RESERVED_IDLE"
            else:
                state = "IN_USE_UNRESERVED" if busy else "FREE"
            out.append({
                "gpu": f"{alias}:{idx}", "server": alias, "index": int(idx),
                "model": re.sub(r"NVIDIA (GeForce )?", "", g.get("name") or "?"),
                "mem_used_gb": round(mem / 1024, 1), "mem_total_gb": round((g.get("memory_total") or 0) / 1024),
                "util_pct": util, "temp_c": g.get("temperature"),
                "busy": busy, "idle_min": (g.get("idle_sec") or 0) // 60,
                "today": today, "tmr": tmr, "state": state,
            })
    return out


STATE_KO = {"FREE": "비어 있음", "RESERVED": "예약·사용 중", "RESERVED_IDLE": "예약·유휴",
            "IN_USE_UNRESERVED": "예약 없이 사용 중", "DOWN": "응답 없음"}


def width(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def table(head, body):
    ws = [max([width(h)] + [width(b[i]) for b in body]) for i, h in enumerate(head)]
    fmt = lambda cells: "  ".join(str(c) + " " * (ws[i] - width(c)) for i, c in enumerate(cells)).rstrip()
    print(fmt(head))
    print(fmt(["-" * w for w in ws]))
    for b in body:
        print(fmt(b))


def print_rows(rs):
    body = []
    for r in rs:
        if r["state"] == "DOWN":
            body.append([r["gpu"], "-", "", "", "", "", "", "", STATE_KO["DOWN"]])
        else:
            body.append([r["gpu"], r["model"], f"{r['mem_used_gb']}/{r['mem_total_gb']}G", f"{r['util_pct']}%",
                         f"{r['temp_c']}°C", r["today"] or "-", r["tmr"] or "-",
                         "-" if r["busy"] else f"{r['idle_min']}분", STATE_KO[r["state"]]])
    table(["GPU", "모델", "메모리", "사용률", "온도", "오늘", "내일", "유휴", "상태"], body)


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))


# ======================================================================= 명령: 조회
def cmd_status(a):
    rs = rows(fetch())
    return emit(rs) if a.json else print_rows(rs)


def cmd_free(a):
    data = fetch()
    here = local_alias(data) if a.here else None
    if a.here and not here:
        die("이 컴퓨터는 시트에 있는 서버가 아닙니다 (--here 는 서버에서만 씁니다).")
    rs = [r for r in rows(data) if r["state"] == "FREE"
          and (not a.tmr or not r["tmr"])
          and (a.mem is None or r["mem_total_gb"] >= a.mem)
          and (a.model is None or a.model.lower() in r["model"].lower())
          and (here is None or r["server"] == here)]
    if a.json:
        return emit(rs)
    if not rs:
        return print("조건에 맞는 빈 GPU 가 없습니다. (`gpusheet status` 로 전체 현황 확인)")
    print_rows(rs)
    print(f"\n{len(rs)}장 사용 가능. 예약 예: gpusheet reserve {rs[0]['gpu']}")


def my_reservations(data, name):
    out = {d: [] for d in DATES}
    for r in rows(data):
        for d in DATES:
            if same_person(r.get(d), name):
                out[d].append(r["gpu"])
    return out


def cmd_mine(a):
    name = my_name()
    data = fetch()
    res = my_reservations(data, name)
    st, info = http("GET", "/names")
    limit = info.get("max_per_person") if st == 200 and isinstance(info, dict) else None
    if a.json:
        return emit({"name": name, "reservations": res, "max_per_person": limit})
    print(f"{name}" + (f"  —  1인당 날짜별 최대 {limit}장" if limit else ""))
    for d, ko in DATES.items():
        print(f"  {ko}: " + (", ".join(res[d]) if res[d] else "없음"))


# ======================================================================= 명령: 예약·해제
def confirm(plan, yes):
    print(plan, flush=True)
    need = "사용자 확인이 필요합니다. 사용자에게 확인받은 뒤 --yes 를 붙여 다시 실행하세요."
    if yes:
        return
    # 사람이 터미널 앞에 있을 때만 묻는다. AI 에이전트는 출력을 가로채므로 stdout 이 터미널이 아니다.
    # (Windows 는 입력이 NUL 이어도 stdin.isatty() 가 참이라 stdout 까지 함께 본다)
    if sys.stdin.isatty() and sys.stdout.isatty():
        try:
            answer = input("진행할까요? [y/N] ")
        except EOFError:
            die(need, 3)
        if answer.strip().lower() in ("y", "yes", "ㅇ"):
            return
        die("취소했습니다.")
    die(need, 3)


def put(device_id, date, user):
    alias, _, idx = device_id.rpartition("_")
    return http("PUT", "/set_user", {"user": user, "device_ids": [{"hostname": alias, "index": idx}], "date": date})


def cmd_reserve(a):
    name = my_name()
    date = "tmr" if a.tmr else "today"
    data = fetch()
    by = {f"{r['server']}_{r['index']}": r for r in rows(data) if r["state"] != "DOWN"}
    devs = list(dict.fromkeys(parse_device(x) for x in a.gpus))
    for d in devs:
        if d not in by:
            die(f"없거나 응답 없는 GPU 입니다: {label(d)}")
    lines = [f"{DATES[date]} 예약 ({name}):"]
    for d in devs:
        r = by[d]
        note = f"현재 예약자 {r[date]}" if r[date] else "비어 있음"
        if r["state"] == "IN_USE_UNRESERVED":
            note += " · ⚠ 예약 없이 누가 사용 중"
        lines.append(f"  {label(d)}  {r['model']} {r['mem_total_gb']}G  ({note})")
    confirm("\n".join(lines), a.yes)
    ok = []
    for d in devs:
        st, body = put(d, date, name)
        if st == 200:
            ok.append(d)
            print(f"✔ {label(d)} 예약됨 ({name}, {DATES[date]})", flush=True)
        else:
            print(f"✘ {label(d)}: {body.get('detail')}", flush=True)
    if not ok:
        sys.exit(1)
    here = local_alias(data)
    idxs = [d.rpartition("_")[2] for d in ok if here and d.startswith(here + "_")]
    if idxs and date == "today":
        print("\n이 서버에서 쓰려면:\n  export CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=" + ",".join(idxs))
        print("  (CUDA_DEVICE_ORDER=PCI_BUS_ID 가 없으면 CUDA 번호가 시트 번호와 달라질 수 있습니다)")
    print("1시간 넘게 GPU 를 안 쓰면 예약이 자동 해제됩니다. 다 쓰면 `gpusheet release` 로 풀어 주세요.")
    sys.exit(0 if len(ok) == len(devs) else 1)


def cmd_release(a):
    name = my_name()
    data = fetch()
    if a.all:
        res = my_reservations(data, name)
        targets = [(parse_device(g), d) for d in DATES for g in res[d]]
        if not targets:
            return print("해제할 예약이 없습니다.")
    else:
        if not a.gpus:
            die("해제할 GPU 를 적거나 --all 을 주세요.", 2)
        date = "tmr" if a.tmr else "today"
        targets = [(parse_device(x), date) for x in a.gpus]
    cur = {f"{r['server']}_{r['index']}": r for r in rows(data) if r["state"] != "DOWN"}
    bad = 0
    for dev, date in targets:
        holder = (cur.get(dev) or {}).get(date)
        if not holder:
            print(f"· {label(dev)} {DATES[date]}: 이미 비어 있음")
            continue
        if not same_person(holder, name):   # AI 가 남의 예약을 지우는 실수를 막는다
            bad += 1
            print(f"✘ {label(dev)} {DATES[date]}: 본인 예약이 아닙니다 (예약자: {holder})", flush=True)
            continue
        st, body = put(dev, date, None)
        if st == 200:
            print(f"✔ {label(dev)} {DATES[date]} 예약 해제", flush=True)
        else:
            bad += 1
            print(f"✘ {label(dev)} {DATES[date]}: {body.get('detail')}", flush=True)
    sys.exit(1 if bad else 0)


# ======================================================================= 명령: 설정·안내
def asset(name):
    with open(os.path.join(ASSETS, name), encoding="utf-8") as fh:
        return fh.read()


def install_claude():
    d = os.path.expanduser("~/.claude/skills/gpusheet")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write(asset("SKILL.md") + asset("AGENTS.md"))   # 머리말 + 공통 규칙 (Codex 와 같은 내용)
    return os.path.join(d, "SKILL.md")


def install_codex():
    p = os.path.expanduser("~/.codex/AGENTS.md")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    try:
        with open(p, encoding="utf-8") as fh:
            old = fh.read()
    except OSError:
        old = ""
    block = f"{CODEX_BEGIN}\n{asset('AGENTS.md').strip()}\n{CODEX_END}\n"
    if CODEX_BEGIN in old and CODEX_END in old:
        new = re.sub(re.escape(CODEX_BEGIN) + r".*?" + re.escape(CODEX_END) + r"\n?", lambda _: block, old, flags=re.S)
    else:
        new = (old.rstrip() + "\n\n" if old.strip() else "") + block
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(new)
    return p


def cmd_setup(a):
    conf = read_user_conf()
    url = (a.url or base_url(required=False)).rstrip("/")
    if not url:
        if not sys.stdin.isatty():
            die("주소를 모릅니다. --url 로 GPU 시트 서버 주소를 주세요.", 2)
        url = input("GPU 시트 서버 주소 (관리자에게 받은 것, 예 http://x.x.x.x:10001): ").strip().rstrip("/")
    st, info = http("GET", "/names", url=url)
    known = info.get("names", []) if st == 200 and isinstance(info, dict) else []
    if st != 200:
        die(f"{url} 에서 GPU 시트 서버를 찾지 못했습니다.")
    name = a.name or conf.get("name")
    if not name:
        if not sys.stdin.isatty():
            die("--name 으로 시트에 쓰는 이름을 주세요.", 2)
        name = input("시트에 쓰는 이름 (예: 홍길동): ").strip()
    match = next((k for k in known if same_person(k, name)), None)
    if match:
        name = match
    elif not a.force:
        close = difflib.get_close_matches(name, known, n=3, cutoff=0.4)
        hint = f" 혹시: {', '.join(close)}?" if close else ""
        die(f"'{name}' 은(는) 연구실 명단에 없습니다.{hint}  처음 쓰는 이름이 맞으면 --force 를 붙이세요.")
    conf.update({"name": name})
    if a.url:
        conf["url"] = url
    os.makedirs(os.path.dirname(USER_CONF), exist_ok=True)
    with open(USER_CONF, "w", encoding="utf-8") as fh:
        json.dump(conf, fh, ensure_ascii=False, indent=1)
    print(f"✔ 이름 등록: {name}  ({USER_CONF})")
    if not a.no_claude:
        print(f"✔ Claude Code 규칙 설치: {install_claude()}")
    if not a.no_codex:
        print(f"✔ Codex 규칙 설치: {install_codex()}")
    print("새로 여는 Claude Code / Codex 세션부터 적용됩니다. 확인: gpusheet mine")


def cmd_guide(a):
    print(asset("AGENTS.md"))


# ======================================================================= 진입점
def main(argv=None):
    p = argparse.ArgumentParser(prog="gpusheet", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"gpusheet {__version__}")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("status", help="전체 현황"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("free", help="지금 쓸 수 있는 GPU")
    s.add_argument("--mem", type=int, help="최소 VRAM(GB)"); s.add_argument("--model", help="모델명 일부 (예: 4090)")
    s.add_argument("--here", action="store_true", help="지금 이 서버만"); s.add_argument("--tmr", action="store_true", help="내일도 비어 있는 것만")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_free)
    s = sub.add_parser("mine", help="내 예약"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_mine)
    s = sub.add_parser("reserve", help="예약"); s.add_argument("gpus", nargs="+", help="12:1 형식 (12번 서버의 1번 GPU)")
    s.add_argument("--tmr", action="store_true", help="내일 칸"); s.add_argument("--yes", "-y", action="store_true", help="확인 생략")
    s.set_defaults(fn=cmd_reserve)
    s = sub.add_parser("release", help="예약 해제 (내 것만)"); s.add_argument("gpus", nargs="*", help="12:1 형식")
    s.add_argument("--tmr", action="store_true", help="내일 칸"); s.add_argument("--all", action="store_true", help="내 예약 전부")
    s.set_defaults(fn=cmd_release)
    s = sub.add_parser("setup", help="이름 등록 + Claude/Codex 규칙 설치")
    s.add_argument("--name"); s.add_argument("--url"); s.add_argument("--force", action="store_true", help="명단에 없는 이름도 등록")
    s.add_argument("--no-claude", action="store_true"); s.add_argument("--no-codex", action="store_true")
    s.set_defaults(fn=cmd_setup)
    s = sub.add_parser("guide", help="AI 에이전트용 사용 규칙"); s.set_defaults(fn=cmd_guide)

    a = p.parse_args(argv)
    if not getattr(a, "fn", None):
        p.print_help()
        return
    a.fn(a)


if __name__ == "__main__":
    main()
