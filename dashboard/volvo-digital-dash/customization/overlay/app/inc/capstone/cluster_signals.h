#ifndef CAPSTONE_CLUSTER_SIGNALS_H
#define CAPSTONE_CLUSTER_SIGNALS_H

#include <QByteArray>
#include <QObject>
#include <QString>

/**
 * @brief Vehicle signals in CAN wire units (ZONAL_OTA_GUIDE §5): speed in
 * 0.1 km/h, rpm, gear (P=0 R=1 N=2 D=3) and an eight-bit warning mask.
 */
struct WireSignals {
    int speed = 0;
    int rpm = 0;
    int gear = 0;
    int warnings = 0;
};

/** Resolves the dashboard models the QML scene binds to, by context name. */
class ModelAccess {
public:
    virtual ~ModelAccess() = default;
    virtual QObject *model(const char *name) const = 0;
};

/**
 * @brief Interprets wire signals into the real dashboard models and reads the
 * interpreted state back from those models.
 *
 * apply() is the single interpretation path used for both the `vehicle` and
 * `functional` IPC operations; observe() derives the reported values only
 * from model state, never from the request. A DEMO/TEST FAULT INJECTION
 * build (CAPSTONE_FAULT_SPEED_DIVISOR) divides the interpreted speed here,
 * so the displayed speedometer and the observation are wrong together.
 */
class ClusterSignalInterpreter {
public:
    static int faultDivisor();
    static const char *const *lampModelNames();   // eight names, warning bit 0..7

    bool apply(const WireSignals &input, const ModelAccess &models, QString *error);
    bool observe(const ModelAccess &models, WireSignals *out, QString *error) const;

private:
    int mGear = 0;   // upstream has no gear model; the interpreted gear is kept here
};

/**
 * @brief Handle one ApplicationIpc v1 request line (`vehicle` / `functional`).
 * @return the response line, or an empty array to close without a response.
 */
QByteArray handleIpcRequest(const QByteArray &raw, ClusterSignalInterpreter &interpreter,
                            const ModelAccess &models);

#endif // CAPSTONE_CLUSTER_SIGNALS_H
