#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQmlComponent>
#include <QQmlIncubationController>
#include <QQuickWindow>
#include <QQuickItem>
#include <QLocalServer>
#include <QLocalSocket>
#include <QUdpSocket>
#include <QSocketNotifier>
#include <QJsonDocument>
#include <QJsonObject>
#include <QFile>
#include <QDir>
#include <QElapsedTimer>
#include <QTimer>
#include <QTextStream>
#include <csignal>
#include <linux/can.h>
#include <linux/can/raw.h>
#include <net/if.h>
#include <sys/socket.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <cstring>
#include <speedometer_model.h>
#include <tachometer_model.h>
#include <temp_and_fuel_gauge_model.h>
#include <accessory_gauge_model.h>
#include <warning_light_model.h>
#include <indicator_model.h>
#include <odometer_model.h>
#include <key_press_emitter.h>

static volatile std::sig_atomic_t stopping = 0;
static void stop(int) { stopping = 1; }

static QQuickItem *findVisual(QQuickItem *item, const QString &name) {
    if (item->objectName() == name) return item;
    for (auto child : item->childItems()) if (auto found = findVisual(child, name)) return found;
    return nullptr;
}

class State : public QObject {
    Q_OBJECT
    Q_PROPERTY(int gear MEMBER gear NOTIFY changed)
    Q_PROPERTY(bool valid MEMBER valid NOTIFY changed)
public:
    int gear = 0;
    bool valid = false;
signals:
    void changed();
};

static quint8 crc8(QByteArray bytes) {
    quint8 value = 0;
    for (auto byte : bytes) {
        value ^= quint8(byte);
        for (int i = 0; i < 8; ++i) value = value & 128 ? (value << 1) ^ 7 : value << 1;
    }
    return value;
}

