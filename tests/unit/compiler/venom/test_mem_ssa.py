from tests.venom_utils import parse_venom
from vyper.venom.analysis import IRAnalysesCache, MemSSA
from vyper.venom.analysis.mem_ssa import MemoryDef, MemoryLocation, MemoryUse
from vyper.venom.basicblock import EMPTY_MEMORY_ACCESS, FULL_MEMORY_ACCESS, IRLabel


def test_basic_clobber():
    pre = """
    function _global {
        _global:
            %val = 42
            mstore 0, %val
            %2 = mload 0
            stop
    }
    """

    ctx = parse_venom(pre)
    fn = ctx.functions[IRLabel("_global")]

    ac = IRAnalysesCache(fn)

    mem_ssa = MemSSA(ac, fn)
    mem_ssa.analyze()

    mem_use = mem_ssa.memory_uses[fn.entry][0]

    # Test clobber detection
    clobbered = mem_ssa.get_clobbered_memory_access(mem_use)
    assert clobbered is not None
    assert isinstance(clobbered, MemoryDef)
    assert not clobbered.is_live_on_entry
    # Verify it's the store instruction in the entry block
    assert clobbered.loc.offset == 0
    assert clobbered.store_inst.operands[0].value == "%val"
    assert clobbered.store_inst.parent == fn.entry


def test_no_clobber_different_locations():
    pre = """
    function _global {
        _global:
            %val = 1
            mstore 0, %val
            %2 = mload 32
            stop
    }
    """

    ctx = parse_venom(pre)
    fn = ctx.functions[IRLabel("_global")]

    ac = IRAnalysesCache(fn)

    mem_ssa = MemSSA(ac, fn)
    mem_ssa.analyze()

    mem_use = mem_ssa.memory_uses[fn.entry][0]

    clobbered = mem_ssa.get_clobbered_memory_access(mem_use)
    assert clobbered.is_live_on_entry  # Should return live_on_entry since no clobber found


def test_phi_node_clobber():
    pre = """
    function _global {
        entry:
            %cond_val = 10
            mstore 64, %cond_val
            %cond = mload 64
            jnz %cond, @block1, @block2
        block1:
            %val1 = 42
            mstore 0, %val1
            jmp @merge
        block2:
            %val2 = 24
            mstore 0, %val2
            jmp @merge
        merge:
            %4 = mload 0
            stop
    }
    """

    ctx = parse_venom(pre)
    fn = ctx.functions[IRLabel("_global")]

    ac = IRAnalysesCache(fn)

    mem_ssa = MemSSA(ac, fn)
    mem_ssa.analyze()

    merge_block = fn.get_basic_block("merge")
    mem_use = mem_ssa.memory_uses[merge_block][0]

    # Test clobber detection through phi node
    clobbered = mem_ssa.get_clobbered_memory_access(mem_use)
    assert clobbered is not None
    assert isinstance(clobbered, MemoryDef)
    # Verify it's a phi node with both store instructions
    assert clobbered.loc.offset == 0
    block1 = fn.get_basic_block("block1")
    block2 = fn.get_basic_block("block2")
    block1_def = mem_ssa.memory_defs[block1][0]
    block2_def = mem_ssa.memory_defs[block2][0]
    assert block1_def.store_inst.operands[0].value == "%val1"
    assert block2_def.store_inst.operands[0].value == "%val2"


def test_clobbering_with_multiple_stores():
    pre = """
    function _global {
        _global:
            %cond = 1
            %val1 = 42
            %val2 = 24
            jnz %cond, @then, @else
        then:
            mstore 0, %val1
            jmp @merge
        else:
            mstore 0, %val2
            jmp @merge
        merge:
            %val3 = 84
            mstore 0, %val3
            %loaded = mload 0
            stop
    }
    """

    ctx = parse_venom(pre)
    fn = ctx.functions[IRLabel("_global")]

    ac = IRAnalysesCache(fn)
    mem_ssa = MemSSA(ac, fn)
    mem_ssa.analyze()

    # Get the blocks
    then_block = fn.get_basic_block("then")
    else_block = fn.get_basic_block("else")
    merge_block = fn.get_basic_block("merge")

    # Get the MemoryDefs
    def1 = mem_ssa.get_memory_def(then_block.instructions[0])  # mstore 0, %val1
    def2 = mem_ssa.get_memory_def(else_block.instructions[0])  # mstore 0, %val2
    def3 = mem_ssa.get_memory_def(merge_block.instructions[1])  # mstore 0, %val3
    use1 = mem_ssa.get_memory_use(merge_block.instructions[-2])  # mload 0

    # Verify reaching defs
    assert use1.reaching_def == def3, f"Expected def3, got {use1.reaching_def}"

    # Test clobbering
    clobberer1 = mem_ssa.get_clobbering_memory_access(def1)
    assert clobberer1 == def3, f"Expected def3 to clobber def1, got {clobberer1}"
    assert clobberer1.loc.offset == 0
    assert clobberer1.store_inst.operands[0].value == "%val3"

    clobberer2 = mem_ssa.get_clobbering_memory_access(def2)
    assert clobberer2 == def3, f"Expected def3 to clobber def2, got {clobberer2}"
    assert clobberer2.loc.offset == 0
    assert clobberer2.store_inst.operands[0].value == "%val3"

    clobberer3 = mem_ssa.get_clobbering_memory_access(def3)
    assert clobberer3 is None, f"Expected None for def3, got {clobberer3}"


