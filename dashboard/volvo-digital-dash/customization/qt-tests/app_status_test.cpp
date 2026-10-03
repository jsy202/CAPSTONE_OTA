// Unit verification (SWE.4) of the Cluster status display model.
#include <QtTest>
#include <QTemporaryDir>
#include <QSignalSpy>
#include <QElapsedTimer>
#include <sys/stat.h>

#include "app_status.h"

static const qint64 NOW_MS = 1000000000000LL;

static QByteArray status(const QByteArray &state, const QByteArray &version,
                         const QByteArray &restoredAt = "null", const QByteArray &schema = "1")
{
    return "{\"schema_version\":" + schema + ",\"state\":\"" + state + "\",\"version\":" +
           (version.isNull() ? QByteArray("null") : "\"" + version + "\"") +
           ",\"restored_at\":" + restoredAt + "}";
}

class AppStatusTest : public QObject
{
    Q_OBJECT

private slots:
    void cleanup()
    {
        qunsetenv("CAPSTONE_APP_STATUS_FILE");
        qunsetenv("CAPSTONE_APP_STATE");
        qunsetenv("CAPSTONE_APP_VERSION");
    }

    void validStatesMapToDisplayState_data()
    {
        QTest::addColumn<QByteArray>("json");
        QTest::addColumn<QString>("state");
        QTest::addColumn<QString>("version");
        QTest::newRow("stable") << status("stable", "1.0.0") << "stable" << "1.0.0";
        QTest::newRow("trial") << status("trial", "1.1.1") << "trial" << "1.1.1";
        QTest::newRow("unknown") << status("unknown", QByteArray()) << "unknown" << "";
    }
    void validStatesMapToDisplayState()
    {
        QFETCH(QByteArray, json);
        QFETCH(QString, state);
        QFETCH(QString, version);
        const AppStatus::Snapshot s = AppStatus::evaluate(json, NOW_MS);
        QCOMPARE(s.state, state);
        QCOMPARE(s.version, version);
    }

    void restoredWindowBoundaries_data()
    {
        QTest::addColumn<qint64>("ageMs");
        QTest::addColumn<QString>("state");
        QTest::newRow("just restored") << qint64(0) << "restored";
        QTest::newRow("5 s") << qint64(5000) << "restored";
        QTest::newRow("14.999 s") << qint64(14999) << "restored";
        QTest::newRow("15 s boundary") << qint64(15000) << "stable";
        QTest::newRow("16 s") << qint64(16000) << "stable";
        QTest::newRow("future clock") << qint64(-1000) << "stable";
    }
    void restoredWindowBoundaries()
    {
        QFETCH(qint64, ageMs);
        QFETCH(QString, state);
        const QByteArray at = QByteArray::number(double(NOW_MS - ageMs) / 1000.0, 'f', 3);
        const AppStatus::Snapshot s = AppStatus::evaluate(status("stable", "1.0.0", at), NOW_MS);
        QCOMPARE(s.state, state);
        QCOMPARE(s.version, QString("1.0.0"));
    }

    void restoredOnlyAppliesToStable()
    {
        const QByteArray at = QByteArray::number(double(NOW_MS) / 1000.0, 'f', 3);
        QCOMPARE(AppStatus::evaluate(status("trial", "1.1.1", at), NOW_MS).state, QString("trial"));
    }

    void invalidInputFailsClosedToUnknown_data()
    {
        QTest::addColumn<QByteArray>("json");
        QTest::newRow("schema 2") << status("stable", "1.0.0", "null", "2");
        QTest::newRow("schema bool") << status("stable", "1.0.0", "null", "true");
        QTest::newRow("unknown state") << status("error", "1.0.0");
        QTest::newRow("restored is not a wire state") << status("restored", "1.0.0");
        QTest::newRow("33-char version") << status("stable", QByteArray(33, '1'));
        QTest::newRow("empty version") << status("stable", "");
        QTest::newRow("missing version") << status("stable", QByteArray());
        QTest::newRow("html version") << status("stable", "<b>1</b>");
        QTest::newRow("newline version") << status("stable", "1.0\\n0");
        QTest::newRow("leading dot") << status("stable", ".1.0");
        QTest::newRow("not json") << QByteArray("{corrupt");
        QTest::newRow("partial write") << QByteArray("{\"schema_version\":1,\"state\":\"sta");
        QTest::newRow("array") << QByteArray("[]");
        QTest::newRow("empty") << QByteArray();
        QTest::newRow("oversized") << QByteArray(5000, ' ') + status("stable", "1.0.0");
        QTest::newRow("huge restored_at") << status("stable", "1.0.0", "1e300");
        QTest::newRow("negative restored_at") << status("stable", "1.0.0", "-5");
        QTest::newRow("string restored_at") << status("stable", "1.0.0", "\"now\"");
    }
    void invalidInputFailsClosedToUnknown()
    {
        QFETCH(QByteArray, json);
        const AppStatus::Snapshot s = AppStatus::evaluate(json, NOW_MS);
        QCOMPARE(s.state, QString("unknown"));
        QCOMPARE(s.version, QString());
    }

    void environmentFallback()
    {
        qputenv("CAPSTONE_APP_STATE", "trial");
        qputenv("CAPSTONE_APP_VERSION", "1.1.1");
        AppStatus::Snapshot s = AppStatus::fromEnvironment();
        QCOMPARE(s.state, QString("trial"));
        QCOMPARE(s.version, QString("1.1.1"));
        qputenv("CAPSTONE_APP_STATE", "restored");
        QCOMPARE(AppStatus::fromEnvironment().state, QString("unknown"));
        qputenv("CAPSTONE_APP_STATE", "stable");
        qputenv("CAPSTONE_APP_VERSION", "bad version");
        QCOMPARE(AppStatus::fromEnvironment().state, QString("unknown"));
        qunsetenv("CAPSTONE_APP_STATE");
        QCOMPARE(AppStatus::fromEnvironment().state, QString("unknown"));
    }

