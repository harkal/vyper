def test_module_event(get_contract, make_input_bundle, get_logs):
    # log from a module
    lib1 = """
event MyEvent:
    pass

@internal
def foo():
    log MyEvent()
    """
    main = """
import lib1

@external
def bar():
    lib1.foo()
    """
    input_bundle = make_input_bundle({"lib1.vy": lib1})
    c = get_contract(main, input_bundle=input_bundle)
    c.bar()
    logs = get_logs(c, "MyEvent")
    assert len(logs) == 1


def test_module_event2(get_contract, make_input_bundle, get_logs):
    # log a module event from main contract
    lib1 = """
event MyEvent:
    x: uint256
    """
    main = """
import lib1

@external
def bar():
    log lib1.MyEvent(5)
    """
    input_bundle = make_input_bundle({"lib1.vy": lib1})
    c = get_contract(main, input_bundle=input_bundle)
    c.bar()
    (log,) = get_logs(c, "MyEvent")
    assert log.args.x == 5


def test_module_event_indexed(get_contract, make_input_bundle, get_logs):
    lib1 = """
event MyEvent:
    x: uint256
    y: indexed(uint256)

@internal
def foo():
    log MyEvent(x=5, y=6)
    """
    main = """
import lib1

@external
def bar():
    lib1.foo()
    """
    input_bundle = make_input_bundle({"lib1.vy": lib1})
    c = get_contract(main, input_bundle=input_bundle)
    c.bar()
    (log,) = get_logs(c, "MyEvent")
    assert log.args.x == 5
    assert log.args.y == 6