def test_partially_overlapping_clobber():
    pre = """
    function _global {
        _global:
            %1 = param
            mstore 256, 4     ; def: 3 (live_on_entry)
            mstore 288, 1007
            mstore 352, 1007
            mstore 356, %1
            stop
    }
    """
    ctx = parse_venom(pre)
    fn = ctx.functions[IRLabel("_global")]

    ac = IRAnalysesCache(fn)
    mem_ssa = MemSSA(ac, fn)
    mem_ssa.analyze()

    # Get the block and instructions
    bb = fn.get_basic_block("_global")
    store1 = bb.instructions[1]
    store2 = bb.instructions[2]
    store3 = bb.instructions[3]
    store4 = bb.instructions[4]

    # Check definitions
    def1 = mem_ssa.get_memory_def(store1)
    def2 = mem_ssa.get_memory_def(store2)
    def3 = mem_ssa.get_memory_def(store3)
    def4 = mem_ssa.get_memory_def(store4)

    assert def1 is not None, "Should have a memory definition for store1"
    assert def2 is not None, "Should have a memory definition for store2"
    assert def3 is not None, "Should have a memory definition for store3"
    assert def4 is not None, "Should have a memory definition for store4"

    # Test clobbering - store4 (mstore 356) should not clobber store3 (mstore 352)
    clobberer3 = mem_ssa.get_clobbering_memory_access(def3)
    assert clobberer3 is None, f"Expected None for def3, got {clobberer3}"

    # Verify partial overlap detection
    assert mem_ssa.alias.may_alias(
        def3.loc, def4.loc
    ), "Partially overlapping locations should alias"

    # But despite aliasing, they should not clobber each other completely
    assert mem_ssa.get_clobbering_memory_access(def3) is None
    assert mem_ssa.get_clobbering_memory_access(def4) is None


def test_ambiguous_clobber():
    pre = """
    function _global {
    _global:
        %6 = callvalue
        mstore 192, 32
        mstore 64, 5
        calldatacopy %6, 0, 32
        return 192, 32
    }
    """
    ctx = parse_venom(pre)
    fn = ctx.functions[IRLabel("_global")]

    ac = IRAnalysesCache(fn)
    mem_ssa = MemSSA(ac, fn)
    mem_ssa.analyze()

    # Get the block and instructions
    bb = fn.get_basic_block("_global")
    store1 = bb.instructions[1]  # mstore 192, 32
    store2 = bb.instructions[2]  # mstore 64, 5
    calldatacopy = bb.instructions[3]  # calldatacopy %6, 0, 32

    # Check definitions
    def1 = mem_ssa.get_memory_def(store1)
    def2 = mem_ssa.get_memory_def(store2)
    calldatacopy_def = mem_ssa.get_memory_def(calldatacopy)

    assert def1 is not None, "Should have a memory definition for store1"
    assert def2 is not None, "Should have a memory definition for store2"
    assert calldatacopy_def is not None, "Should have a memory definition for calldatacopy"

    # Test clobbering - calldatacopy should clobber both stores
    clobberer1 = mem_ssa.get_clobbering_memory_access(def1)
    assert (
        clobberer1 == calldatacopy_def
    ), f"Expected calldatacopy to clobber store1, got {clobberer1}"

    clobberer2 = mem_ssa.get_clobbering_memory_access(def2)
    assert (
        clobberer2 == calldatacopy_def
    ), f"Expected calldatacopy to clobber store2, got {clobberer2}"

    # Verify calldatacopy returns FULL_MEMORY_ACCESS
    assert (
        calldatacopy_def.loc == FULL_MEMORY_ACCESS
    ), f"Expected FULL_MEMORY_ACCESS for calldatacopy, got {calldatacopy_def.loc}"