int main(int argc, char **argv) {
    QGuiApplication app(argc, argv);
    const auto args = app.arguments();
    const bool check = args.contains("--check");
    auto argument = [&](const QString &name, const QString &fallback = QString()) {
        const int i = args.indexOf(name);
        return i >= 0 && i + 1 < args.size() ? args[i + 1] : fallback;
    };
    QFile config(QCoreApplication::applicationDirPath() + "/../cluster.json");
    if (!config.open(QIODevice::ReadOnly)) return 2;
    const auto settings = QJsonDocument::fromJson(config.readAll()).object();
    const auto policy = settings.value("policy").toString();
    if (policy != "source_counter" && policy != "arrival_order") return 2;
    SpeedometerModel speed;
    speed.setMinValue(0); speed.setMaxValue(220); speed.setUnits("km/h"); speed.setTopUnits("°C");
    TachometerModel rpm;
    rpm.setMaxRpm(7000); rpm.setRedLine(6000);
    TempAndFuelGaugeModel temp;
    temp.setProperty("minTemp", -40); temp.setProperty("maxTemp", 130); temp.setProperty("currentTemp", 90);
    temp.setProperty("fuelLevel", 60); temp.setProperty("tempUnits", "°C");
    OdometerModel odo;
    KeyPressEmitter keys;
    State state;
    // The engine must be destroyed before the models its bindings observe.
    QQmlApplicationEngine engine;
    auto context = engine.rootContext();
    context->setContextProperty("capstoneVersion", settings.value("version").toString("DEV"));
    context->setContextProperty("speedoModel", &speed);
    context->setContextProperty("rpmModel", &rpm);
    context->setContextProperty("tempFuelModel", &temp);
    context->setContextProperty("odometerModel", &odo);
    context->setContextProperty("keyPressEmitter", &keys);
    context->setContextProperty("RASPBERRY_PI", false);
    context->setContextProperty("capstoneState", &state);
    for (const auto name : {"coolantTempModel", "fuelLevelModel", "oilPModel", "oilTModel", "boostModel", "voltMeterModel"}) {
        auto model = new AccessoryGaugeModel(&app);
        model->setMinValue(0); model->setMaxValue(150); model->setUnits("");
        context->setContextProperty(name, model);
    }
    WarningLightModel *warning = nullptr;
    for (const auto name : {"parkingBrakeLightModel", "brakeFailureLightModel", "bulbFailureLightModel", "shiftUpLightModel", "highBeamLightModel", "srsWarningLightModel", "oilWarningLightModel", "batteryWarningLightModel", "absWarningLightModel", "checkEngineLightModel", "serviceLightModel"}) {
        auto model = new WarningLightModel(&app);
        model->setLightColor(QColor("#ffb347"));
        model->setText(QString(name) == "checkEngineLightModel" ? "CHECK\nENGINE" : "");
        context->setContextProperty(name, model);
        if (QString(name) == "checkEngineLightModel") warning = model;
    }
    for (const auto name : {"leftBlinkerModel", "rightBlinkerModel"}) context->setContextProperty(name, new IndicatorModel(&app));
    engine.load(QUrl("qrc:/main.qml"));
    if (engine.rootObjects().isEmpty()) return 2;
    auto window = qobject_cast<QQuickWindow *>(engine.rootObjects().first());
    QQmlComponent component(&engine, QUrl("qrc:/capstone/Overlay.qml"));
    auto panel = qobject_cast<QQuickItem *>(component.create());
    if (!window || !panel) { qWarning() << component.errors(); return 2; }
    panel->setParentItem(window->contentItem());
    auto uiReady = [&] {
        return window->property("capstoneGaugeReady").toBool()
            && findVisual(window->contentItem(), "capstoneSpeedNeedle")
            && (!engine.incubationController() || engine.incubationController()->incubatingObjectCount() == 0);
    };
    if (check) {
        QTimer ready;
        QElapsedTimer deadline; deadline.start();
        int stable = 0;
        QObject::connect(&ready, &QTimer::timeout, [&] {
            auto loader = engine.incubationController();
            if (loader) loader->incubateFor(5);
            if (!uiReady()) stable = 0; else ++stable;
            if (stable >= 3) {
                QTextStream(stdout) << "{\"initialized\":true,\"ui\":\"VolvoQt\"}\n";
                app.quit();
            } else if (deadline.elapsed() > 3000) app.exit(2);
        });
        ready.start(20);
        return app.exec();
    }
    const auto socketPath = argument("--socket", qEnvironmentVariable("CAPSTONE_MODEL_SOCKET"));
    if (socketPath.isEmpty()) return 2;
    QLocalServer control;
    control.setSocketOptions(QLocalServer::UserAccessOption | QLocalServer::GroupAccessOption);
    if (!control.listen(socketPath)) return 2; // Never unlink an existing socket owned by another app.
    QElapsedTimer age;
    int last = -1, received = -1, generation = 0, rejected = 0;
    auto consume = [&](int identifier, const QByteArray &data) {
        if (identifier != 0x200) return;
        ++generation;
        received = data.size() == 8 ? quint8(data[6]) : -1;
        if (data.size() != 8 || crc8(data.left(7) + QByteArray::fromHex("0021")) != quint8(data[7])) {
            ++rejected; return;
        }
        const int value = (quint8(data[0]) << 8) | quint8(data[1]);
        const int revs = (quint8(data[2]) << 8) | quint8(data[3]);
        const int counter = quint8(data[6]);
        const int delta = (counter - last + 256) % 256;
        if (value > 3000 || revs > 9000 || quint8(data[4]) > 6
                || (policy == "source_counter" && last >= 0 && (delta == 0 || delta >= 128))) {
            ++rejected; return;
        }
        last = counter;
        speed.setCurrentValue(value / 10.0);
        rpm.setRpm(revs);
        state.gear = quint8(data[4]); state.valid = true;
        warning->setOn(quint8(data[5]) & 1);
        age.restart(); emit state.changed();
    };
    QUdpSocket udp;
    int can = -1;
    QSocketNotifier *canNotifier = nullptr;
    if (args.contains("--udp-port")) {
        bool ok = false; int port = argument("--udp-port").toInt(&ok);
        if (!ok || port < 0 || port > 65535 || !udp.bind(QHostAddress::LocalHost, quint16(port))) return 2;
        QObject::connect(&udp, &QUdpSocket::readyRead, [&] {
            while (udp.hasPendingDatagrams()) {
                QByteArray bytes(4097, '\0'); const auto n = udp.readDatagram(bytes.data(), bytes.size());
                if (n < 0 || n > 4096) continue;
                auto frame = QJsonDocument::fromJson(bytes.left(n)).object();
                consume(frame.value("id").toInt(-1), QByteArray::fromHex(frame.value("data").toString().toLatin1()));
            }
        });
    } else {
        const auto channel = argument("--can-channel", qEnvironmentVariable("CAPSTONE_CAN_CHANNEL", "can0")).toLatin1();
        if (channel.size() >= IFNAMSIZ) return 2;
        can = ::socket(PF_CAN, SOCK_RAW | SOCK_NONBLOCK, CAN_RAW);
        if (can < 0) return 2;
        can_filter filter{0x200, CAN_SFF_MASK | CAN_EFF_FLAG | CAN_RTR_FLAG};
        if (::setsockopt(can, SOL_CAN_RAW, CAN_RAW_FILTER, &filter, sizeof(filter)) < 0) { ::close(can); return 2; }
        ifreq request{}; std::strncpy(request.ifr_name, channel.constData(), IFNAMSIZ - 1);
        sockaddr_can address{}; address.can_family = AF_CAN;
        if (::ioctl(can, SIOCGIFINDEX, &request) < 0) { ::close(can); return 2; }
        address.can_ifindex = request.ifr_ifindex;
        if (::bind(can, reinterpret_cast<sockaddr *>(&address), sizeof(address)) < 0) { ::close(can); return 2; }
        canNotifier = new QSocketNotifier(can, QSocketNotifier::Read, &app);
        QObject::connect(canNotifier, &QSocketNotifier::activated, [&](int) {
            can_frame frame{};
            while (::read(can, &frame, sizeof(frame)) == sizeof(frame))
                if (frame.can_id <= 0x7ff && frame.can_dlc <= CAN_MAX_DLEN)
                    consume(frame.can_id, QByteArray(reinterpret_cast<char *>(frame.data), frame.can_dlc));
        });
    }
    QObject::connect(&control, &QLocalServer::newConnection, [&] {
        while (auto client = control.nextPendingConnection()) {
            auto buffer = new QByteArray;
            QObject::connect(client, &QLocalSocket::disconnected, client, &QObject::deleteLater);
            QObject::connect(client, &QObject::destroyed, [buffer] { delete buffer; });
            QTimer::singleShot(1000, client, [client] { client->disconnectFromServer(); });
            QObject::connect(client, &QLocalSocket::readyRead, [&, client, buffer] {
                *buffer += client->readAll();
                if (buffer->size() > 4096) { client->disconnectFromServer(); return; }
                if (!buffer->contains('\n')) return;
                auto input = QJsonDocument::fromJson(buffer->split('\n').first()).object();
                if (input.value("op") == "capture") {
                    const auto path = argument("--capture");
                    if (!path.isEmpty()) window->grabWindow().save(path);
                }
                QJsonObject out{{"nonce", input.value("nonce")}, {"pid", int(QCoreApplication::applicationPid())},
                    {"version_label", findVisual(window->contentItem(), "capstoneVersionLabel")
                        ? findVisual(window->contentItem(), "capstoneVersionLabel")->property("text").toString() : QString()},
                    {"ui_ready", uiReady()},
                    {"model_speed", speed.currentValue()}, {"qml_speed", findVisual(window->contentItem(), "capstoneSpeedNeedle")
                        ? findVisual(window->contentItem(), "capstoneSpeedNeedle")->property("value").toDouble() : -1.0},
                    {"rpm", panel->property("displayedRpm").toInt()}, {"gear", panel->property("displayedGear").toInt()},
                    {"warning", panel->property("displayedWarning").toBool()}, {"valid", panel->property("displayedValid").toBool()},
                    {"source_counter", last}, {"received_counter", received}, {"generation", generation}, {"rejected", rejected},
                    {"executable", QCoreApplication::applicationFilePath()},
                    {"udp_port", int(udp.localPort())}, {"policy", policy}};
                client->write(QJsonDocument(out).toJson(QJsonDocument::Compact) + '\n');
                client->disconnectFromServer();
            });
        }
    });
    QTimer timer;
    QObject::connect(&timer, &QTimer::timeout, [&] {
        if (age.isValid() && age.elapsed() > 500 && state.valid) { state.valid = false; emit state.changed(); }
        if (stopping) app.quit();
    });
    timer.start(25);
    std::signal(SIGTERM, stop); std::signal(SIGINT, stop);
    const int result = app.exec();
    if (canNotifier) canNotifier->setEnabled(false);
    if (can >= 0) ::close(can);
    return result;
}
#include "main.moc"
