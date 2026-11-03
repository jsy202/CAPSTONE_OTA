import QtQuick 2.15
import QtQuick.Window 2.15

Window {
    id: panel
    visible: true
    width: 430; height: 740
    minimumWidth: 430; minimumHeight: 740
    color: "#101b29"
    title: "CAPSTONE OTA · 진행 상황"
    property string stage: otaProgress.stage || ""
    property bool success: !otaProgress.unconfirmed && (stage === "committed" || stage === "rolled_back")
    property bool blocked: stage === "recovery_failed" || stage === "failed" || stage === "error"
    property bool recovery: ["recover", "recovery_verified", "recheck", "rolled_back", "recovery_failed"].indexOf(stage) >= 0
    property color accent: blocked ? "#ff929e" : success ? "#69e1b1" : recovery ? "#ffca7b" : "#8bbcff"
    property string headline: {
        if (otaProgress.unconfirmed) return "진행 중단 · 결과 미확인"
        var titles = {metadata:"릴리스 정보 수신",signature:"배포 서명 확인",signature_verified:"서명 확인 완료",
            download:"앱 다운로드",archive_verify:"압축 파일 무결성 확인",install:"앱 설치",
            trial:"새 계기판 초기화",verify:"실제 계기판 검증",verification_failed:"표시 오류 검출",
            recover:"이전 앱 복구본 확인",recovery_verified:"복구본 확인 완료",recheck:"이전 앱 재검증",
            committed:"업데이트 확정",rolled_back:"V2 복구 완료",recovery_failed:"복구 차단 · 서비스 미복원",
            failed:"첫 설치 실패",error:"업데이트 중단"}
        // The generic updater may restore versions other than the demo's V2.
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
        anchors.fill: parent; anchors.margins: 26; spacing: 16
        Text { text: "CLUSTER / OTA MONITOR"; color: "#a9bcd1"; font.pixelSize: 13; font.bold: true }
        Row {
            spacing: 12
            Rectangle { width: 4; height: 43; radius: 2; color: panel.accent }
            Column {
                spacing: 2
                Text { text: "기록된 정상 버전"; color: "#a9bcd1"; font.pixelSize: 12 }
                Text { text: otaProgress.version || "미설치"; color: "white"; font.pixelSize: 21; font.bold: true; textFormat: Text.PlainText }
            }
            Column {
                spacing: 2
                Text { text: "시험 대상"; color: "#a9bcd1"; font.pixelSize: 12 }
                Text { text: otaProgress.candidate || "—"; color: "#c6dbff"; font.pixelSize: 21; font.bold: true; textFormat: Text.PlainText }
            }
        }
        Rectangle { width: parent.width; height: 1; color: "#33455b" }
        Text { text: panel.recovery ? "ROLLBACK / 복구" : "UPDATE / 업데이트"; color: panel.accent; font.pixelSize: 14; font.bold: true }
        Text { width: parent.width; text: panel.headline; color: "white"; font.pixelSize: 26; font.bold: true; wrapMode: Text.Wrap; textFormat: Text.PlainText }
        Row {
            width: parent.width; spacing: 8
            Rectangle {
                width: parent.width - 65; height: 12; radius: 6; color: "#34465b"; anchors.verticalCenter: parent.verticalCenter
                Rectangle { width: parent.width * (panel.stage === "download" && panel.fraction >= 0 ? panel.fraction : panel.success ? 1 : 0); height: parent.height; radius: 6; color: panel.accent }
            }
            Text { text: panel.percentLabel || (panel.success ? "완료" : "단계"); color: panel.accent; font.pixelSize: 17; font.bold: true }
        }
        Text {
            width: parent.width; color: "#b6c7db"; font.pixelSize: 12; wrapMode: Text.Wrap
            text: panel.stage === "download" ? (otaProgress.total > 0 ? Math.round(otaProgress.received / 1024) + " / " + Math.round(otaProgress.total / 1024) + " KiB · 실제 수신량" : "전체 크기 미확인 · 수신 중") : "검사·설치·복구는 퍼센트 대신 실제 단계로 표시"
        }
        Column {
            width: parent.width; spacing: 11
            Repeater {
                model: panel.recovery ? ["복구본 무결성 확인", "이전 앱 실행", "실제 계기판 재검증", "복구 완료"] : ["릴리스 정보 수신", "배포 서명 확인", "앱 다운로드·해시 확인", "앱 설치", "실제 계기판 시험", "업데이트 확정"]
                delegate: Row {
                    spacing: 12
                    Text { width: 23; text: index < panel.stepIndex || panel.success ? "✓" : index === panel.stepIndex ? "●" : "○"; color: index <= panel.stepIndex ? panel.accent : "#8ca2ba"; font.pixelSize: 16 }
                    Text { text: modelData; color: index <= panel.stepIndex ? "#f0f5fc" : "#a4b6cb"; font.pixelSize: 15 }
                }
            }
        }
        Rectangle {
            width: parent.width; height: 92; radius: 9; color: otaProgress.mismatch ? "#432c36" : "#1d2d42"
            Column {
                anchors.fill: parent; anchors.margins: 12; spacing: 5
                Text { text: otaProgress.mismatch ? "최초 표시 오류 · 복구 후에도 기록 유지" : "실제 판정 정보"; color: otaProgress.mismatch ? "#ffb3ba" : "#b6c7db"; font.pixelSize: 12 }
                Text { width: parent.width; color: "white"; font.bold: true; font.pixelSize: 19; wrapMode: Text.Wrap; textFormat: Text.PlainText
                    text: otaProgress.mismatch ? "기대 " + otaProgress.mismatch.expected + " / 실제 " + otaProgress.mismatch.qml + " km/h" : panel.blocked ? "정상 서비스 복구를 확인하지 못함" : panel.success ? "판정 통과 확인" : "검사 결과를 기다리고 있습니다" }
            }
        }
        Text { width: parent.width; text: panel.stage === "recovery_failed" ? "복구 이미지 거부. 기록된 버전은 현재 실행 증거가 아닙니다." : otaProgress.unconfirmed ? "업데이터 종료 또는 갱신 지연. 결과를 다시 확인하세요." : "별도 Qt 프로세스 · 읽기 전용 관찰"; color: panel.blocked || otaProgress.unconfirmed ? "#ffb3ba" : "#a9bcd1"; font.pixelSize: 12; wrapMode: Text.Wrap }
    }
}
