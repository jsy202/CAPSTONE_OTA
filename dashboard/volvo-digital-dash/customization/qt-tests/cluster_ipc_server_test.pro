# Standalone Qt Test for the Cluster IPC server (QLocalServer on the event loop).
QT += core network testlib
QT -= gui
CONFIG += c++17 console testcase
CONFIG -= app_bundle
TARGET = cluster_ipc_server_test
INCLUDEPATH += ../overlay/app/inc/capstone
HEADERS += ../overlay/app/inc/capstone/cluster_signals.h ../overlay/app/inc/capstone/cluster_ipc_server.h
SOURCES += cluster_ipc_server_test.cpp ../overlay/app/src/capstone/cluster_signals.cpp \
           ../overlay/app/src/capstone/cluster_ipc_server.cpp
