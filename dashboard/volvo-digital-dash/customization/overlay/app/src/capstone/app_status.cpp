#include "app_status.h"

#include <QDateTime>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>
#include <QSet>
#include <QtMath>

namespace {
const char DEFAULT_STATUS_PATH[] = "/run/capstone-ota-ui/digital-cluster.json";
const qint64 MAX_STATUS_BYTES = 4096;
const qint64 RESTORED_BANNER_MS = 15000;
const int POLL_INTERVAL_MS = 1000;

bool validVersion(const QString &version)
{
    static const QRegularExpression pattern(
        QStringLiteral("\\A[0-9A-Za-z][0-9A-Za-z.+-]{0,31}\\z"));
    return pattern.match(version).hasMatch();
}

AppStatus::Snapshot unknown()
{
    return {QStringLiteral("unknown"), QString()};
}
} // namespace

AppStatus::AppStatus(QObject *parent)
    : QObject(parent),
      mPath(qEnvironmentVariable("CAPSTONE_APP_STATUS_FILE", QString::fromLatin1(DEFAULT_STATUS_PATH))),
      mSnapshot(unknown())
{
    refresh();
    connect(&mTimer, &QTimer::timeout, this, &AppStatus::refresh);
    mTimer.start(POLL_INTERVAL_MS);
}

AppStatus::Snapshot AppStatus::evaluate(const QByteArray &json, qint64 nowMs)
{
    if (json.size() > MAX_STATUS_BYTES)
        return unknown();
    QJsonParseError error;
    const QJsonDocument document = QJsonDocument::fromJson(json, &error);
    if (error.error != QJsonParseError::NoError || !document.isObject())
        return unknown();
    const QJsonObject object = document.object();
    const QSet<QString> keys{QStringLiteral("schema_version"), QStringLiteral("state"),
                             QStringLiteral("version"), QStringLiteral("restored_at")};
    const QStringList present = object.keys();
    if (QSet<QString>(present.begin(), present.end()) != keys)
        return unknown();
    const QJsonValue schema = object.value(QStringLiteral("schema_version"));
    if (!schema.isDouble() || schema.toDouble() != 1.0)
        return unknown();
    const QString state = object.value(QStringLiteral("state")).toString();
    if (state != QLatin1String("stable") && state != QLatin1String("trial"))
        return unknown();
    const QJsonValue version = object.value(QStringLiteral("version"));
    if (!version.isString() || !validVersion(version.toString()))
        return unknown();
    const QJsonValue restoredAt = object.value(QStringLiteral("restored_at"));
    if (!restoredAt.isNull() && !restoredAt.isDouble())
        return unknown();
    if (state == QLatin1String("stable") && restoredAt.isDouble() && qIsFinite(restoredAt.toDouble())) {
        const qint64 age = nowMs - qint64(restoredAt.toDouble() * 1000.0);
        if (age >= 0 && age < RESTORED_BANNER_MS)
            return {QStringLiteral("restored"), version.toString()};
    }
    return {state, version.toString()};
}

AppStatus::Snapshot AppStatus::fromEnvironment()
{
    const QString state = qEnvironmentVariable("CAPSTONE_APP_STATE");
    const QString version = qEnvironmentVariable("CAPSTONE_APP_VERSION");
    if ((state != QLatin1String("stable") && state != QLatin1String("trial")) || !validVersion(version))
        return unknown();
    return {state, version};
}

QString AppStatus::titleFor(const QString &state)
{
    if (state == QLatin1String("stable"))
        return QStringLiteral("STABLE");
    if (state == QLatin1String("trial"))
        return QStringLiteral("OTA TRIAL");
    if (state == QLatin1String("restored"))
        return QStringLiteral("RESTORED");
    return QStringLiteral("SW STATUS");
}

QString AppStatus::detailFor(const QString &state, const QString &version)
{
    if (state == QLatin1String("stable") || state == QLatin1String("trial")
            || state == QLatin1String("restored"))
        return QStringLiteral("SW ") + version;
    return QString::fromUtf8("—");
}

void AppStatus::refresh()
{
    Snapshot next = unknown();
    if (QFileInfo::exists(mPath)) {
        QFile file(mPath);
        if (file.open(QIODevice::ReadOnly))
            next = evaluate(file.read(MAX_STATUS_BYTES + 1), QDateTime::currentMSecsSinceEpoch());
    } else {
        next = fromEnvironment();
    }
    if (!(next == mSnapshot)) {
        mSnapshot = next;
        emit changed();
    }
}