def test_complex_loop_clobber():
    pre = """
    function _global {
        entry:
            %init_val = 10
            mstore 0, %init_val          # Initial store
            jmp @loop_header
        loop_header:
            %loop_val = mload 0          # Load that could be clobbered by multiple paths
            %cond1_val = 1
            mstore 96, %cond1_val        # Store first condition
            %cond1 = mload 96            # Load first condition
            jnz %cond1, @path_a, @path_b
        path_a:
            %cond2_val = 1
            mstore 128, %cond2_val       # Store second condition
            %cond2 = mload 128           # Load second condition
            jnz %cond2, @nested_a1, @nested_a2
        nested_a1:
            %val_a1 = 42
            mstore 0, %val_a1            # Potential clobber 1
            jmp @loop_continue
        nested_a2:
            %val_a2 = 24
            mstore 32, %val_a2           # Store to different location
            jmp @loop_continue
        path_b:
            %val_b = 84
            mstore 0, %val_b             # Potential clobber 2
            jmp @loop_continue
        loop_continue:
            %loop_cond_val = 1
            mstore 160, %loop_cond_val   # Store loop condition
            %loop_cond = mload 160       # Load loop condition
            jnz %loop_cond, @loop_header, @exit
        exit:
            %final = mload 0
            stop
    }
    """

    ctx = parse_venom(pre)
    fn = ctx.functions[IRLabel("_global")]

    ac = IRAnalysesCache(fn)
    mem_ssa = MemSSA(ac, fn)
    mem_ssa.analyze()

    # Test the load in loop_header
    loop_header_block = fn.get_basic_block("loop_header")
    loop_header_load = mem_ssa.memory_uses[loop_header_block][0]
    clobbered = mem_ssa.get_clobbered_memory_access(loop_header_load)

    # Should detect clobbering since the load can be affected by stores in nested_a1 and path_b
    assert clobbered is not None
    assert isinstance(clobbered, MemoryDef)
    assert not clobbered.is_live_on_entry

    # Verify the clobbering comes from the correct stores
    nested_a1_block = fn.get_basic_block("nested_a1")
    path_b_block = fn.get_basic_block("path_b")
    nested_a1_def = mem_ssa.memory_defs[nested_a1_block][0]
    path_b_def = mem_ssa.memory_defs[path_b_block][0]

    assert nested_a1_def.loc.offset == 0
    assert nested_a1_def.store_inst.operands[0].value == "%val_a1"
    assert path_b_def.loc.offset == 0
    assert path_b_def.store_inst.operands[0].value == "%val_b"

    # Test the final load in exit block
    exit_block = fn.get_basic_block("exit")
    exit_load = mem_ssa.memory_uses[exit_block][0]
    exit_clobbered = mem_ssa.get_clobbered_memory_access(exit_load)

    # Should also detect clobbering for the final load
    assert exit_clobbered is not None
    assert isinstance(exit_clobbered, MemoryDef)
    assert not exit_clobbered.is_live_on_entry

    # Verify store to different location doesn't affect analysis
    nested_a2_block = fn.get_basic_block("nested_a2")
    different_loc_store = mem_ssa.memory_defs[nested_a2_block][0]
    assert different_loc_store.loc.offset == 32
    assert different_loc_store.store_inst.operands[0].value == "%val_a2"


def test_simple_def_chain():
    code = """
    function _global {
        entry:
            %val1 = 10
            mstore 0, %val1
            %val2 = 20
            mstore 0, %val2
            %val3 = 30
            mstore 0, %val3
            %val4 = mload 0
            stop
    }
    """

    ctx = parse_venom(code)
    fn = ctx.functions[IRLabel("_global")]

    ac = IRAnalysesCache(fn)
    mem_ssa = MemSSA(ac, fn)
    mem_ssa.analyze()

    bb = fn.get_basic_block("entry")
    def_1 = mem_ssa.get_memory_def(bb.instructions[1])
    def_2 = mem_ssa.get_memory_def(bb.instructions[3])
    def_3 = mem_ssa.get_memory_def(bb.instructions[5])
    use_loc0 = mem_ssa.get_memory_use(bb.instructions[-2])

    assert use_loc0 is not None
    assert isinstance(use_loc0, MemoryUse)
    assert use_loc0.loc.offset == 0
    assert use_loc0.reaching_def == def_3
    assert def_3.reaching_def == def_2
    assert def_2.reaching_def == def_1
    assert def_1.reaching_def == mem_ssa.live_on_entry


