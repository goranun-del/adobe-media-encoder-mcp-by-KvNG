"""AME MCP: AE 컴프를 Adobe Media Encoder 로 보내 출력경로·인코딩값을 지정해 렌더하고 완료를 확인한다.

경로: Claude -> 이 서버 -> AfterFX.com -r (AE 메인 엔진) -> BridgeTalk -> AME
- aep 는 AE 로 열지 않는다. 컴프 이름은 aep 파일(RIFX)에서 직접 읽어 AE 아이템 ID 로 바꾼다.
- 출력 파일은 덮어쓰지 않는다(있으면 _02, _03 ...).
- 경로는 하드코딩하지 않는다. 설치 위치는 실행 중인 프로세스·Program Files 에서 찾고, 환경변수로 덮어쓸 수 있다.
"""
import json, os, re, struct, subprocess, threading, time, uuid
from pathlib import Path
from urllib.parse import quote, unquote

import psutil
from mcp.server.fastmcp import FastMCP

HERE = Path(__file__).resolve().parent
WORK = HERE / "_work"
JOBS = WORK / "jobs"
EVENTS = WORK / "events.jsonl"  # AME 가 모든 렌더 이벤트를 출력 경로와 함께 쓰는 곳
MY_PRESETS = HERE / "presets"
TICKS = 254016000000  # Premiere/AME 시간 단위(초당)
# epr 폴더명 뒤 4글자(FileType) -> AME 포맷 이름. 없는 포맷은 queue_comp 의 format 인자로 직접 지정
FORMATS = {"H264": "H.264", "MooV": "QuickTime", "HEVC": "HEVC (H.265)", "MP4 ": "MPEG4"}

mcp = FastMCP("ame")
_lock = threading.Lock()


# ---------- 설치 위치 ----------
def _adobe_dirs(prefix):
    # MCP 클라이언트가 최소 환경변수로 서버를 띄우면 ProgramFiles 가 없을 수 있다(실측) -> SystemDrive 기준도 본다
    roots = {os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles"),
             os.path.join(os.environ.get("SystemDrive", "C:") + os.sep, "Program Files")}
    found = []
    for r in filter(None, roots):
        d = Path(r) / "Adobe"
        if d.is_dir():
            found += [p for p in d.iterdir() if p.name.startswith(prefix)]
    return sorted(found, key=lambda p: p.name)


def _procs(name):
    out = []
    for p in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        if (p.info["name"] or "").lower() == name.lower():
            out.append(p.info)
    return out


def afterfx_com():
    env = os.environ.get("AMEMCP_AFTERFX_COM")
    if env:
        return Path(env)
    for p in _procs("AfterFX.exe"):  # 실행 중인 AE 버전 우선
        if p["exe"] and "-m" not in (p["cmdline"] or []):
            c = Path(p["exe"]).with_name("AfterFX.com")
            if c.exists():
                return c
    for d in reversed(_adobe_dirs("Adobe After Effects")):
        c = d / "Support Files" / "AfterFX.com"
        if c.exists():
            return c
    raise RuntimeError("AfterFX.com 을 찾지 못함. AMEMCP_AFTERFX_COM 환경변수로 지정")


def preset_dirs():
    dirs = [MY_PRESETS]
    ame = _adobe_dirs("Adobe Media Encoder")
    if ame:
        dirs.append(ame[-1] / "MediaIO" / "systempresets")
    docs = Path(os.environ.get("USERPROFILE", "~")).expanduser() / "Documents" / "Adobe" / "Adobe Media Encoder"
    if docs.is_dir():
        vers = sorted([p for p in docs.iterdir() if re.match(r"\d", p.name)], key=lambda p: p.name)
        if vers:
            dirs.append(vers[-1] / "Presets")
    return [d for d in dirs if d.is_dir()]


