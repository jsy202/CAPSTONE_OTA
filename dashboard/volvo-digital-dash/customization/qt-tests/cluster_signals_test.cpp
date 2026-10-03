// Unit verification (A2) of the Cluster signal interpretation and IPC protocol.
// The fake models expose the same Q_PROPERTY names as the upstream dashboard
// models (speedoModel.currentValue/units, rpmModel.rpm, <lamp>LightModel.on).
#include <QtTest>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMap>

#include "cluster_signals.h"

class Speedo : public QObject {
    Q_OBJECT
    Q_PROPERTY(qreal currentValue READ currentValue WRITE setCurrentValue)
    Q_PROPERTY(QString units READ units WRITE setUnits)
public:
    qreal value = 0.0, clampAt = -1.0;
    QString unit = "kph";
    qreal currentValue() const { return value; }
    void setCurrentValue(qreal v) { value = (clampAt >= 0 && v > clampAt) ? clampAt : v; }
    QString units() const { return unit; }
    void setUnits(QString u) { unit = u; }
};

class Tach : public QObject {
    Q_OBJECT
    Q_PROPERTY(int rpm READ rpm WRITE setRpm)
public:
    int value = 0;
    int rpm() const { return value; }
    void setRpm(int v) { value = v; }
};

class Lamp : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool on READ on WRITE setOn)
public:
    bool value = false;
    bool on() const { return value; }
    void setOn(bool v) { value = v; }
};

class FakeModels : public ModelAccess {
public:
    Speedo speedo; Tach tach; Lamp lamps[8];
    bool hideSpeedo = false;
    QObject *model(const char *name) const override {
        const QString n = QString::fromLatin1(name);
        if (n == "speedoModel") return hideSpeedo ? nullptr : const_cast<Speedo *>(&speedo);
        if (n == "rpmModel") return const_cast<Tach *>(&tach);
        for (int i = 0; i < 8; ++i)
            if (n == QString::fromLatin1(ClusterSignalInterpreter::lampModelNames()[i]))
                return const_cast<Lamp *>(&lamps[i]);
        return nullptr;
    }
};

static QByteArray req(const QByteArray &op, const QByteArray &payload, const QByteArray &rid = "ab12",
                      const QByteArray &schema = "1")
{
    return "{\"schema_version\":" + schema + ",\"request_id\":\"" + rid + "\",\"operation\":\"" + op +
           "\",\"payload\":" + payload + "}\n";
}

static const QByteArray FUNC = "{\"speed\":650,\"rpm\":3000,\"gear\":3,\"warnings\":1,\"test_id\":7}";
static const QByteArray VEH = "{\"speed\":650,\"rpm\":3000,\"gear\":3,\"warnings\":1}";

static QJsonObject parse(const QByteArray &raw)
{
    return QJsonDocument::fromJson(raw).object();
}

class ClusterSignalsTest : public QObject
{
    Q_OBJECT
private slots:
    void faultDivisorMatchesBuild()
    {
#ifdef CAPSTONE_FAULT_SPEED_DIVISOR
        QCOMPARE(ClusterSignalInterpreter::faultDivisor(), 10);
#else
        QCOMPARE(ClusterSignalInterpreter::faultDivisor(), 1);
#endif
    }

    void vehicleWritesRealModelProperties()
    {
        FakeModels m; ClusterSignalInterpreter s; QString error;
        QVERIFY(s.apply({650, 3000, 3, 0b10000001}, m, &error));
#ifdef CAPSTONE_FAULT_SPEED_DIVISOR
        QCOMPARE(m.speedo.value, 6.5);             // the display itself is wrong
#else
        QCOMPARE(m.speedo.value, 65.0);
#endif
        QCOMPARE(m.tach.value, 3000);
        QVERIFY(m.lamps[0].value && m.lamps[7].value && !m.lamps[3].value);
    }

    void mphDisplayRoundTrips()
    {
        FakeModels m; m.speedo.unit = "mph"; ClusterSignalInterpreter s; QString error; WireSignals out;
        QVERIFY(s.apply({650, 800, 0, 0}, m, &error));
        QVERIFY(qAbs(m.speedo.value - 65.0 / 1.609344 / ClusterSignalInterpreter::faultDivisor()) < 1e-9);
        QVERIFY(s.observe(m, &out, &error));
        QCOMPARE(out.speed, 650 / ClusterSignalInterpreter::faultDivisor());
    }

    void observeReadsModelsNotRequest()
    {
        FakeModels m; ClusterSignalInterpreter s; QString error; WireSignals out;
        m.speedo.clampAt = 50.0;                    // model rewrites what it is given
        QVERIFY(s.apply({650, 3000, 3, 1}, m, &error));
        m.tach.value = 2900;                        // model changed after apply
        m.lamps[2].value = true;
        QVERIFY(s.observe(m, &out, &error));
        QCOMPARE(out.speed, qMin(500, 650 / ClusterSignalInterpreter::faultDivisor()));
        QCOMPARE(out.rpm, 2900);
        QCOMPARE(out.warnings, 0b101);
        QCOMPARE(out.gear, 3);
    }

