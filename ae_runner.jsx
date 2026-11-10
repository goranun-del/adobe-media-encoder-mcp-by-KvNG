// AfterFX.com -r 로 실행되는 stub 이 $.global.AMEMCP_* 를 채운 뒤 이 파일을 evalFile 한다.
// 하는 일: AME 스크립트 파일을 BridgeTalk 로 AME 에 보내고 회신을 결과 파일에 쓴다. AE 프로젝트는 건드리지 않는다.
// v1.001: bt.send(timeout) 동기 대기가 AE 스크립트 엔진을 AME 회신까지 붙잡아(queue_comp 35초 실측) AE MCP 가 먹통이 됐다.
//         → timeout 없이 보내고 즉시 끝낸다. 회신은 onResult/onError 가 결과 파일에 쓰고, server.py 가 파일을 기다린다.
(function () {
    var g = $.global;
    var id = g.AMEMCP_ID, outPath = g.AMEMCP_OUT;   // 다음 호출이 $.global 을 덮어쓰므로 지역 변수로 고정
    function writeOut(r) {
        var o = new File(outPath);
        o.encoding = "UTF-8";
        if (o.open("w")) { o.write(id + "\n" + r); o.close(); }
    }
    app.beginUndoGroup("AME MCP");
    try {
        if (g.AMEMCP_NOLAUNCH && !BridgeTalk.isRunning("ame")) {
            writeOut("NOT_RUNNING");
        } else {
            var f = new File(g.AMEMCP_BODY);
            f.encoding = "UTF-8";
            if (!f.open("r")) throw new Error("본문 파일 열기 실패: " + f.fsName);
            var body = f.read();
            f.close();
            var bt = new BridgeTalk();
            bt.target = "ame";
            bt.body = body;
            bt.onResult = function (m) { writeOut("RESULT " + m.body); };
            bt.onError = function (m) { writeOut("BTERROR " + m.body); };
            bt.send();
        }
    } catch (e) {
        writeOut("AEERROR " + e.toString() + " line " + e.line);
    } finally {
        app.endUndoGroup();
    }
})();
