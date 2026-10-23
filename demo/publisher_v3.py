"""V3 regression: connection setup now incorrectly owns the Sender lifetime."""


class Sender:
    def __init__(self):
        self.counter = 250

    def next_counter(self):
        current = self.counter
        self.counter = (current + 1) % 256
        return current


class Publisher:
    def __init__(self):
        self.connect()

    def connect(self):
        self.sender = Sender()

    def reconnect(self):
        self.connect()  # Regression: also recreates the otherwise healthy sender.

    def next_counter(self):
        return self.sender.next_counter()
