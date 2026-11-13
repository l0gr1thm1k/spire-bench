"""The library must install and import on a machine with no game installed."""


def test_the_package_imports_without_a_game():
    import spirebench
    import spirebench.client
    import spirebench.gateway
    import spirebench.worker

    assert spirebench.__version__
