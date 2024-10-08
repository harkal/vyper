def test_bytes_logging_extended(get_contract, get_logs):
    code = """
event MyLog:
    arg1: int128
    arg2: Bytes[64]
    arg3: int128

@external
def foo():
    log MyLog(arg1=667788, arg2=b'hellohellohellohellohellohellohellohellohello', arg3=334455)
    """

    c = get_contract(code)
    c.foo()
    (log,) = get_logs(c, "MyLog")

    assert log.args.arg1 == 667788
    assert log.args.arg2 == b"hello" * 9
    assert log.args.arg3 == 334455


def test_bytes_logging_extended_variables(get_contract, get_logs):
    code = """
event MyLog:
    arg1: Bytes[64]
    arg2: Bytes[64]
    arg3: Bytes[64]

@external
def foo():
    a: Bytes[64] = b'hellohellohellohellohellohellohellohellohello'
    b: Bytes[64] = b'hellohellohellohellohellohellohellohello'
    # test literal much smaller than buffer
    log MyLog(arg1=a, arg2=b, arg3=b'hello')
    """

    c = get_contract(code)
    c.foo()
    (log,) = get_logs(c, "MyLog")
    assert log.args.arg1 == b"hello" * 9
    assert log.args.arg2 == b"hello" * 8
    assert log.args.arg3 == b"hello" * 1


def test_bytes_logging_extended_passthrough(get_contract, get_logs):
    code = """
event MyLog:
    arg1: int128
    arg2: Bytes[64]
    arg3: int128

@external
def foo(a: int128, b: Bytes[64], c: int128):
    log MyLog(arg1=a, arg2=b, arg3=c)
    """

    c = get_contract(code)

    c.foo(333, b"flower" * 8, 444)
    log = get_logs(c, "MyLog")

    assert log[0].args.arg1 == 333
    assert log[0].args.arg2 == b"flower" * 8
    assert log[0].args.arg3 == 444


def test_bytes_logging_extended_storage(get_contract, get_logs):
    code = """
event MyLog:
    arg1: int128
    arg2: Bytes[64]
    arg3: int128

a: int128
b: Bytes[64]
c: int128

@external
def foo():
    log MyLog(arg1=self.a, arg2=self.b, arg3=self.c)

@external
def set(x: int128, y: Bytes[64], z: int128):
    self.a = x
    self.b = y
    self.c = z
    """

    c = get_contract(code)
    c.foo()
    log = get_logs(c, "MyLog")

    assert log[0].args.arg1 == 0
    assert log[0].args.arg2 == b""
    assert log[0].args.arg3 == 0

    c.set(333, b"flower" * 8, 444)
    c.foo()

    (log,) = get_logs(c, "MyLog")
    assert log.args.arg1 == 333
    assert log.args.arg2 == b"flower" * 8
    assert log.args.arg3 == 444


def test_bytes_logging_extended_mixed_with_lists(get_contract, get_logs):
    code = """
event MyLog:
    arg1: int128[2][2]
    arg2: Bytes[64]
    arg3: int128
    arg4: Bytes[64]

@external
def foo():
    log MyLog(
        arg1=[[24, 26], [12, 10]],
        arg2=b'hellohellohellohellohellohellohellohellohello',
        arg3=314159,
        arg4=b'helphelphelphelphelphelphelphelphelphelphelp'
    )
    """

    c = get_contract(code)
    c.foo()
    (log,) = get_logs(c, "MyLog")

    assert log.args.arg1 == [[24, 26], [12, 10]]
    assert log.args.arg2 == b"hello" * 9
    assert log.args.arg3 == 314159
    assert log.args.arg4 == b"help" * 11