def test_may_alias():
    pre = """
    function _global {
        _global:
            stop
    }
    """

    # Dummy function to test may_alias
    ctx = parse_venom(pre)
    fn = ctx.functions[IRLabel("_global")]

    ac = IRAnalysesCache(fn)
    mem_ssa = MemSSA(ac, fn)
    mem_ssa.analyze()

    # Test non-overlapping memory locations
    loc1 = MemoryLocation(offset=0, size=32)
    loc2 = MemoryLocation(offset=32, size=32)
    assert not mem_ssa.alias.may_alias(loc1, loc2), "Non-overlapping locations should not alias"

    # Test overlapping memory locations
    loc3 = MemoryLocation(offset=0, size=16)
    loc4 = MemoryLocation(offset=8, size=8)
    assert mem_ssa.alias.may_alias(loc3, loc4), "Overlapping locations should alias"

    # Test FULL_MEMORY_ACCESS
    full_loc = FULL_MEMORY_ACCESS
    assert mem_ssa.alias.may_alias(
        full_loc, loc1
    ), "FULL_MEMORY_ACCESS should alias with any non-empty location"
    assert not mem_ssa.alias.may_alias(
        full_loc, EMPTY_MEMORY_ACCESS
    ), "FULL_MEMORY_ACCESS should not alias with EMPTY_MEMORY_ACCESS"

    # Test EMPTY_MEMORY_ACCESS
    empty_loc = EMPTY_MEMORY_ACCESS
    assert not mem_ssa.alias.may_alias(
        empty_loc, loc1
    ), "EMPTY_MEMORY_ACCESS should not alias with any location"
    assert not mem_ssa.alias.may_alias(
        empty_loc, full_loc
    ), "EMPTY_MEMORY_ACCESS should not alias with FULL_MEMORY_ACCESS"

    # Test zero/negative size locations
    zero_size_loc = MemoryLocation(offset=0, size=0)
    assert not mem_ssa.alias.may_alias(zero_size_loc, loc1), "Zero size location should not alias"
    assert not mem_ssa.alias.may_alias(
        zero_size_loc, zero_size_loc
    ), "Zero size locations should not alias with each other"

    # Test partial overlap
    loc5 = MemoryLocation(offset=0, size=64)
    loc6 = MemoryLocation(offset=32, size=32)
    assert mem_ssa.alias.may_alias(loc5, loc6), "Partially overlapping locations should alias"
    assert mem_ssa.alias.may_alias(loc6, loc5), "Partially overlapping locations should alias"

    # Test exact same location
    loc7 = MemoryLocation(offset=0, size=64)
    loc8 = MemoryLocation(offset=0, size=64)
    assert mem_ssa.alias.may_alias(loc7, loc8), "Identical locations should alias"

    # Test adjacent but non-overlapping locations
    loc9 = MemoryLocation(offset=0, size=64)
    loc10 = MemoryLocation(offset=64, size=64)
    assert not mem_ssa.alias.may_alias(
        loc9, loc10
    ), "Adjacent but non-overlapping locations should not alias"
    assert not mem_ssa.alias.may_alias(
        loc10, loc9
    ), "Adjacent but non-overlapping locations should not alias"


def test_basic_def_use_assignment():
    pre = """
    function _global {
        _global:
            %1 = param
            mstore 0, 1
            mstore 32, 2
            %2 = mload 0
            stop
    }
    """

    ctx = parse_venom(pre)
    fn = ctx.functions[IRLabel("_global")]

    ac = IRAnalysesCache(fn)
    mem_ssa = MemSSA(ac, fn)
    mem_ssa.analyze()

    # Get the block and instructions
    bb = fn.get_basic_block("_global")
    store1 = bb.instructions[1]  # mstore 0, 1
    store2 = bb.instructions[2]  # mstore 32, 2
    load = bb.instructions[3]  # %2 = mload 0

    # Check definitions
    def1 = mem_ssa.get_memory_def(store1)
    def2 = mem_ssa.get_memory_def(store2)
    assert def1 is not None
    assert def2 is not None
    assert def1.id == 1
    assert def2.id == 2
    assert def1.loc.offset == 0
    assert def2.loc.offset == 32

    # Check use
    use = mem_ssa.get_memory_use(load)
    assert use is not None
    assert use.reaching_def == def2
    assert use.loc.offset == 0

    # Verify the def chain
    assert def1.reaching_def == mem_ssa.live_on_entry
    assert def2.reaching_def == def1


