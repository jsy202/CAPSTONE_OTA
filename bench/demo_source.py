"""Normal bench CAN traffic, stopped by acceptance tests before fault injection."""
import time

from capstone_ota.qt_source import frame, send


def main():
    counter = 0
    while True:
        send(frame(650, counter, rpm=2300, gear=4), channel='can0')
        counter = (counter + 1) % 256
        time.sleep(.1)


if __name__ == '__main__':
    main()
