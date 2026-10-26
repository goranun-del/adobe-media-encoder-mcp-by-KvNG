// AME 쪽 함수 모음. server.py 가 이 파일 + 호출 1줄을 BridgeTalk 로 보낸다.
// 주의: BridgeTalk 본문에 백슬래시 문자를 쓰면 깨진다 -> 문자열은 decodeURIComponent 로 받고 B 로 만든다.
var AMEMCP = (function () {
    var B = String.fromCharCode(92);
    function J(v) {
        if (v === null || v === undefined) return "null";
        var t = typeof v, i, s;
        if (t === "number" || t === "boolean") return String(v);
        if (t === "string") {
            s = '"';
            for (i = 0; i < v.length; i++) {
                var c = v.charCodeAt(i);
                if (c === 34 || c === 92) s += B + v.charAt(i);
                else if (c < 32) s += B + "u" + ("000" + c.toString(16)).slice(-4);
                else s += v.charAt(i);
            }
            return s + '"';
        }
        if (v instanceof Array) {
            var a = [];
            for (i = 0; i < v.length; i++) a.push(J(v[i]));
            return "[" + a.join(",") + "]";
        }
        var o = [];
        for (var k in v) if (v.hasOwnProperty(k)) o.push(J(k) + ":" + J(v[k]));
        return "{" + o.join(",") + "}";
    }
    function log(path, obj) {
        obj.t = new Date().getTime();
        var f = new File(path);
        f.encoding = "UTF-8";
        f.open("a");
        f.writeln(J(obj));
        f.close();
    }
    function out(obj) { return encodeURIComponent(J(obj)); }
    // wrapper 이벤트는 작업별로 갈리지 않는다(뒤 항목 이벤트가 앞 wrapper 리스너로 감, 실측).
    // 그래서 EncoderHost 이벤트에 출력 경로를 같이 기록하고, server.py 가 출력 경로로 작업을 찾는다.
    // 리스너는 AME 세션당 1번만 걸고 처리 함수($.global.AMEMCP_H)는 호출 때마다 새로 정의(코드 수정이 바로 반영).
    if (!$.global.AMEMCP_W) $.global.AMEMCP_W = {};
    if (!$.global.AMEMCP_LASTP) $.global.AMEMCP_LASTP = {};
    var key = function (p) { return String(p).toLowerCase(); };
    $.global.AMEMCP_H = {
        fail: function (e) { $.global.AMEMCP_LAST_FAIL = e.error + " | " + e.srcFilePath; },
        started: function (e) {
            $.global.AMEMCP_CUR = e.outputFilePath;
            log($.global.AMEMCP_EVLOG, { ev: "started", out: e.outputFilePath });
        },
        progress: function (e) {
            // 진행률 이벤트는 outputFilePath 가 비어 온다(실측) -> AME 는 한 번에 1건이라 started 의 경로로 채움
            var o = e.outputFilePath || $.global.AMEMCP_CUR || "", lp = $.global.AMEMCP_LASTP;
            var p = Math.round(e.progress * 100), k = key(o);
            if (lp[k] === undefined || p - lp[k] >= 10 || p === 100) { lp[k] = p; log($.global.AMEMCP_EVLOG, { ev: "progress", out: o, v: p }); }
        },
        done: function (e) {
            var w = $.global.AMEMCP_W[key(e.outputFilePath)], lo = "";
            try { if (w) lo = w.getLogOutput(); } catch (x) {}
            log($.global.AMEMCP_EVLOG, { ev: "finished", out: e.outputFilePath, result: e.result, log: lo });
        }
    };
    if (!$.global.AMEMCP_HOOKED2) {
        var eh = app.getEncoderHost();
        var on = function (tgt, name, h) { tgt.addEventListener(name, function (e) { $.global.AMEMCP_H[h](e); }, false); };
        on(app.getFrontend(), "onBatchItemCreationFailed", "fail");
        on(eh, "onItemEncodingStarted", "started");
        on(eh, "onEncodingItemProgressUpdate", "progress");
        on(eh, "onEncodingItemProgressUpdated", "progress");
        on(eh, "onItemEncodeCompleted", "done");
        on(eh, "onItemEncodeComplete", "done");
        $.global.AMEMCP_HOOKED2 = true;
    }
    return {
        status: function () {
            var eh = app.getEncoderHost();
            return out({ ok: true, batch: eh.getBatchEncoderStatus(), batchRunning: eh.isBatchRunning(), formats: eh.getFormatList() });
        },
        queueDL: function (job, aep, guid, format, preset, dst, w, h, start, logPath) {
            $.global.AMEMCP_LAST_FAIL = "";
            // 포맷 이름은 AME 언어에 따라 다름(예: HEVC(H.265)) -> 공백 무시 일치로 실제 이름 찾기
            var fl = app.getEncoderHost().getFormatList(), norm = function (s) { return String(s).split(" ").join("").toLowerCase(); };
            for (var i = 0; i < fl.length; i++) if (norm(fl[i]) === norm(format)) format = fl[i];
            var wr = app.getFrontend().addDLToBatch(aep, format, preset, guid, dst);
            if (!wr) return out({ ok: false, error: "addDLToBatch 실패: " + ($.global.AMEMCP_LAST_FAIL || "원인 미보고(포맷·프리셋·aep·컴프 확인)") });
            $.global.AMEMCP_W[String(dst).toLowerCase()] = wr;
            var sized = null;
            if (w > 0 && h > 0) sized = wr.setOutputFrameSize(w, h);
            log(logPath, { ev: "queued", job: job, out: dst });
            if (start) app.getEncoderHost().runBatch();
            return out({ ok: true, sized: sized, started: !!start });
        },
        start: function () {
            var eh = app.getEncoderHost();
            eh.runBatch();
            return out({ ok: true, batch: eh.getBatchEncoderStatus() });
        }
    };
})();
