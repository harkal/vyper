import pytest

from tests.evm_backends.base_env import ExecutionReverted


def test_call_in_call(get_contract):
    code = """
@internal
def _foo(a: uint256,) -> uint256:
    return 1 + a

@internal
def _foo2() -> uint256:
    return 4

@external
def foo() -> uint256:
    return self._foo(self._foo2())
    """

    c = get_contract(code)
    assert c.foo() == 5

def test_call_in_call_with_raise(get_contract):
    code = """
@internal
def sum(a: uint256) -> uint256:
    if a > 1:
        return a + 1
    raise

@internal
def middle(a: uint256) -> uint256:
    return self.sum(a)

@external
def test(a: uint256) -> uint256:
    return self.middle(a)
    """

    c = get_contract(code)

    assert c.test(2) == 3

    with pytest.raises(ExecutionReverted):
        c.test(0)