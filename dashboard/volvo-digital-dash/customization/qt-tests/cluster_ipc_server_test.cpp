// Component verification (A2/A8) of the Cluster IPC server: event-driven,
// never blocked by a slow, partial or oversized client, safe socket handling.
#include <QtTest>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLocalSocket>
#include <QTemporaryDir>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include "cluster_ipc_server.h"

class Speedo : public QObject {
    Q_OBJECT
    Q_PROPERTY(qreal currentValue READ currentValue WRITE setCurrentValue)
    Q_PROPERTY(QString units READ units)
public:
    qreal value = 0.0;
    qreal currentValue() const { return value; }
    void setCurrentValue(qreal v) { value = v; }
    QString units() const { return "kph"; }
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
class Models : public ModelAccess {
public:
    Speedo speedo; Tach tach; Lamp lamps[8];
    QObject *model(const char *name) const override {
        const QString n = QString::fromLatin1(name);
        if (n == "speedoModel") return const_cast<Speedo *>(&speedo);
        if (n == "rpmModel") return const_cast<Tach *>(&tach);
        for (int i = 0; i < 8; ++i)
            if (n == QString::fromLatin1(ClusterSignalInterpreter::lampModelNames()[i]))
                return const_cast<Lamp *>(&lamps[i]);
        return nullptr;
    }
};

static const QByteArray FUNC =
    "{\"schema_version\":1,\"request_id\":\"ab\",\"operation\":\"functional\","
    "\"payload\":{\"speed\":650,\"rpm\":3000,\"gear\":3,\"warnings\":1,\"test_id\":1}}\n";

class ClusterIpcServerTest : public QObject
{
    Q_OBJECT
    QTemporaryDir dir;
    QString path() const { return dir.filePath("ota.sock"); }

    static QByteArray roundTrip(const QString &path, const QByteArray &request)
    {
        QLocalSocket client;
        client.connectToServer(path);
        if (!client.waitForConnected(1000))
            return "NO-CONNECT";
        client.write(request);
        client.flush();
        QByteArray reply;
        QElapsedTimer timer; timer.start();
        while (timer.elapsed() < 3000 && !reply.endsWith('\n') && client.state() == QLocalSocket::ConnectedState) {
            QCoreApplication::processEvents(QEventLoop::AllEvents, 20);
            reply += client.readAll();
        }
        reply += client.readAll();
        return reply;
    }

private slots:
    void servesFunctionalFromModels()
    {
        Models *models = new Models;
        ClusterIpcServer server(models);
        QString error;
        QVERIFY2(server.listen(path(), &error), qPrintable(error));
        const QJsonObject r = QJsonDocument::fromJson(roundTrip(path(), FUNC)).object();
        QCOMPARE(r.value("ok").toBool(), true);
        QCOMPARE(r.value("result").toObject().value("speed").toInt(), 650 / ClusterSignalInterpreter::faultDivisor());
        QCOMPARE(models->tach.value, 3000);
    }

    void socketIsOwnerAndGroupOnly()
    {
        ClusterIpcServer server(new Models);
        QString error;
        QVERIFY(server.listen(path(), &error));
        struct stat info;
        QCOMPARE(::lstat(QFile::encodeName(path()).constData(), &info), 0);
        QVERIFY(S_ISSOCK(info.st_mode));
        QCOMPARE(int(info.st_mode & 0777), 0660);
    }

    void silentClientDoesNotBlockOthers()
    {
        ClusterIpcServer server(new Models);
        QString error;
        QVERIFY(server.listen(path(), &error));
        QLocalSocket silent;
        silent.connectToServer(path());
        QVERIFY(silent.waitForConnected(1000));
        silent.write("{\"schema_version\":1");      // partial, never finished
        silent.flush();
        QElapsedTimer timer; timer.start();
        const QByteArray reply = roundTrip(path(), FUNC);
        QVERIFY(reply.contains("\"ok\":true"));
        QVERIFY(timer.elapsed() < 1000);
        QTRY_COMPARE_WITH_TIMEOUT(silent.state(), QLocalSocket::UnconnectedState, 3500);  // 2 s deadline
    }

    void oversizedInputIsClosedWithoutResponse()
    {
        ClusterIpcServer server(new Models);
        QString error;
        QVERIFY(server.listen(path(), &error));
        QCOMPARE(roundTrip(path(), QByteArray(5000, 'x')), QByteArray());
    }

    void staleSocketIsReplacedButOtherFilesAreRefused()
    {
        // A dead socket file (bound, then closed) is a stale endpoint: replace it.
        const QByteArray native = QFile::encodeName(path());
        int fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
        sockaddr_un address{};
        address.sun_family = AF_UNIX;
        qstrncpy(address.sun_path, native.constData(), sizeof(address.sun_path));
        QCOMPARE(::bind(fd, reinterpret_cast<sockaddr *>(&address), sizeof(address)), 0);
        ::close(fd);
        {
            ClusterIpcServer replaced(new Models);
            QString error;
            QVERIFY2(replaced.listen(path(), &error), qPrintable(error));
        }
        // Anything else at the path is never deleted.
        QFile::remove(path());
        QFile regular(path());
        QVERIFY(regular.open(QIODevice::WriteOnly));
        regular.write("keep");
        regular.close();
        ClusterIpcServer refused(new Models);
        QString error;
        QVERIFY(!refused.listen(path(), &error));
        QVERIFY(regular.open(QIODevice::ReadOnly));
        QCOMPARE(regular.readAll(), QByteArray("keep"));
    }

    void cleanup() { QFile::remove(path()); }
};

QTEST_GUILESS_MAIN(ClusterIpcServerTest)
#include "cluster_ipc_server_test.moc"
