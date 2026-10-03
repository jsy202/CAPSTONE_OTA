#include "cluster_ipc_server.h"

#include <QFile>
#include <QLocalSocket>
#include <QTimer>
#include <QtGlobal>

#include <cerrno>
#include <sys/stat.h>

namespace {
const int MAX_MESSAGE = 4096;
const int CONNECTION_DEADLINE_MS = 2000;
const char DEFAULT_PATH[] = "/run/digital-cluster/ota.sock";
} // namespace

ClusterIpcServer::ClusterIpcServer(ModelAccess *models, QObject *parent)
    : QObject(parent), mModels(models)
{
#ifdef CAPSTONE_FAULT_SPEED_DIVISOR
    qWarning("DEMO/TEST FAULT INJECTION BUILD: speed divisor %d (never deploy as a release)",
             ClusterSignalInterpreter::faultDivisor());
#endif
    mServer.setSocketOptions(QLocalServer::UserAccessOption | QLocalServer::GroupAccessOption);
    mServer.setMaxPendingConnections(8);
    connect(&mServer, &QLocalServer::newConnection, this, &ClusterIpcServer::accept);
}

ClusterIpcServer::~ClusterIpcServer()
{
    const QString path = mServer.fullServerName();
    mServer.close();
    if (!path.isEmpty())
        QFile::remove(path);   // our own socket; listen() already proved the path was a socket
}

QString ClusterIpcServer::defaultPath()
{
    return qEnvironmentVariable("CAPSTONE_CLUSTER_IPC_SOCKET", QString::fromLatin1(DEFAULT_PATH));
}

bool ClusterIpcServer::listen(const QString &path, QString *error)
{
    struct stat info;
    const QByteArray native = QFile::encodeName(path);
    if (::lstat(native.constData(), &info) == 0) {
        if (!S_ISSOCK(info.st_mode)) {
            *error = QStringLiteral("refusing to replace non-socket ") + path;
            return false;
        }
        QLocalServer::removeServer(path);
    } else if (errno != ENOENT) {
        *error = QStringLiteral("cannot inspect ") + path;
        return false;
    }
    if (!mServer.listen(path)) {
        *error = mServer.errorString();
        return false;
    }
    // Qt leaves 0770; the socket needs only read/write for owner and group.
    if (::chmod(native.constData(), 0660) != 0) {
        mServer.close();
        *error = QStringLiteral("cannot restrict socket permissions");
        return false;
    }
    return true;
}

void ClusterIpcServer::listenOrWarn()
{
    QString error;
    if (!listen(defaultPath(), &error))
        qWarning("CAPSTONE OTA IPC unavailable: %s", qPrintable(error));
}

void ClusterIpcServer::accept()
{
    while (QLocalSocket *socket = mServer.nextPendingConnection()) {
        mBuffers.insert(socket, QByteArray());
        connect(socket, &QLocalSocket::readyRead, this, [this, socket] { read(socket); });
        connect(socket, &QLocalSocket::disconnected, this, [this, socket] { finish(socket); });
        QTimer::singleShot(CONNECTION_DEADLINE_MS, socket, [this, socket] { finish(socket); });
    }
}

void ClusterIpcServer::read(QLocalSocket *socket)
{
    auto it = mBuffers.find(socket);
    if (it == mBuffers.end())
        return;
    it->append(socket->read(MAX_MESSAGE + 1 - it->size()));
    const int newline = it->indexOf('\n');
    if (newline < 0 && it->size() <= MAX_MESSAGE)
        return;   // wait for the rest; the deadline bounds how long
    const QByteArray reply = newline >= 0 ? handleIpcRequest(it->left(newline + 1), mInterpreter, *mModels)
                                          : QByteArray();
    if (!reply.isEmpty()) {
        socket->write(reply);
        socket->flush();
    }
    finish(socket);
}

void ClusterIpcServer::finish(QLocalSocket *socket)
{
    if (mBuffers.remove(socket) == 0)
        return;
    socket->disconnectFromServer();
    socket->deleteLater();
}
