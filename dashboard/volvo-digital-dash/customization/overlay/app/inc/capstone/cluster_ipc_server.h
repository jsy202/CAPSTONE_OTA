#ifndef CAPSTONE_CLUSTER_IPC_SERVER_H
#define CAPSTONE_CLUSTER_IPC_SERVER_H

#include <QHash>
#include <QLocalServer>
#include <QObject>
#include <memory>

#include "cluster_signals.h"

class QLocalSocket;

/**
 * @brief ApplicationIpc v1 server for the zone agent (ZONAL_OTA_GUIDE §5):
 * `vehicle` and `functional` over /run/digital-cluster/ota.sock.
 *
 * Runs on the GUI event loop and is fully event-driven: no blocking accept or
 * read, a 4096-byte cap, one request per connection, and a 2 s deadline per
 * connection, so a slow or malicious peer cannot stall rendering. Requests
 * are applied to and read back from the dashboard models in one event-loop
 * turn, so a functional observation cannot interleave with a vehicle sample.
 */
class ClusterIpcServer : public QObject {
    Q_OBJECT
public:
    explicit ClusterIpcServer(ModelAccess *models, QObject *parent = nullptr);  // takes ownership
    ~ClusterIpcServer() override;

    static QString defaultPath();   // CAPSTONE_CLUSTER_IPC_SOCKET or /run/digital-cluster/ota.sock

    /** Listen at path (0660). Only a stale socket at path is replaced. */
    bool listen(const QString &path, QString *error);
    /** listen(defaultPath()); on failure log a warning and keep the dashboard running. */
    void listenOrWarn();

private slots:
    void accept();

private:
    void read(QLocalSocket *socket);
    void finish(QLocalSocket *socket);

    std::unique_ptr<ModelAccess> mModels;
    ClusterSignalInterpreter mInterpreter;
    QLocalServer mServer;
    QHash<QLocalSocket *, QByteArray> mBuffers;
};

#endif // CAPSTONE_CLUSTER_IPC_SERVER_H