def test_read_write_memory_clobbering():
    pre = """
    function _global {
        entry:
            mstore 0, 42        # Store value at 0
            mstore 32, 100         # Store value at 32
            %ret = call 0, 0x1234, 0, 0, 32, 32, 32
            %loaded = mload 0       # Load from 0
            %loaded2 = mload 32     # Load from 32
            stop
    }
    """
    ctx = parse_venom(pre)
    fn = ctx.functions[IRLabel("_global")]

    ac = IRAnalysesCache(fn)
    mem_ssa = MemSSA(ac, fn)
    mem_ssa.analyze()

    # Get the block and instructions
    bb = fn.get_basic_block("entry")
    store1 = bb.instructions[0]  # mstore 0, %value
    store2 = bb.instructions[1]  # mstore 32, 100
    call_inst = bb.instructions[2]  # call instruction
    load1 = bb.instructions[3]  # mload 0
    load2 = bb.instructions[4]  # mload 32

    # Check definitions
    def1 = mem_ssa.get_memory_def(store1)
    def2 = mem_ssa.get_memory_def(store2)
    call_def = mem_ssa.get_memory_def(call_inst)
    call_use = mem_ssa.get_memory_use(call_inst)
    assert def1 is not None
    assert def2 is not None
    assert call_def is not None
    assert call_use is not None

    # Verify call instruction has both read and write memory areas
    assert call_def.loc.offset == 32  # Write area
    assert call_def.loc.size == 32  # Write size
    assert call_use.loc.offset == 0  # Read area
    assert call_use.loc.size == 32  # Read size

    use1 = mem_ssa.get_memory_use(load1)
    use2 = mem_ssa.get_memory_use(load2)
    assert use1.reaching_def == call_def
    assert use2.reaching_def == call_def

    clobberer1 = mem_ssa.get_clobbering_memory_access(def1)
    clobberer2 = mem_ssa.get_clobbering_memory_access(def2)
    assert clobberer1 is None
    assert clobberer2 == call_def


def test_read_write_memory_clobbering_partial():
    pre = """
    function _global {
        entry:
            mstore 0, 42        # Store value at 0
            mstore 32, 100         # Store value at 32
            %ret = call 0, 0x1234, 0, 31, 2, 0, 32
            %loaded = mload 0       # Load from 0
            %loaded2 = mload 32     # Load from 32
            stop
    }
    """
    ctx = parse_venom(pre)
    fn = ctx.functions[IRLabel("_global")]

    ac = IRAnalysesCache(fn)
    mem_ssa = MemSSA(ac, fn)
    mem_ssa.analyze()

    # Get the block and instructions
    bb = fn.get_basic_block("entry")
    store1 = bb.instructions[0]  # mstore 0, %value
    store2 = bb.instructions[1]  # mstore 32, 100
    call_inst = bb.instructions[2]  # call instruction
    load1 = bb.instructions[3]  # mload 0
    load2 = bb.instructions[4]  # mload 32

    # Check definitions
    def1 = mem_ssa.get_memory_def(store1)
    def2 = mem_ssa.get_memory_def(store2)
    call_def = mem_ssa.get_memory_def(call_inst)
    call_use = mem_ssa.get_memory_use(call_inst)
    assert def1 is not None
    assert def2 is not None
    assert call_def is not None
    assert call_use is not None

    # Verify call instruction has both read and write memory areas
    # Write area
    assert call_def.loc.offset == 0
    assert call_def.loc.size == 32
    # Read area
    assert call_use.loc.offset == 31
    assert call_use.loc.size == 2

    clobberer1 = mem_ssa.get_clobbering_memory_access(def1)
    assert clobberer1 is None

    clobberer2 = mem_ssa.get_clobbering_memory_access(def2)
    assert clobberer2 is None

    use1 = mem_ssa.get_memory_use(load1)
    use2 = mem_ssa.get_memory_use(load2)
    assert use1.reaching_def == call_def
    assert use2.reaching_def == call_def
