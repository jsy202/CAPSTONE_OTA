import QtQuick 2.15
import QtQuick.Window 2.15

Window {
    id: panel
    visible: true
    width: presentation ? 1740 : 460; height: presentation ? 800 : 760
    minimumWidth: presentation ? 1740 : 460; minimumHeight: presentation ? 800 : 760
    color: "#050505"
    title: "CAPSTONE OTA · 검증과 A/B 슬롯"
    property string stage: otaProgress.stage || ""
    property bool success: !otaProgress.unconfirmed && (stage === "committed" || stage === "rolled_back")
    property bool blocked: stage === "recovery_failed" || stage === "failed" || stage === "error"
    property bool recovery: ["recover", "recovery_verified", "recheck", "rolled_back", "recovery_failed"].indexOf(stage) >= 0
    property color accent: blocked ? "#ff929e" : otaProgress.unconfirmed ? "#ffca7b" : success ? "#69e1b1" : recovery ? "#ffca7b" : "#8bbcff"
    function number(value) { return typeof value === "number" && isFinite(value) }
    function version(value) { return ({"1.0.0":"V1", "2.0.0":"V2", "3.0.0":"V3"})[value] || value || "기록 없음" }
    property bool historicalMismatch: !!otaProgress.mismatch && number(otaProgress.mismatch.expected) && number(otaProgress.mismatch.qml)
    property var comparison: historicalMismatch ? otaProgress.mismatch : (otaProgress.observation || ({}))
    property string expectedLabel: number(comparison.expected) ? String(comparison.expected) : "—"
    property string actualLabel: number(comparison.qml) ? String(comparison.qml) : "—"
    property string comparisonNote: {
        if (historicalMismatch) {
            if (comparison.valid === false && comparison.expected === comparison.qml)
                return "표시값 일치 · 입력 유효기간 초과 기록"
            return "차이 " + Math.abs(comparison.expected - comparison.qml) + " km/h"
        }
        return number(comparison.qml) ? "검증 기록" : "샘플 대기"
    }
    property var slots: otaProgress.slot_view || ({})
    property string slotAVersion: version((slots.A || {}).version)
    property string slotBVersion: version((slots.B || {}).version)
    property bool slotASelected: slots.available === true && slots.selected === "A"
    property bool slotBSelected: slots.available === true && slots.selected === "B"
    property bool slotAVerified: slotASelected && !blocked && !otaProgress.unconfirmed && otaProgress.verified_slot === "A"
    property bool slotBVerified: slotBSelected && !blocked && !otaProgress.unconfirmed && otaProgress.verified_slot === "B"
    property string route: {
        if (otaProgress.unconfirmed) return "상태 갱신 대기 · 마지막 기록 표시"
        if (stage === "recovery_failed") return "복구 중단"
        if (stage === "rolled_back" && otaProgress.mismatch_slot && otaProgress.verified_slot)
            return otaProgress.mismatch_slot + " → " + otaProgress.verified_slot + "  복구 완료"
        if (recovery && !success) {
            var normal = slots.A && slots.A.role === "committed" ? "A" : slots.B && slots.B.role === "committed" ? "B" : ""
            var trial = slots.A && slots.A.role === "trial" ? "A" : slots.B && slots.B.role === "trial" ? "B" : ""
            if (normal && trial) return trial + " → " + normal + "  복구 확인 중"
        }
        if (success) return (slots.selected || "—") + " 슬롯 · 업데이트 판정 확정"
        if (slots.selected) return slots.selected + " 슬롯 선택 기록 · 검증 결과 대기"
        return "슬롯 정보 대기"
    }
    property string headline: {
        if (otaProgress.transport_lost) return "원격 연결 끊김 · 마지막 기록"
        if (otaProgress.unconfirmed) return "진행 중단 · 결과 미확인"
        var titles = {metadata:"릴리스 정보 수신",signature:"배포 서명 확인",signature_verified:"서명 확인 완료",
            download:"앱 다운로드",archive_verify:"압축 파일 무결성 확인",install:"앱 설치",
            trial:"새 계기판 초기화",verify:"실제 계기판 검증",verification_failed:"표시 오류 검출",
            recover:"이전 앱 복구본 확인",recovery_verified:"복구본 확인 완료",recheck:"이전 앱 재검증",
            committed:"업데이트 확정",rolled_back:"V2 복구 완료",recovery_failed:"복구 차단 · 서비스 미복원",
            failed:"첫 설치 실패",error:"업데이트 중단"}
        if (stage === "rolled_back" && otaProgress.version !== "2.0.0") return "이전 앱 복구 완료"
        return titles[stage] || "진행 정보 대기"
    }
    property real fraction: otaProgress.total > 0 && otaProgress.received >= 0 && otaProgress.received <= otaProgress.total
                            ? otaProgress.received / otaProgress.total : -1
    property string percentLabel: stage === "download" && fraction >= 0 ? Math.floor(fraction * 100) + "%" : ""
    property int stepIndex: {
        var steps = {metadata:0,signature:1,signature_verified:1,download:2,archive_verify:2,install:3,
                     trial:4,verify:4,verification_failed:4,committed:5,
                     recover:0,recovery_verified:1,recheck:2,rolled_back:3,recovery_failed:0}
        return steps[stage] === undefined ? -1 : steps[stage]
    }
    property bool dashboardVisible: presentation && otaProgress.screen_live === true && clusterImage.status === Image.Ready
    property int logCount: (otaProgress._logs || []).length
    property int canLogCount: otaProgress._can ? (otaProgress._can.lines || []).length : 0
    Item {
        visible: presentation
        width: 1280; height: 480
        Image { id: clusterImage; anchors.fill: parent; source: otaProgress.screen_url || ""; cache: false; fillMode: Image.PreserveAspectFit; visible: panel.dashboardVisible }
        Text { anchors.centerIn: parent; visible: !panel.dashboardVisible; text: "계기판 화면 연결 대기"; color: "#888888"; font.pixelSize: 22 }
    }
    Item {
        visible: presentation; x: 22; y: 496; width: 1236; height: 280
        Rectangle { width: parent.width; height: 1; color: "#484848" }
        Row {
            y: 14; width: parent.width; spacing: 26
            Column {
                width: 684; spacing: 8
                Text { text: "진행 로그 · Pi2 시간"; color: "#aaaaaa"; font.pixelSize: 14 }
                Repeater {
                    model: (otaProgress._logs || []).slice(-8)
                    delegate: Text { width: 684; text: modelData; color: "#cccccc"; font.family: "Monospace"; font.pixelSize: 13; textFormat: Text.PlainText; elide: Text.ElideRight }
                }
                Text { width: 684; text: "판정: " + (otaProgress.stage || "대기"); color: panel.blocked ? "#dd8888" : panel.success ? "#82bb92" : "#aaaaaa"; font.family: "Monospace"; font.pixelSize: 13; textFormat: Text.PlainText }
                Text { width: 684; text: otaProgress.detail ? "검증 기록: " + otaProgress.detail : ""; color: "#aaaaaa"; font.family: "Monospace"; font.pixelSize: 12; textFormat: Text.PlainText; elide: Text.ElideRight }
            }
            Column {
                width: 526; spacing: 5
                Text { text: "CAN 0x200 · " + (otaProgress._can ? otaProgress._can.source || "수신 보드" : "수신 보드") + "  " + (otaProgress.can_recent ? "수신 중" : "대기"); color: "#aaaaaa"; font.pixelSize: 14 }
                Repeater {
                    model: otaProgress._can ? (otaProgress._can.lines || []) : []
                    delegate: Text { width: 526; text: modelData; color: otaProgress.can_recent ? "#cccccc" : "#777777"; font.family: "Monospace"; font.pixelSize: 12; textFormat: Text.PlainText; elide: Text.ElideRight }
                }
            }
        }
    }
    Item {
        x: presentation ? 1280 : 0
        width: 460; height: 760
    Column {
        anchors.fill: parent; anchors.margins: 22; spacing: 12
        Text { text: "업데이트 상태"; color: "#a5a5a5"; font.pixelSize: 14 }
        Text { width: parent.width; text: panel.headline; color: "#eeeeee"; font.pixelSize: 26; font.bold: true; wrapMode: Text.Wrap; textFormat: Text.PlainText }
        Rectangle { width: parent.width; height: 1; color: "#484848" }
        Text {
            text: panel.historicalMismatch ? panel.version(otaProgress.mismatch_version) + (otaProgress.mismatch_phase === "recovery" ? " 복구 실패 당시" : " 검증 실패 당시") : "최종 검증"
            color: "#adadad"; font.pixelSize: 14; textFormat: Text.PlainText
        }
        Row {
            width: parent.width; spacing: 20
            Repeater {
                model: ["expected", "actual"]
                delegate: Column {
                    width: (parent.width - 20) / 2; spacing: 1
                    Text { text: modelData === "expected" ? "기준값" : "계기판 표시값"; color: "#aaaaaa"; font.pixelSize: 14 }
                    Row {
                        spacing: 5
                        Text { text: modelData === "expected" ? panel.expectedLabel : panel.actualLabel; color: modelData === "actual" && panel.historicalMismatch ? "#ee7777" : "#eeeeee"; font.pixelSize: 54 }
                        Text { text: "km/h"; color: "#999999"; font.pixelSize: 12; anchors.bottom: parent.bottom; anchors.bottomMargin: 10 }
                    }
                }
            }
        }
        Text { width: parent.width; text: panel.comparisonNote; color: panel.historicalMismatch ? "#dd8888" : "#999999"; font.pixelSize: 14; wrapMode: Text.Wrap }
        Text {
            width: parent.width; font.pixelSize: 15; wrapMode: Text.Wrap
            color: panel.success ? "#82bb92" : "#aaaaaa"
            visible: !panel.blocked
            text: panel.success && otaProgress.observation && panel.number(otaProgress.observation.qml)
                ? (panel.stage === "rolled_back" ? panel.version(otaProgress.version) + " 재검증 통과  ·  " : "검증 통과  ·  ") + otaProgress.observation.qml + " km/h"
                : panel.blocked ? "정상 서비스 복원 안 됨" : "판정 대기"
        }
        Rectangle { width: parent.width; height: 1; color: "#484848" }
        Text { text: "앱 슬롯"; color: "#aaaaaa"; font.pixelSize: 14 }
        Column {
            width: parent.width; spacing: 8
            Repeater {
                model: ["A", "B"]
                delegate: Item {
                    id: slotRow
                    width: parent.width
                    height: record.role === "recovery_failed" && otaProgress.injected_recovery_version ? 62 : 46
                    property var record: panel.slots[modelData] || ({})
                    property bool selected: modelData === "A" ? panel.slotASelected : panel.slotBSelected
                    property bool verified: modelData === "A" ? panel.slotAVerified : panel.slotBVerified
                    Text { x: 0; y: 3; text: modelData; color: "#aaaaaa"; font.pixelSize: 20 }
                    Text { x: 35; width: 140; text: panel.version(slotRow.record.version); textFormat: Text.PlainText; color: "#eeeeee"; font.pixelSize: 24; elide: Text.ElideRight }
                    Text {
                        anchors.right: parent.right; y: 5; font.pixelSize: 14
                        color: slotRow.record.role === "recovery_failed" || (panel.blocked && slotRow.selected) ? "#dd8888" : slotRow.verified ? "#82bb92" : "#aaaaaa"
                        text: slotRow.record.role === "recovery_failed" ? "복구 거부" : slotRow.verified ? "선택 / 검증 통과" : slotRow.selected ? "선택 / 미승인" : slotRow.record.role === "trial" ? "시험본" : slotRow.record.role === "committed" ? "확정 기록" : slotRow.record.version ? "이전 버전" : "—"
                    }
                    Text { x: 35; y: 32; text: slotRow.record.role === "recovery_failed" && otaProgress.injected_recovery_version ? panel.version(otaProgress.injected_recovery_version) + " 시험 주입" : ""; textFormat: Text.PlainText; color: "#dd8888"; font.pixelSize: 13 }
                }
            }
        }
        Text { width: parent.width; text: panel.route; font.pixelSize: 15; color: panel.blocked ? "#dd8888" : panel.success ? "#82bb92" : "#aaaaaa"; wrapMode: Text.Wrap; textFormat: Text.PlainText }
        Rectangle { width: parent.width; height: 1; color: "#484848" }
        Row {
            width: parent.width; height: 20
            Text { width: parent.width - 70; text: (panel.recovery ? "복구" : "업데이트") + (panel.success || panel.blocked ? " 결과" : " 진행"); color: "#aaaaaa"; font.pixelSize: 14 }
            Text { width: 70; horizontalAlignment: Text.AlignRight; text: panel.percentLabel || (panel.blocked ? "중단" : panel.success ? "완료" : "진행 중"); color: panel.accent; font.pixelSize: 14 }
        }
        Rectangle {
            width: parent.width; height: 4; color: "#333333"
            Rectangle { width: parent.width * (panel.stage === "download" && panel.fraction >= 0 ? panel.fraction : panel.success ? 1 : 0); height: parent.height; color: panel.accent }
        }
        Row {
            id: phases
            width: parent.width; height: 32; spacing: 4
            property var labels: panel.recovery ? ["복구본", "실행", "재검증", "완료"] : ["정보", "서명", "다운로드", "설치", "검증", "확정"]
            Repeater {
                model: phases.labels
                delegate: Text {
                    width: (phases.width - (phases.labels.length - 1) * 4) / phases.labels.length
                    text: modelData; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter
                    color: index <= panel.stepIndex || panel.success ? "#cccccc" : "#747474"
                    font.underline: index === panel.stepIndex
                }
            }
        }
        Text { width: parent.width; text: otaProgress.unconfirmed ? "연결 확인 필요" : "판정 시점 기준"; color: "#808080"; font.pixelSize: 12; wrapMode: Text.Wrap }
    }
    }
}
