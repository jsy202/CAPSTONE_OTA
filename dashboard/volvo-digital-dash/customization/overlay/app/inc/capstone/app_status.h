#ifndef CAPSTONE_APP_STATUS_H
#define CAPSTONE_APP_STATUS_H

#include <QByteArray>
#include <QObject>
#include <QString>
#include <QTimer>

/**
 * @brief Display-only OTA status of the running Cluster application.
 *
 * Reads the v1 status file published by capstone-ota-ui-status (default
 * /run/capstone-ota-ui/digital-cluster.json, override with
 * CAPSTONE_APP_STATUS_FILE). Only when that file does not exist are
 * CAPSTONE_APP_STATE / CAPSTONE_APP_VERSION used (desktop demos). Anything
 * that cannot be validated is shown as "unknown"; it never affects gauges.
 */
class AppStatus : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString state READ state NOTIFY changed)
    Q_PROPERTY(QString version READ version NOTIFY changed)
    Q_PROPERTY(QString title READ title NOTIFY changed)
    Q_PROPERTY(QString detail READ detail NOTIFY changed)
    Q_PROPERTY(bool bannerActive READ bannerActive NOTIFY changed)
public:
    struct Snapshot {
        QString state;    //!< stable, trial, restored or unknown
        QString version;  //!< empty unless state is known
        bool operator==(const Snapshot &other) const {
            return state == other.state && version == other.version;
        }
    };

    explicit AppStatus(QObject *parent = nullptr);

    static Snapshot evaluate(const QByteArray &json, qint64 nowMs);
    static Snapshot fromEnvironment();
    static QString titleFor(const QString &state);
    static QString detailFor(const QString &state, const QString &version);

    QString state() const { return mSnapshot.state; }
    QString version() const { return mSnapshot.version; }
    QString title() const { return titleFor(mSnapshot.state); }
    QString detail() const { return detailFor(mSnapshot.state, mSnapshot.version); }
    bool bannerActive() const { return mSnapshot.state == QLatin1String("restored"); }

public slots:
    void refresh();

signals:
    void changed();

private:
    QString mPath;
    Snapshot mSnapshot;
    QTimer mTimer;
};

#endif // CAPSTONE_APP_STATUS_H
