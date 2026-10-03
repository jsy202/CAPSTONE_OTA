#include "cluster_signals.h"

#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>
#include <QSet>
#include <QVariant>
#include <QtMath>

#include <algorithm>

#ifdef CAPSTONE_FAULT_SPEED_DIVISOR
static_assert(CAPSTONE_FAULT_SPEED_DIVISOR >= 2, "fault build needs a divisor of at least 2");
#endif

namespace {
const int MAX_MESSAGE = 4096;
const double KM_PER_MILE = 1.609344;
const char *const LAMPS[8] = {
    "checkEngineLightModel", "absWarningLightModel", "batteryWarningLightModel", "oilWarningLightModel",
    "srsWarningLightModel", "parkingBrakeLightModel", "brakeFailureLightModel", "bulbFailureLightModel",
};

bool isMph(QObject *speedo)
{
    return speedo->property("units").toString() == QLatin1String("mph");
}

bool intField(const QJsonObject &object, const char *key, int maximum, int *out)
{
    const QJsonValue value = object.value(QLatin1String(key));
    if (!value.isDouble())
        return false;
    const double number = value.toDouble();
    if (number != qFloor(number) || number < 0 || number > maximum)
        return false;
    *out = int(number);
    return true;
}

QByteArray reply(const QString &requestId, bool ok, const QJsonObject &result)
{
    QJsonObject object{{"schema_version", 1}, {"request_id", requestId}, {"ok", ok}, {"result", result}};
    return QJsonDocument(object).toJson(QJsonDocument::Compact) + '\n';
}
} // namespace

int ClusterSignalInterpreter::faultDivisor()
{
#ifdef CAPSTONE_FAULT_SPEED_DIVISOR
    return CAPSTONE_FAULT_SPEED_DIVISOR;
#else
    return 1;
#endif
}

const char *const *ClusterSignalInterpreter::lampModelNames()
{
    return LAMPS;
}

bool ClusterSignalInterpreter::apply(const WireSignals &input, const ModelAccess &models, QString *error)
{
    QObject *speedo = models.model("speedoModel");
    QObject *tach = models.model("rpmModel");
    QObject *lamps[8];
    for (int i = 0; i < 8; ++i)
        lamps[i] = models.model(LAMPS[i]);
    if (!speedo || !tach || std::find(lamps, lamps + 8, nullptr) != lamps + 8) {
        *error = QStringLiteral("dashboard model unavailable");
        return false;
    }
    const double kmh = input.speed / 10.0 / faultDivisor();
    bool written = speedo->setProperty("currentValue", isMph(speedo) ? kmh / KM_PER_MILE : kmh);
    written = tach->setProperty("rpm", input.rpm) && written;
    for (int i = 0; i < 8; ++i)
        written = lamps[i]->setProperty("on", bool(input.warnings & (1 << i))) && written;
    if (!written) {
        *error = QStringLiteral("dashboard model rejected a value");
        return false;
    }
    mGear = input.gear;
    return true;
}

bool ClusterSignalInterpreter::observe(const ModelAccess &models, WireSignals *out, QString *error) const
{
    QObject *speedo = models.model("speedoModel");
    QObject *tach = models.model("rpmModel");
    if (!speedo || !tach) {
        *error = QStringLiteral("dashboard model unavailable");
        return false;
    }
    const double shown = speedo->property("currentValue").toDouble();
    const double kmh = isMph(speedo) ? shown * KM_PER_MILE : shown;
    WireSignals result;
    result.speed = qRound(kmh * 10.0);
    result.rpm = tach->property("rpm").toInt();
    result.gear = mGear;
    for (int i = 0; i < 8; ++i) {
        QObject *lamp = models.model(LAMPS[i]);
        if (!lamp) {
            *error = QStringLiteral("dashboard model unavailable");
            return false;
        }
        if (lamp->property("on").toBool())
            result.warnings |= 1 << i;
    }
    if (result.speed < 0 || result.speed > 65535 || result.rpm < 0 || result.rpm > 65535) {
        *error = QStringLiteral("interpreted value is not representable");
        return false;
    }
    *out = result;
    return true;
}

QByteArray handleIpcRequest(const QByteArray &raw, ClusterSignalInterpreter &interpreter, const ModelAccess &models)
{
    if (raw.size() > MAX_MESSAGE || !raw.endsWith('\n'))
        return QByteArray();
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(raw, &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject())
        return QByteArray();
    const QJsonObject request = document.object();
    const QStringList keys = request.keys();
    static const QSet<QString> envelope{"schema_version", "request_id", "operation", "payload"};
    static const QRegularExpression requestId(QStringLiteral("\\A[0-9a-f]{1,64}\\z"));
    const QJsonValue schema = request.value("schema_version");
    const QString id = request.value("request_id").toString();
    if (QSet<QString>(keys.begin(), keys.end()) != envelope || !schema.isDouble() || schema.toDouble() != 1.0
            || !request.value("request_id").isString() || !requestId.match(id).hasMatch())
        return QByteArray();

    const QString operation = request.value("operation").toString();
    const QJsonObject payload = request.value("payload").toObject();
    const QStringList fields = payload.keys();
    QSet<QString> expected{"speed", "rpm", "gear", "warnings"};
    if (operation == QLatin1String("functional"))
        expected.insert("test_id");
    WireSignals s;
    int testId = 0;
    const bool valid = (operation == QLatin1String("vehicle") || operation == QLatin1String("functional"))
        && request.value("payload").isObject() && QSet<QString>(fields.begin(), fields.end()) == expected
        && intField(payload, "speed", 65535, &s.speed) && intField(payload, "rpm", 65535, &s.rpm)
        && intField(payload, "gear", 3, &s.gear) && intField(payload, "warnings", 255, &s.warnings)
        && (operation != QLatin1String("functional") || intField(payload, "test_id", 255, &testId));
    if (!valid)
        return reply(id, false, QJsonObject());

    QString error;
    if (!interpreter.apply(s, models, &error))
        return reply(id, false, QJsonObject());
    if (operation == QLatin1String("vehicle"))
        return reply(id, true, QJsonObject());

    // Functional: report what the application now holds, read back from the
    // same models the QML scene renders, in this same event-loop turn.
    WireSignals observed;
    if (!interpreter.observe(models, &observed, &error))
        return reply(id, false, QJsonObject());
    return reply(id, true, QJsonObject{{"speed", observed.speed}, {"rpm", observed.rpm},
                                       {"gear", observed.gear}, {"warnings", observed.warnings}});
}
