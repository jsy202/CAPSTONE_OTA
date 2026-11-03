#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQuickWindow>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTimer>
#include <QDateTime>
#include <QDir>
#include <csignal>
#include <cerrno>

static volatile std::sig_atomic_t stopping = 0;
static void stop(int) { stopping = 1; }

int main(int argc, char **argv) {
    QGuiApplication app(argc, argv);
    const auto args = app.arguments();
    auto argument = [&](const QString &key, QString fallback = {}) {
        const int i = args.indexOf(key);
        return i >= 0 && i + 1 < args.size() ? args[i + 1] : fallback;
    };
    if (!args.contains("--root")) return 2;
    const auto root = QDir(argument("--root")).absolutePath();
    QQmlApplicationEngine engine;
    QVariantMap values;
    auto poll = [&] {
        QFile file(root + "/progress.json");
        QJsonObject obj;
        if (file.open(QIODevice::ReadOnly) && file.size() <= 65536)
            obj = QJsonDocument::fromJson(file.readAll()).object();
        const QString stage = obj.value("stage").toString();
        const QStringList stages{"metadata", "signature", "signature_verified", "download",
            "archive_verify", "install", "trial", "verify", "verification_failed", "recover",
            "recovery_verified", "recheck", "committed", "rolled_back", "recovery_failed", "failed", "error"};
        if (!stages.contains(stage) || !obj.value("updated").isDouble()) obj = {};
        bool terminal = QStringList{"committed", "rolled_back", "recovery_failed", "failed", "error"}
            .contains(obj.value("stage").toString());
        const auto now = QDateTime::currentMSecsSinceEpoch() / 1000.0;
        const auto remote = obj.value("_remote").toObject();
        const bool mirrored = obj.value("_remote").isObject();
        const auto receivedAge = now - remote.value("received").toDouble();
        const bool lost = mirrored && (receivedAge > 5 || receivedAge < -5 || !remote.value("error").toString().isEmpty());
        const auto age = mirrored ? remote.value("writer_age").toDouble() : now - obj.value("updated").toDouble();
        const int pid = obj.value("pid").toInt();
        const bool dead = mirrored ? !remote.value("writer_alive").toBool() : pid <= 0 || (::kill(pid, 0) != 0 && errno == ESRCH);
        const bool hasRecord = !obj.isEmpty();
        obj["transport_lost"] = lost;
        obj["unconfirmed"] = lost || (hasRecord && !terminal && (dead || age > 65 || age < -5));
        obj["age"] = age > 0 && age < 86400 ? int(age) : 0;
        const auto screen = obj.value("_screen").toObject();
        const auto screenAge = now - screen.value("received").toDouble();
        const bool screenLive = screen.value("available").toBool() && screenAge >= -5 && screenAge <= 3 && !lost;
        obj["screen_live"] = screenLive;
        obj["screen_url"] = screenLive ? QUrl::fromLocalFile(argument("--dashboard-image")).toString()
            + "?rev=" + screen.value("revision").toString() : QString();
        const auto can = obj.value("_can").toObject();
        const auto canAge = now - can.value("received").toDouble();
        obj["can_recent"] = canAge >= -5 && canAge <= 3 && can.value("error").toString().isEmpty();
        const auto next = obj.toVariantMap();
        if (next != values) {
            values = next;
            engine.rootContext()->setContextProperty("otaProgress", values);
        }
    };
    engine.rootContext()->setContextProperty("otaProgress", QVariantMap{});
    engine.rootContext()->setContextProperty("deviceRoot", root);
    engine.rootContext()->setContextProperty("presentation", args.contains("--dashboard-image"));
    poll();
    engine.load(QUrl("qrc:/progress/Panel.qml"));
    if (engine.rootObjects().isEmpty()) return 2;
    auto window = qobject_cast<QQuickWindow *>(engine.rootObjects().first());
    if (!window) return 2;
    window->setPosition(argument("--x", "1280").toInt(), argument("--y", "0").toInt());
    const QString capture = argument("--capture"), snapshot = argument("--snapshot");
    const int duration = argument("--quit-after", "0").toInt();
    if (duration > 0) QTimer::singleShot(duration, [&] {
        if (!capture.isEmpty() && !window->grabWindow().save(capture)) { app.exit(2); return; }
        if (!snapshot.isEmpty()) {
            QFile output(snapshot);
            QJsonObject report{{"title", window->property("headline").toString()},
                {"percent", window->property("percentLabel").toString()},
                {"success", window->property("success").toBool()},
                {"unconfirmed", values.value("unconfirmed").toBool()},
                {"expected", window->property("expectedLabel").toString()},
                {"actual", window->property("actualLabel").toString()},
                {"comparison_note", window->property("comparisonNote").toString()},
                {"historical_mismatch", window->property("historicalMismatch").toBool()},
                {"slot_a_version", window->property("slotAVersion").toString()},
                {"slot_b_version", window->property("slotBVersion").toString()},
                {"slot_a_selected", window->property("slotASelected").toBool()},
                {"slot_b_selected", window->property("slotBSelected").toBool()},
                {"slot_a_verified", window->property("slotAVerified").toBool()},
                {"slot_b_verified", window->property("slotBVerified").toBool()},
                {"route", window->property("route").toString()}};
            report["width"] = window->width(); report["height"] = window->height();
            report["dashboard_visible"] = window->property("dashboardVisible").toBool();
            report["event_log_count"] = window->property("logCount").toInt();
            report["can_log_count"] = window->property("canLogCount").toInt();
            if (!output.open(QIODevice::WriteOnly)) { app.exit(2); return; }
            output.write(QJsonDocument(report).toJson(QJsonDocument::Compact));
        }
        app.quit();
    });
    std::signal(SIGTERM, stop); std::signal(SIGINT, stop);
    QTimer timer;
    QObject::connect(&timer, &QTimer::timeout, [&] { if (stopping) app.quit(); else poll(); });
    timer.start(150);
    return app.exec();
}
