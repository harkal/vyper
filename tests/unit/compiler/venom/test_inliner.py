
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
