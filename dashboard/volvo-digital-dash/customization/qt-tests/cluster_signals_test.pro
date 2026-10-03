# Standalone Qt Test for the Cluster signal interpreter and IPC protocol.
# Build with "qmake CAPSTONE_FAULT_SPEED_DIVISOR=10" for the fault variant.
QT += core testlib
QT -= gui
CONFIG += c++17 console testcase
CONFIG -= app_bundle
TARGET = cluster_signals_test
!isEmpty(CAPSTONE_FAULT_SPEED_DIVISOR) {
    DEFINES += CAPSTONE_FAULT_SPEED_DIVISOR=$$CAPSTONE_FAULT_SPEED_DIVISOR
    TARGET = cluster_signals_fault_test
}
INCLUDEPATH += ../overlay/app/inc/capstone
HEADERS += ../overlay/app/inc/capstone/cluster_signals.h
SOURCES += cluster_signals_test.cpp ../overlay/app/src/capstone/cluster_signals.cpp
