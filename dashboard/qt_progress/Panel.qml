import QtQuick 2.15
import QtQuick.Window 2.15

Window {
    id: panel
    visible: true
    width: 620; height: 960
    minimumWidth: 560; minimumHeight: 960
    color: "#0c1420"
    title: "CAPSTONE OTA · 검증과 A/B 슬롯"
    property string stage: otaProgress.stage || ""
    property bool success: !otaProgress.unconfirmed && (stage === "committed" || stage === "rolled_back")
    property bool blocked: stage === "recovery_failed" || stage === "failed" || stage === "error"
    property bool recovery: ["recover", "recovery_verified", "recheck", "rolled_back", "recovery_failed"].indexOf(stage) >= 0
    property color accent: blocked ? "#ff929e" : otaProgress.unconfirmed ? "#ffca7b" : success ? "#69e1b1" : recovery ? "#ffca7b" : "#8bbcff"
    function number(value) { return typeof value === "number" && isFinite(value) }
    function version(value) { return ({"1.0.0":"V1", "2.0.0":"V2", "3.0.0":"V3"})[value] || value || "기록 없음" }
    function versionColor(value) { return value === "1.0.0" ? "#ceb0ff" : value === "3.0.0" ? "#ffca7b" : "#8bbcff" }
    property bool historicalMismatch: !!otaProgress.mismatch && number(otaProgress.mismatch.expected) && number(otaProgress.mismatch.qml)
    property var comparison: historicalMismatch ? otaProgress.mismatch : (otaProgress.observation || ({}))
    property string expectedLabel: number(comparison.expected) ? String(comparison.expected) : "—"
    property string actualLabel: number(comparison.qml) ? String(comparison.qml) : "—"
    property string comparisonNote: {
        if (historicalMismatch) {
            if (comparison.valid === false && comparison.expected === comparison.qml)
                return "표시값 일치 · 입력 유효기간 초과 기록"
            return "차이 " + Math.abs(comparison.expected - comparison.qml) + " km/h  ·  복구 후에도 실패 기록 유지"
        }
        return number(comparison.qml) ? "실제 판정에서 수집한 샘플" : "관찰 자료 대기 · 값을 만들지 않음"
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
        if (stage === "recovery_failed") return "복구 경로 차단 · 정상 서비스 미복원"
        if (stage === "rolled_back" && otaProgress.mismatch_slot && otaProgress.verified_slot)
            return otaProgress.mismatch_slot + " → " + otaProgress.verified_slot + "  ·  이전 앱 재검증 통과"
        if (recovery && !success) {
            var normal = slots.A && slots.A.role === "committed" ? "A" : slots.B && slots.B.role === "committed" ? "B" : ""
            var trial = slots.A && slots.A.role === "trial" ? "A" : slots.B && slots.B.role === "trial" ? "B" : ""
            if (normal && trial) return trial + " → " + normal + "  ·  복구본 확인·재검증 중"
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
    Column {
        anchors.fill: parent; anchors.margins: 26; spacing: 15
        Row {
            width: parent.width; height: 22
            Text { text: "CAPSTONE / OTA VALIDATION"; color: "#a9bcd1"; font.pixelSize: 14; font.bold: true }
        }
        Text { width: parent.width; text: panel.headline; color: "#f4f7fc"; font.pixelSize: 27; font.bold: true; wrapMode: Text.Wrap; textFormat: Text.PlainText }
        Rectangle {
            width: parent.width; height: 210; radius: 14
            color: panel.historicalMismatch ? "#2b1e2a" : "#16263a"
            border.color: panel.historicalMismatch ? "#c66c7b" : "#39516d"
            Column {
                anchors.fill: parent; anchors.margins: 18; spacing: 13
                Text { width: parent.width; text: panel.historicalMismatch ? (otaProgress.mismatch_phase === "recovery" ? "복구 " : "후보 ") + panel.version(otaProgress.mismatch_version) + " · 최초 불일치 기록" : "마지막 검증 샘플"; color: panel.historicalMismatch ? "#ffb4bc" : "#c4d5ed"; font.pixelSize: 16; font.bold: true; textFormat: Text.PlainText; elide: Text.ElideRight }
                Row {
                    width: parent.width; spacing: 16
                    Repeater {
                        model: ["expected", "actual"]
                        delegate: Column {
                            width: (parent.width - 16) / 2; spacing: 3
                            Text { text: modelData === "expected" ? "표시해야 할 속도" : "Qt 계기판 표시값"; color: "#bacbe0"; font.pixelSize: 16 }
                            Row {
                                spacing: 7
                                Text { text: modelData === "expected" ? panel.expectedLabel : panel.actualLabel; color: modelData === "actual" && panel.historicalMismatch ? "#ff929e" : "#f5f8ff"; font.pixelSize: 66; font.bold: true }
                                Text { text: "km/h"; color: "#b9cbe0"; font.pixelSize: 15; anchors.bottom: parent.bottom; anchors.bottomMargin: 13 }
                            }
                        }
                    }
                }
                Text { width: parent.width; font.pixelSize: 15; font.bold: true; color: panel.historicalMismatch ? "#ffb4bc" : "#a9c1dc"
                    text: panel.comparisonNote; wrapMode: Text.Wrap }
            }
        }
        Text {
            width: parent.width; color: panel.success ? "#69e1b1" : "#b9cbe0"; font.pixelSize: 16; wrapMode: Text.Wrap
            text: panel.success && otaProgress.observation && panel.number(otaProgress.observation.qml) ? (panel.stage === "rolled_back" ? "✓ 복구 재검증  " : "✓ 최종 검증  ") + otaProgress.observation.expected + " = " + otaProgress.observation.qml + " km/h" : panel.blocked ? "× 정상 서비스 복원 확인 없음" : "판정 대기 · 슬롯 선택과 정상 실행은 별도"
        }
        Text { text: "A/B 앱 슬롯  ·  버전은 신뢰 기록 기준"; color: "#b9cbe0"; font.pixelSize: 14; font.bold: true }
        Row {
            width: parent.width; height: 155; spacing: 12
            Repeater {
                model: ["A", "B"]
                delegate: Rectangle {
                    id: card
                    width: (parent.width - 12) / 2; height: 155; radius: 12
                    property var record: panel.slots[modelData] || ({})
                    property bool selected: modelData === "A" ? panel.slotASelected : panel.slotBSelected
                    property bool verified: modelData === "A" ? panel.slotAVerified : panel.slotBVerified
                    color: "#172438"
                    border.width: selected || verified ? 2 : 1
                    border.color: record.role === "recovery_failed" ? "#ff929e" : verified ? "#69e1b1" : selected ? "#8bbcff" : "#40526b"
                    Text { anchors.left: parent.left; anchors.top: parent.top; anchors.margins: 15; text: "SLOT " + modelData; color: "#b9cbe0"; font.pixelSize: 15; font.bold: true }
                    Text { anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 15; text: card.selected ? "선택 기록" : ""; color: "#a8ccff"; font.pixelSize: 13 }
                    Text { anchors.left: parent.left; anchors.leftMargin: 15; y: 43; width: parent.width - 30; text: panel.version(card.record.version); textFormat: Text.PlainText; color: panel.versionColor(card.record.version); font.pixelSize: 36; font.bold: true; elide: Text.ElideRight }
                    Text { anchors.left: parent.left; anchors.leftMargin: 15; y: 93; text: card.record.role === "recovery_failed" && otaProgress.injected_recovery_version ? "시험 주입: " + panel.version(otaProgress.injected_recovery_version) : ""; textFormat: Text.PlainText; color: "#ceb0ff"; font.pixelSize: 13 }
                    Text { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 15; font.pixelSize: 15; wrapMode: Text.Wrap
                        color: card.record.role === "recovery_failed" ? "#ffb4bc" : card.verified ? "#69e1b1" : "#b9cbe0"
                        text: card.record.role === "recovery_failed" ? "× 복구 확인 실패" : card.verified ? "✓ 검증 통과" : card.record.role === "committed" ? "마지막 정상 기록" : card.record.role === "trial" ? "시험 설치본" : card.record.version ? "이전 시험 기록" : "버전 정보 없음" }
                }
            }
        }
        Rectangle {
            width: parent.width; height: 46; radius: 8; color: panel.blocked ? "#3a2631" : "#1c3042"
            Text { anchors.fill: parent; anchors.margins: 10; text: panel.route; color: panel.blocked ? "#ffb4bc" : panel.success ? "#69e1b1" : "#c7d9ed"; font.pixelSize: 16; font.bold: true; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight; textFormat: Text.PlainText }
        }
        Text { text: panel.recovery ? "ROLLBACK / 복구 진행" : "UPDATE / 업데이트 진행"; color: panel.accent; font.pixelSize: 15; font.bold: true }
        Row {
            width: parent.width; spacing: 10; height: 23
            Rectangle {
                width: parent.width - 72; height: 12; radius: 6; color: "#34465b"; anchors.verticalCenter: parent.verticalCenter
                Rectangle { width: parent.width * (panel.stage === "download" && panel.fraction >= 0 ? panel.fraction : panel.success ? 1 : 0); height: parent.height; radius: 6; color: panel.accent }
            }
            Text { text: panel.percentLabel || (panel.success ? "완료" : "단계"); color: panel.accent; font.pixelSize: 18; font.bold: true }
        }
        Row {
            id: phases
            width: parent.width; height: 59; spacing: 5
            property var labels: panel.recovery ? ["복구본", "앱 실행", "재검증", "완료"] : ["정보", "서명", "다운로드", "설치", "검증", "확정"]
            Repeater {
                model: phases.labels
                delegate: Rectangle {
                    width: (phases.width - (phases.labels.length - 1) * 5) / phases.labels.length; height: 59; radius: 7
                    color: index === panel.stepIndex ? "#30435c" : "#172438"
                    Column {
                        anchors.centerIn: parent; spacing: 3
                        Text { anchors.horizontalCenter: parent.horizontalCenter; text: index < panel.stepIndex || panel.success ? "✓" : String(index + 1); font.pixelSize: 17; color: index <= panel.stepIndex || panel.success ? panel.accent : "#a0b5ce" }
                        Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData; font.pixelSize: 12; color: "#c7d5e6" }
                    }
                }
            }
        }
        Text { width: parent.width; color: "#a9bcd1"; font.pixelSize: 13; wrapMode: Text.Wrap
            text: panel.stage === "download" ? (otaProgress.total > 0 ? Math.round(otaProgress.received / 1024) + " / " + Math.round(otaProgress.total / 1024) + " KiB · 실제 수신량" : "전체 크기 미확인 · 수신 중") : "검사·설치·복구는 실제 단계로 표시" }
        Text { width: parent.width; text: panel.stage === "recovery_failed" ? "복구본 거부 또는 검증 실패. 기록된 V2는 현재 정상 실행의 증거가 아닙니다." : otaProgress.unconfirmed ? "갱신 중단. 표시값은 마지막 기록이며 정상 실행을 단정할 수 없습니다." : "읽기 전용 판정 기록 · 계기판 픽셀 측정과는 별도"; color: panel.blocked || otaProgress.unconfirmed ? "#ffb4bc" : "#a9bcd1"; font.pixelSize: 13; wrapMode: Text.Wrap }
    }
}
