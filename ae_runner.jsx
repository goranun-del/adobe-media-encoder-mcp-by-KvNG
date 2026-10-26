// AfterFX.com -r 로 실행되는 stub 이 $.global.AMEMCP_* 를 채운 뒤 이 파일을 evalFile 한다.
// 하는 일: AME 스크립트 파일을 BridgeTalk 로 AME 에 보내고 회신을 결과 파일에 쓴다. AE 프로젝트는 건드리지 않는다.
(function () {
    var g = $.global, r;
    app.beginUndoGroup("AME MCP");
    try {
        if (g.AMEMCP_NOLAUNCH && !BridgeTalk.isRunning("ame")) {
            r = "NOT_RUNNING";
        } else {
            var f = new File(g.AMEMCP_BODY);
            f.encoding = "UTF-8";
            if (!f.open("r")) throw new Error("본문 파일 열기 실패: " + f.fsName);
            var body = f.read();
            f.close();
            var bt = new BridgeTalk();
            bt.target = "ame";
            bt.body = body;
            bt.onResult = function (m) { r = "RESULT " + m.body; };
            bt.onError = function (m) { r = "BTERROR " + m.body; };
            bt.send(g.AMEMCP_TIMEOUT || 60);
            if (r === undefined) r = "NO_REPLY";
        }
    } catch (e) {
        r = "AEERROR " + e.toString() + " line " + e.line;
    } finally {
        app.endUndoGroup();
    }
    var o = new File(g.AMEMCP_OUT);
    o.encoding = "UTF-8";
    o.open("w");
    o.write(g.AMEMCP_ID + "\n" + r);
    o.close();
})();
