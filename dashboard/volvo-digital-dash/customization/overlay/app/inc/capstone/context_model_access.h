#ifndef CAPSTONE_CONTEXT_MODEL_ACCESS_H
#define CAPSTONE_CONTEXT_MODEL_ACCESS_H

#include <QQmlContext>
#include <QVariant>

#include "cluster_signals.h"

/** Resolves the same context-property model objects the QML scene binds to. */
class ContextModelAccess : public ModelAccess {
public:
    explicit ContextModelAccess(QQmlContext *context) : mContext(context) {}
    QObject *model(const char *name) const override {
        return qvariant_cast<QObject *>(mContext->contextProperty(QString::fromLatin1(name)));
    }

private:
    QQmlContext *mContext;
};

#endif // CAPSTONE_CONTEXT_MODEL_ACCESS_H