# ---------- AE -> AME 호출 ----------
def _arg(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return 'decodeURIComponent("%s")' % quote(str(v), safe="")


def call_ame(func, *args, launch=True, timeout=90):
    """ame_lib.es 의 AMEMCP.<func>(args) 를 AME 에서 실행하고 dict 를 돌려준다."""
    with _lock:
        WORK.mkdir(exist_ok=True)
        rid = uuid.uuid4().hex[:8]
        body = ("$.global.AMEMCP_EVLOG = %s;\n" % _arg(str(EVENTS).replace("\\", "/"))
                + (HERE / "ame_lib.es").read_text(encoding="utf-8")
                + "\nAMEMCP.%s(%s);\n" % (func, ",".join(_arg(a) for a in args)))
        body_f, out_f, stub_f = WORK / "req.es", WORK / "res.txt", WORK / "req.jsx"
        body_f.write_text(body, encoding="utf-8")
        out_f.write_text("", encoding="utf-8")
        fs = lambda p: str(p).replace("\\", "/")
        stub_f.write_text(
            "$.global.AMEMCP_ID=%s;$.global.AMEMCP_BODY=%s;$.global.AMEMCP_OUT=%s;$.global.AMEMCP_NOLAUNCH=%s;$.global.AMEMCP_TIMEOUT=%d;\n"
            "$.evalFile(new File(%s));\n" % (json.dumps(rid), json.dumps(fs(body_f)), json.dumps(fs(out_f)),
                                             "false" if launch else "true", timeout, json.dumps(fs(HERE / "ae_runner.jsx"))),
            encoding="utf-8-sig")
        proc = subprocess.Popen([str(afterfx_com()), "-r", str(stub_f)], creationflags=subprocess.CREATE_NO_WINDOW,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.time() + timeout + 30
        while time.time() < end:
            txt = out_f.read_text(encoding="utf-8", errors="replace")
            if txt.startswith(rid + "\n"):
                break
            time.sleep(0.5)
        else:
            if proc.poll() is None:
                proc.kill()  # 내가 띄운 AfterFX.com 만
            raise RuntimeError("AE 응답 없음(%ds). AE 가 대화상자에 막혔거나 기동 중인지 확인" % (timeout + 30))
        r = txt[len(rid) + 1:]
        if r == "NOT_RUNNING":
            return {"ok": True, "ame_running": False}
        if not r.startswith("RESULT "):
            raise RuntimeError(r)
        return json.loads(unquote(r[7:]))


# ---------- aep 읽기 ----------
def aep_comps(aep):
    """aep(RIFX)에서 컴프 (id, 이름) 목록. idta: 0-1 타입(4=컴프), 16-19 아이템 ID."""
    b = Path(aep).read_bytes()
    if b[:4] != b"RIFX":
        raise ValueError("aep(RIFX) 파일이 아님: %s" % aep)
    comps = []

    def walk(off, end):
        while off + 8 <= end:
            cid, size = b[off:off + 4], struct.unpack(">I", b[off + 4:off + 8])[0]
            body = off + 8
            if cid in (b"LIST", b"RIFX"):
                if b[body:body + 4] == b"Item":
                    _item(body + 4, body + size)
                walk(body + 4, body + size)
            off = body + size + (size & 1)

    def _item(off, end):
        typ = iid = name = None
        while off + 8 <= end:
            cid, size = b[off:off + 4], struct.unpack(">I", b[off + 4:off + 8])[0]
            body = off + 8
            if cid == b"idta":
                typ, iid = struct.unpack(">H", b[body:body + 2])[0], struct.unpack(">I", b[body + 16:body + 20])[0]
            elif cid == b"Utf8" and name is None:
                name = b[body:body + size].decode("utf-8", "replace")
            off = body + size + (size & 1)
        if typ == 4:
            comps.append((iid, name))

    walk(0, len(b))
    return comps


# ---------- 프리셋 ----------
def _param(t, key):
    i = t.find("<ParamIdentifier>%s</ParamIdentifier>" % key)
    if i < 0:
        return None, None
    blk = t[t.rfind("<ExporterParam ", 0, i):t.find("</ExporterParam>", i)]
    v = re.search(r"<ParamValue>([^<]*)</ParamValue>", blk)
    d = re.search(r"<ParamIsDisabled>(\w+)</ParamIsDisabled>", blk)
    return (v.group(1) if v else None), (d.group(1) == "true" if d else None)


def preset_info(path):
    t = Path(path).read_text(encoding="utf-8", errors="replace")
    name = re.search(r"<PresetName>([^<]*)</PresetName>", t)
    name = name.group(1) if name else Path(path).stem
    name = name.split("=", 1)[1].rstrip(")") if name.startswith("($$$") else name
    fourcc = bytes.fromhex(Path(path).parent.name.split("_")[-1]).decode("latin1") if re.fullmatch(r"[0-9A-Fa-f]{8}_[0-9A-Fa-f]{8}", Path(path).parent.name) else None
    folder = re.search(r"<FolderDisplayPath>([^<]*)</FolderDisplayPath>", t)
    fps, fps_locked = _param(t, "ADBEVideoFPS")
    w, _ = _param(t, "ADBEVideoWidth")
    h, _ = _param(t, "ADBEVideoHeight")
    tb, _ = _param(t, "ADBEVideoTargetBitrate")
    return {"name": name, "path": str(path), "folder": folder.group(1) if folder else "",
            "format": FORMATS.get(fourcc or "", None) if fourcc else _my_format(t),
            "width": int(w) if w and w.isdigit() else None, "height": int(h) if h and h.isdigit() else None,
            "fps": round(TICKS / int(fps), 3) if fps and fps.isdigit() and int(fps) else None,
            "fps_locked_to_source": fps_locked, "target_mbps": float(tb) if tb else None,
            "requested_fps": float(_my_tag(t, "fps")) if _my_tag(t, "fps") else None}


def _my_format(t):
    return _my_tag(t, "format")


def _my_tag(t, key):
    """make_preset 이 epr 에 남긴 주석 <!-- AMEMCP key=값 --> 읽기"""
    m = re.search(r"<!-- AMEMCP %s=([^>]*) -->" % key, t)
    return m.group(1) if m else None


def _unique(p):
    p = Path(p)
    if not p.exists():
        return p
    stem = re.sub(r"_\d{2}$", "", p.stem)
    n = 2
    while (p.with_name("%s_%02d%s" % (stem, n, p.suffix))).exists():
        n += 1
    return p.with_name("%s_%02d%s" % (stem, n, p.suffix))


def _num(v):
    s = "%g" % v
    return s if "." in s else s + "."


# ---------- MCP 도구 ----------
@mcp.tool()
def ame_status() -> dict:
    """AE·AME 실행 상태와 AME 배치 상태. AME 가 꺼져 있으면 띄우지 않고 그대로 알린다."""
    ae = [p["pid"] for p in _procs("AfterFX.exe") if "-m" not in (p["cmdline"] or [])]
    ame = [p["pid"] for p in _procs("Adobe Media Encoder.exe")]
    res = {"ae_pids": ae, "ame_pids": ame, "afterfx_com": None, "ame": None}
    try:
        res["afterfx_com"] = str(afterfx_com())
    except RuntimeError as e:
        res["afterfx_com"] = str(e)
    if ae and ame:
        res["ame"] = call_ame("status", launch=False, timeout=20)
    elif not ae:
        res["note"] = "AE 가 꺼져 있음. queue_comp 등은 AE 를 띄운다(기동 시간 걸림)"
    return res


@mcp.tool()
def list_presets(query: str = "", limit: int = 40) -> dict:
    """AME 프리셋(.epr) 검색: 이 서버가 만든 것 > AME 시스템 > 사용자 프리셋 순. query 는 이름·폴더 부분일치.
    fps_locked_to_source=true 인 프리셋은 make_preset 의 fps 변경이 먹지 않는다."""
    if limit < 1:
        raise ValueError("limit 은 1 이상")
    out = []
    for d in preset_dirs():
        for p in sorted(d.rglob("*.epr")):
            try:
                info = preset_info(p)
            except Exception as e:  # 깨진 프리셋은 건너뛰되 알린다
                info = {"name": p.stem, "path": str(p), "error": str(e)}
            if query.lower() in (info["name"] + " " + info.get("folder", "")).lower():
                out.append(info)
                if len(out) >= limit:
                    return {"presets": out, "truncated": True}
    # list 를 그대로 반환하면 FastMCP(1.12) 가 빈 응답을 보낸다(실측) -> dict 로 감쌈
    return {"presets": out, "truncated": False}


@mcp.tool()
def make_preset(base: str, name: str, width: int = 0, height: int = 0, fps: float = 0,
                target_mbps: float = 0, max_mbps: float = 0) -> dict:
    """base 프리셋(.epr 경로)을 복제해 해상도·fps·비트레이트를 바꾼 새 프리셋을 presets\\<name>.epr 로 만든다(덮어쓰기 없음).
    fps 를 바꾸려면 fps_locked_to_source=false 인 base 를 쓸 것(예: H.264 'Twitter 720p HD').
    잠긴 base 에 fps 를 주면 만들기는 하되 warning 을 붙이고, wait 가 실제 출력 fps 와 대조한다."""
    if not name.strip():
        raise ValueError("name 이 비어 있음")
    if min(width, height, fps, target_mbps, max_mbps) < 0:
        raise ValueError("width·height·fps·target_mbps·max_mbps 는 0(변경 안 함) 또는 양수")
    bp = Path(base)
    if not bp.is_file():
        raise ValueError("base 프리셋 파일이 없음: %s" % base)
    t = bp.read_text(encoding="utf-8")
    info = preset_info(bp)
    warning = None
    if fps and info["fps_locked_to_source"]:
        warning = "base 의 fps 가 소스 일치로 잠겨 있어 fps 변경이 무시될 수 있음(실측). fps_locked_to_source=false 인 base 권장"
    changes = {}
    if width:
        changes["ADBEVideoWidth"] = str(width)
    if height:
        changes["ADBEVideoHeight"] = str(height)
    if fps:
        changes["ADBEVideoFPS"] = str(round(TICKS / fps))
    if target_mbps:
        changes["ADBEVideoTargetBitrate"] = _num(target_mbps)
        changes["ADBEVideoMaxBitrate"] = _num(max_mbps or round(target_mbps * 1.2, 2))
    elif max_mbps:
        changes["ADBEVideoMaxBitrate"] = _num(max_mbps)
    for k, v in changes.items():
        pat = re.compile(r"(<ParamIdentifier>" + re.escape(k) + r"</ParamIdentifier>.*?<ParamValue>)([^<]*)(</ParamValue>)", re.S)
        t, n = pat.subn(lambda m: m.group(1) + v + m.group(3), t, count=1)
        if not n:
            raise ValueError("base 에 %s 항목이 없음(이 포맷은 해당 값 변경 불가)" % k)
    MY_PRESETS.mkdir(exist_ok=True)
    dst = _unique(MY_PRESETS / (re.sub(r'[\\/:*?"<>|]', "_", name) + ".epr"))
    t = re.sub(r"<PresetName>[^<]*</PresetName>", lambda m: "<PresetName>%s</PresetName>" % dst.stem, t, count=1)
    fmt = info["format"]
    # 복제본은 폴더명으로 포맷을 알 수 없고, 요청 fps 는 wait 대조용이라 주석으로 남긴다
    tags = "".join("\n<!-- AMEMCP %s=%s -->" % kv for kv in (("format", fmt), ("fps", "%g" % fps if fps else None)) if kv[1])
    t = t.replace("?>", "?>" + tags, 1) if t.startswith("<?xml") else tags.lstrip("\n") + "\n" + t
    dst.write_text(t, encoding="utf-8")
    res = {"preset": str(dst), "changes": changes, "format": fmt, "base": info["name"]}
    if warning:
        res["warning"] = warning
    return res


@mcp.tool()
def queue_comp(aep: str, comp: str, preset: str, output: str, width: int = 0, height: int = 0,
               start: bool = False, format: str = "") -> dict:
    """aep 의 컴프를 AME 대기열에 넣는다(Dynamic Link, AE 로 aep 를 열지 않음). AME 가 꺼져 있으면 띄운다.
    comp: 컴프 이름 또는 AE 아이템 ID. output: 출력 파일 경로(있으면 _02.. 로 바꿈). width/height: 프레임 크기 덮어쓰기.
    start=true 면 바로 렌더 시작. 렌더 시작은 AME 대기열 전체를 돌린다는 점에 주의. 결과 확인은 wait(job_id).
    aep 는 저장된 상태 기준으로 렌더된다(AE 의 저장 안 된 변경은 반영 안 됨)."""
    if not aep.strip() or not Path(aep).is_file():
        raise ValueError("aep 파일이 없음: '%s'" % aep)
    if not Path(preset).is_file():
        raise ValueError("프리셋 파일이 없음: '%s'" % preset)
    aep_p = Path(aep).resolve()
    comps = aep_comps(aep_p)
    hit = [c for c in comps if c[1] == comp]  # 이름 우선(숫자 이름 컴프도 있음), 없으면 ID 로
    if not hit and comp.isdigit():
        hit = [c for c in comps if c[0] == int(comp)]
    if len(hit) != 1:
        raise ValueError(("컴프 '%s' 를 찾지 못함" if not hit else "같은 이름 컴프가 여러 개('%s'), ID 로 지정") % comp
                         + ". 컴프 목록: " + ", ".join("%d:%s" % c for c in comps))
    cid, cname = hit[0]
    info = preset_info(preset)
    fmt = format or info["format"]
    if not fmt:
        raise ValueError("프리셋 포맷을 알 수 없음. format 인자로 지정(ame_status 의 formats 목록 참고)")
    out = Path(output)
    if not out.suffix:
        raise ValueError("output 은 확장자까지 포함한 파일 경로")
    out.parent.mkdir(parents=True, exist_ok=True)
    out = _unique(out)
    job = uuid.uuid4().hex[:12]
    JOBS.mkdir(parents=True, exist_ok=True)
    (JOBS / (job + ".json")).write_text(json.dumps({"aep": str(aep_p), "comp": cname, "comp_id": cid, "preset": str(preset),
                                                    "format": fmt, "output": str(out), "queued_at": time.time(),
                                                    "expected_fps": info["requested_fps"] or (None if info["fps_locked_to_source"] else info["fps"])},
                                                   ensure_ascii=False), encoding="utf-8")
    r = call_ame("queueDL", job, str(aep_p), "%08x-0000-0000-0000-000000000000" % cid, fmt, str(Path(preset).resolve()),
                 str(out), width, height, bool(start), str(EVENTS).replace("\\", "/"), timeout=120)
    if not r.get("ok"):
        raise RuntimeError(r.get("error"))
    return {"job_id": job, "comp": cname, "comp_id": cid, "format": fmt, "output": str(out), "started": r.get("started"),
            "size_override": r.get("sized")}


@mcp.tool()
def start() -> dict:
    """AME 대기열 렌더 시작(대기열 전체가 돈다)."""
    return call_ame("start")


@mcp.tool()
def wait(job_id: str, timeout_sec: int = 600) -> dict:
    """job 이 끝날 때까지 기다린다. ok/result(AME 값 True·False), AME 로그 요약, 출력 파일 실측(ffprobe 있으면)을 돌려준다.
    긴 렌더는 timeout_sec 을 짧게(예: 120) 주고 반복 호출. 시간 초과 시 queue_progress(대기열 기준 %)를 준다."""
    mf = JOBS / (job_id + ".json")
    if not re.fullmatch(r"[0-9a-f]{12}", job_id) or not mf.is_file():
        raise ValueError("job_id '%s' 없음(queue_comp 가 돌려준 job_id 를 쓸 것)" % job_id)
    meta = json.loads(mf.read_text(encoding="utf-8"))
    key = os.path.normcase(os.path.normpath(meta["output"]))
    since = meta["queued_at"] * 1000 - 5000
    end, last = time.time() + timeout_sec, None
    while time.time() < end:
        lines = EVENTS.read_text(encoding="utf-8").splitlines() if EVENTS.exists() else []
        allev = [e for e in (json.loads(x) for x in lines if x.strip()) if e.get("t", 0) >= since]
        evs = [e for e in allev if e.get("out") and os.path.normcase(os.path.normpath(e["out"])) == key]
        # 진행률 이벤트는 출력 경로가 비고 started 보다 먼저 올 때가 있어(실측) 작업별이 아닌 대기열 기준 최신값
        prog = [e["v"] for e in allev if e["ev"] == "progress"]
        last = prog[-1] if prog else None
        fin = [e for e in evs if e["ev"] == "finished"]
        if fin:
            res = {"job_id": job_id, "ok": str(fin[-1]["result"]) in ("True", "true", "Done!"),
                   "result": fin[-1]["result"], "output": meta["output"],
                   "exists": Path(meta["output"]).exists()}
            try:
                res["ame_log"] = json.loads(fin[-1]["log"]).get("summary")
            except Exception:
                res["ame_log"] = fin[-1]["log"]
            if res["exists"]:
                res["size_bytes"] = Path(meta["output"]).stat().st_size
                res["probe"] = _probe(meta["output"])
                want = meta.get("expected_fps")
                got = _probe_fps(res["probe"])
                if want and got and abs(got - want) > 0.01:
                    res["warning"] = "fps 불일치: 프리셋 %g, 실제 출력 %g (프리셋 fps 가 소스 일치로 무시된 것으로 보임)" % (want, got)
            return res
        time.sleep(1)
    return {"job_id": job_id, "result": "timeout", "queue_progress": last,
            "hint": "start 를 안 했거나 AME 가 재시작돼 이벤트가 끊겼을 수 있음. ame_status 로 배치 상태 확인"}


def _probe_fps(probe):
    if not isinstance(probe, dict):
        return None
    for s in probe.get("streams", []):
        if s.get("codec_type") == "video" and "/" in s.get("r_frame_rate", ""):
            n, d = s["r_frame_rate"].split("/")
            return int(n) / int(d) if int(d) else None
    return None


def _probe(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=codec_type,codec_name,width,height,r_frame_rate,bit_rate:format=duration,bit_rate",
                            "-of", "json", path], capture_output=True, text=True, encoding="utf-8", timeout=30,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        return json.loads(r.stdout) if r.returncode == 0 else r.stderr.strip()
    except FileNotFoundError:
        return "ffprobe 없음(PATH)"


if __name__ == "__main__":
    mcp.run()