    void functionalResponseIsInterpretedState()
    {
        FakeModels m; ClusterSignalInterpreter s;
        const QJsonObject r = parse(handleIpcRequest(req("functional", FUNC), s, m));
        QCOMPARE(r.value("ok").toBool(), true);
        QCOMPARE(r.value("request_id").toString(), QString("ab12"));
        const QJsonObject result = r.value("result").toObject();
        QCOMPARE(result.keys(), QStringList({"gear", "rpm", "speed", "warnings"}));
        QCOMPARE(result.value("speed").toInt(), 650 / ClusterSignalInterpreter::faultDivisor());
        QCOMPARE(result.value("rpm").toInt(), 3000);
        QCOMPARE(result.value("gear").toInt(), 3);
        QCOMPARE(result.value("warnings").toInt(), 1);
    }

    void functionalResponseIsNotAnEchoOfTheRequest()
    {
        FakeModels m; m.speedo.clampAt = 50.0; ClusterSignalInterpreter s;   // display saturates at 50 km/h
        const QJsonObject result = parse(handleIpcRequest(req("functional", FUNC), s, m)).value("result").toObject();
        QCOMPARE(result.value("speed").toInt(), qMin(500, 650 / ClusterSignalInterpreter::faultDivisor()));
        QVERIFY(result.value("speed").toInt() != 650);
    }

    void vehicleResponseIsOk()
    {
        FakeModels m; ClusterSignalInterpreter s;
        const QJsonObject r = parse(handleIpcRequest(req("vehicle", VEH), s, m));
        QCOMPARE(r.value("ok").toBool(), true);
        QCOMPARE(r.keys(), QStringList({"ok", "request_id", "result", "schema_version"}));
    }

    void rejectedRequestsAnswerNotOk_data()
    {
        QTest::addColumn<QByteArray>("raw");
        QTest::newRow("unknown op") << req("reboot", VEH);
        QTest::newRow("missing field") << req("vehicle", "{\"speed\":1,\"rpm\":1,\"gear\":0}");
        QTest::newRow("extra field") << req("vehicle", "{\"speed\":1,\"rpm\":1,\"gear\":0,\"warnings\":0,\"x\":1}");
        QTest::newRow("gear out of range") << req("vehicle", "{\"speed\":1,\"rpm\":1,\"gear\":4,\"warnings\":0}");
        QTest::newRow("negative speed") << req("vehicle", "{\"speed\":-1,\"rpm\":1,\"gear\":0,\"warnings\":0}");
        QTest::newRow("fractional rpm") << req("vehicle", "{\"speed\":1,\"rpm\":1.5,\"gear\":0,\"warnings\":0}");
        QTest::newRow("warnings > 255") << req("vehicle", "{\"speed\":1,\"rpm\":1,\"gear\":0,\"warnings\":256}");
        QTest::newRow("functional without test_id") << req("functional", VEH);
        QTest::newRow("string speed") << req("vehicle", "{\"speed\":\"1\",\"rpm\":1,\"gear\":0,\"warnings\":0}");
    }
    void rejectedRequestsAnswerNotOk()
    {
        QFETCH(QByteArray, raw);
        FakeModels m; ClusterSignalInterpreter s;
        const QJsonObject r = parse(handleIpcRequest(raw, s, m));
        QCOMPARE(r.value("ok").toBool(true), false);
        QCOMPARE(r.value("result").toObject().isEmpty(), true);
        QCOMPARE(m.speedo.value, 0.0);              // nothing was applied
    }

    void missingModelAnswersNotOk()
    {
        FakeModels m; m.hideSpeedo = true; ClusterSignalInterpreter s;
        QCOMPARE(parse(handleIpcRequest(req("functional", FUNC), s, m)).value("ok").toBool(true), false);
    }

    void malformedEnvelopeGetsNoResponse_data()
    {
        QTest::addColumn<QByteArray>("raw");
        QTest::newRow("not json") << QByteArray("{corrupt\n");
        QTest::newRow("array") << QByteArray("[]\n");
        QTest::newRow("no newline") << req("vehicle", VEH).chopped(1);
        QTest::newRow("oversized") << QByteArray(5000, ' ') + req("vehicle", VEH);
        QTest::newRow("bool schema") << req("vehicle", VEH, "ab12", "true");
        QTest::newRow("schema 2") << req("vehicle", VEH, "ab12", "2");
        QTest::newRow("bad request id") << req("vehicle", VEH, "XYZ");
        QTest::newRow("empty request id") << req("vehicle", VEH, "");
        QTest::newRow("extra envelope key") << QByteArray("{\"schema_version\":1,\"request_id\":\"a\",\"operation\":\"vehicle\",\"payload\":{},\"x\":1}\n");
    }
    void malformedEnvelopeGetsNoResponse()
    {
        QFETCH(QByteArray, raw);
        FakeModels m; ClusterSignalInterpreter s;
        QVERIFY(handleIpcRequest(raw, s, m).isEmpty());
    }
};

QTEST_GUILESS_MAIN(ClusterSignalsTest)
#include "cluster_signals_test.moc"
