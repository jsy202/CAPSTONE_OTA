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
        const auto age = QDateTime::currentMSecsSinceEpoch() / 1000.0 - obj.value("updated").toDouble();
        const int pid = obj.value("pid").toInt();
        const bool dead = pid <= 0 || (::kill(pid, 0) != 0 && errno == ESRCH);
        obj["unconfirmed"] = !obj.isEmpty() && !terminal && (dead || age > 65 || age < -5);
        obj["age"] = age > 0 && age < 86400 ? int(age) : 0;
        const auto next = obj.toVariantMap();
        if (next != values) {
            values = next;
            engine.rootContext()->setContextProperty("otaProgress", values);
        }
    };
    engine.rootContext()->setContextProperty("otaProgress", QVariantMap{});
    engine.rootContext()->setContextProperty("deviceRoot", root);
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
                {"unconfirmed", values.value("unconfirmed").toBool()}};
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
