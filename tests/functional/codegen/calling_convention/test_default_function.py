from eth_utils import to_wei


def test_throw_on_sending(env, tx_failed, get_contract):
    code = """
x: public(int128)

@deploy
def __init__():
    self.x = 123
    """
    c = get_contract(code)

    assert c.x() == 123
    assert env.get_balance(c.address) == 0
    value = to_wei(0.1, "ether")
    env.set_balance(env.deployer, value)
    with tx_failed():
        env.message_call(c.address, value=value, data=b"")  # call default function
    assert env.get_balance(c.address) == 0


def test_basic_default(env, get_logs, get_contract):
    code = """
event Sent:
    sender: indexed(address)

@external
@payable
def __default__():
    log Sent(sender=msg.sender)
    """
    c = get_contract(code)
    env.set_balance(env.deployer, 10**18)
    env.message_call(c.address, value=10**17, data=b"")  # call default function
    (log,) = get_logs(c, "Sent")
    assert env.deployer == log.args.sender
    assert env.get_balance(c.address) == to_wei(0.1, "ether")


def test_basic_default_default_param_function(env, get_logs, get_contract):
    code = """
event Sent:
    sender: indexed(address)

@external
@payable
def fooBar(a: int128 = 12345) -> int128:
    log Sent(sender=empty(address))
    return a

@external
@payable
def __default__():
    log Sent(sender=msg.sender)
    """
    c = get_contract(code)
    env.set_balance(env.deployer, 10**18)
    env.message_call(c.address, value=10**17, data=b"")  # call default function
    (log,) = get_logs(c, "Sent")
    assert env.deployer == log.args.sender
    assert env.get_balance(c.address) == to_wei(0.1, "ether")


def test_basic_default_not_payable(env, tx_failed, get_contract):
    code = """
event Sent:
    sender: indexed(address)

@external
def __default__():
    log Sent(sender=msg.sender)
    """
    c = get_contract(code)
    env.set_balance(env.deployer, 10**17)

    with tx_failed():
        env.message_call(c.address, value=10**17, data=b"")  # call default function


def test_multi_arg_default(assert_compile_failed, get_contract):
    code = """
@payable
@external
def __default__(arg1: int128):
    pass
    """
    assert_compile_failed(lambda: get_contract(code))


def test_always_public(assert_compile_failed, get_contract):
    code = """
@internal
def __default__():
    pass
    """
    assert_compile_failed(lambda: get_contract(code))


def test_always_public_2(assert_compile_failed, get_contract):
    code = """
event Sent:
    sender: indexed(address)

def __default__():
    log Sent(sender=msg.sender)
    """
    assert_compile_failed(lambda: get_contract(code))


def test_zero_method_id(env, get_logs, get_contract, tx_failed):
    # test a method with 0x00000000 selector,
    # expects at least 36 bytes of calldata.
    code = """
event Sent:
    sig: uint256

@external
@payable
# function selector: 0x00000000
def blockHashAskewLimitary(v: uint256) -> uint256:
    log Sent(sig=2)
    return 7

@external
def __default__():
    log Sent(sig=1)
    """
    c = get_contract(code)

    assert c.blockHashAskewLimitary(0) == 7

    def _call_with_bytes(hexstr):
        # call our special contract and return the logged value
        data = bytes.fromhex(hexstr.removeprefix("0x"))
        env.message_call(c.address, value=0, data=data)
        (log,) = get_logs(c, "Sent")
        return log.args.sig

    assert 1 == _call_with_bytes("0x")

    # call blockHashAskewLimitary with proper calldata
    assert 2 == _call_with_bytes("0x" + "00" * 36)

    # call blockHashAskewLimitary with extra trailing bytes in calldata
    assert 2 == _call_with_bytes("0x" + "00" * 37)

    for i in range(4):
        # less than 4 bytes of calldata doesn't match the 0 selector and goes to default
        assert 1 == _call_with_bytes("0x" + "00" * i)

    for i in range(4, 36):
        # match the full 4 selector bytes, but revert due to malformed (short) calldata
        with tx_failed():
            _call_with_bytes(f"0x{'00' * i}")


def test_another_zero_method_id(env, get_logs, get_contract, tx_failed):
    # test another zero method id but which only expects 4 bytes of calldata
    code = """
event Sent:
    sig: uint256

@external
@payable
# function selector: 0x00000000
def wycpnbqcyf() -> uint256:
    log Sent(sig=2)
    return 7

@external
def __default__():
    log Sent(sig=1)
    """
    c = get_contract(code)

    assert c.wycpnbqcyf() == 7

    def _call_with_bytes(hexstr):
        # call our special contract and return the logged value
        data = bytes.fromhex(hexstr.removeprefix("0x"))
        env.message_call(c.address, value=0, data=data, gas=10**6)
        (log,) = get_logs(c, "Sent")
        return log.args.sig

    assert 1 == _call_with_bytes("0x")

    # call wycpnbqcyf
    assert 2 == _call_with_bytes("0x" + "00" * 4)

    # too many bytes ok
    assert 2 == _call_with_bytes("0x" + "00" * 5)

    # "right" method id but by accident - not enough bytes.
    for i in range(4):
        assert 1 == _call_with_bytes("0x" + "00" * i)


def test_partial_selector_match_trailing_zeroes(env, get_logs, get_contract):
    code = """
event Sent:
    sig: uint256

@external
@payable
# function selector: 0xd88e0b00
def fow() -> uint256:
    log Sent(sig=2)
    return 7

@external
def __default__():
    log Sent(sig=1)
    """
    c = get_contract(code)

    # sanity check - we can call c.fow()
    assert c.fow() == 7

    def _call_with_bytes(hexstr):
        # call our special contract and return the logged value
        data = bytes.fromhex(hexstr.removeprefix("0x"))
        env.message_call(c.address, value=0, data=data)
        (log,) = get_logs(c, "Sent")
        return log.args.sig

    # check we can call default function
    assert 1 == _call_with_bytes("0x")

    # check fow() selector is 0xd88e0b00
    assert 2 == _call_with_bytes("0xd88e0b00")

    # check calling d88e0b with no trailing zero goes to fallback instead of reverting
    assert 1 == _call_with_bytes("0xd88e0b")
