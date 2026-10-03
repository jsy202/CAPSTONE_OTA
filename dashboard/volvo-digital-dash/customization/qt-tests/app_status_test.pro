# Standalone Qt Test for the overlay AppStatus model (not part of upstream).
QT += core testlib
QT -= gui
CONFIG += c++17 console testcase
CONFIG -= app_bundle
TARGET = app_status_test
INCLUDEPATH += ../overlay/app/inc/capstone
HEADERS += ../overlay/app/inc/capstone/app_status.h
SOURCES += app_status_test.cpp ../overlay/app/src/capstone/app_status.cpp