    void displayTextPerState()
    {
        QCOMPARE(AppStatus::titleFor("stable"), QString("STABLE"));
        QCOMPARE(AppStatus::detailFor("stable", "1.0.0"), QString("SW 1.0.0"));
        QCOMPARE(AppStatus::titleFor("trial"), QString("OTA TRIAL"));
        QCOMPARE(AppStatus::detailFor("trial", "1.1.1"), QString("SW 1.1.1"));
        QCOMPARE(AppStatus::titleFor("restored"), QString("RESTORED"));
        QCOMPARE(AppStatus::detailFor("restored", "1.0.0"), QString("SW 1.0.0"));
        QCOMPARE(AppStatus::titleFor("unknown"), QString("SW STATUS"));
        QCOMPARE(AppStatus::detailFor("unknown", ""), QString::fromUtf8("—"));
    }

    void fileSourceWinsAndPollingPropagatesChanges()
    {
        QTemporaryDir dir;
        const QString path = dir.filePath("digital-cluster.json");
        qputenv("CAPSTONE_APP_STATUS_FILE", path.toUtf8());
        qputenv("CAPSTONE_APP_STATE", "trial");
        qputenv("CAPSTONE_APP_VERSION", "9.9.9");
        QFile file(path);
        QVERIFY(file.open(QIODevice::WriteOnly));
        file.write(status("stable", "1.0.0"));
        file.close();

        AppStatus model;
        QCOMPARE(model.state(), QString("stable"));
        QCOMPARE(model.version(), QString("1.0.0"));
        QCOMPARE(model.bannerActive(), false);

        QSignalSpy spy(&model, &AppStatus::changed);
        QVERIFY(file.open(QIODevice::WriteOnly | QIODevice::Truncate));
        file.write(status("trial", "1.1.1"));
        file.close();
        model.refresh();
        QCOMPARE(model.state(), QString("trial"));
        QCOMPARE(model.title(), QString("OTA TRIAL"));
        QCOMPARE(model.detail(), QString("SW 1.1.1"));
        QCOMPARE(spy.count(), 1);
        model.refresh();
        QCOMPARE(spy.count(), 1);  // no change, no signal
    }

    void missingFileUsesEnvironmentThenUnknown()
    {
        QTemporaryDir dir;
        qputenv("CAPSTONE_APP_STATUS_FILE", dir.filePath("absent.json").toUtf8());
        qputenv("CAPSTONE_APP_STATE", "trial");
        qputenv("CAPSTONE_APP_VERSION", "1.1.1");
        AppStatus withEnv;
        QCOMPARE(withEnv.state(), QString("trial"));
        qunsetenv("CAPSTONE_APP_STATE");
        AppStatus without;
        QCOMPARE(without.state(), QString("unknown"));
        QCOMPARE(without.detail(), QString::fromUtf8("—"));
    }

    void restoredBannerExpiresOnTimer()
    {
        QTemporaryDir dir;
        const QString path = dir.filePath("s.json");
        qputenv("CAPSTONE_APP_STATUS_FILE", path.toUtf8());
        QFile file(path);
        QVERIFY(file.open(QIODevice::WriteOnly));
        const double at = double(QDateTime::currentMSecsSinceEpoch()) / 1000.0 - 14.0;
        file.write(status("stable", "1.0.0", QByteArray::number(at, 'f', 3)));
        file.close();
        AppStatus model;
        QCOMPARE(model.state(), QString("restored"));
        QVERIFY(model.bannerActive());
        QTRY_COMPARE_WITH_TIMEOUT(model.state(), QString("stable"), 3000);
        QVERIFY(!model.bannerActive());
    }

    void fifoStatusPathNeverBlocksTheGuiThread()
    {
        QTemporaryDir dir;
        const QString path = dir.filePath("digital-cluster.json");
        QCOMPARE(::mkfifo(path.toLocal8Bit().constData(), 0600), 0);
        qputenv("CAPSTONE_APP_STATUS_FILE", path.toUtf8());
        QElapsedTimer timer;
        timer.start();
        AppStatus model;           // must not block opening the FIFO
        QVERIFY(timer.elapsed() < 500);
        QCOMPARE(model.state(), QString("unknown"));
    }

    void symlinkStatusPathIsNotFollowed()
    {
        QTemporaryDir dir;
        const QString target = dir.filePath("elsewhere.json");
        QFile file(target);
        QVERIFY(file.open(QIODevice::WriteOnly));
        file.write(status("trial", "1.1.1"));
        file.close();
        const QString path = dir.filePath("digital-cluster.json");
        QVERIFY(QFile::link(target, path));
        qputenv("CAPSTONE_APP_STATUS_FILE", path.toUtf8());
        AppStatus model;
        QCOMPARE(model.state(), QString("unknown"));
    }

    void unreadablePathDoesNotCrash()
    {
        QTemporaryDir dir;
        qputenv("CAPSTONE_APP_STATUS_FILE", dir.path().toUtf8());  // a directory
        AppStatus model;
        QCOMPARE(model.state(), QString("unknown"));
    }
};

QTEST_GUILESS_MAIN(AppStatusTest)
#include "app_status_test.moc"
