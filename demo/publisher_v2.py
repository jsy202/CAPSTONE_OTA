"""V2: the Sender belongs to the running application, not its input connection."""


class Sender:
    def __init__(self):
        self.counter = 250

    def next_counter(self):
        current = self.counter
        self.counter = (current + 1) % 256
        return current


class Publisher:
    def __init__(self):
        self.sender = Sender()

    def reconnect(self):
        # Restore the input connection without replacing the CAN stream state.
        pass

    def next_counter(self):
        return self.sender.next_counter()
