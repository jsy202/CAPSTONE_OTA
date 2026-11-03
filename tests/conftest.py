def pytest_addoption(parser):
    group = parser.getgroup('physical CAN bench')
    group.addoption('--hardware-config', help='Explicit trusted JSON configuration for actual Pi/CAN tests')
    group.addoption('--hardware-output', help='Artifact directory for the physical run (must be new)')
